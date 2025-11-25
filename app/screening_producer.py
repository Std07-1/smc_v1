"""Stage1→State публішер (формування агрегованого стану активів).

Шлях: ``app/screening_producer.py``

Призначення:
    • періодичний збір даних через UnifiedDataStore і Stage1 монітор;
    • нормалізація та уніфікація сигналів (confidence / tp/sl / triggers);
    • публікація повного snapshot у Redis (канал і ключ) для UI.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from redis.asyncio import Redis
from rich.console import Console
from rich.logging import RichHandler

from config.config import (
    DEFAULT_LOOKBACK,
    DEFAULT_TIMEFRAME,
    MIN_READY_PCT,
    SCREENING_BATCH_SIZE,
    SMC_PIPELINE_CFG,
    SMC_PIPELINE_ENABLED,
    TRADE_REFRESH_INTERVAL,
    WS_GAP_STATUS_PATH,
)
from config.constants import (
    ASSET_STATE,
    K_SIGNAL,
    K_STATS,
)
from stage1.asset_monitoring import AssetMonitorStage1
from UI.publish_full_state import publish_full_state
from utils.utils import (
    create_error_signal,
    create_no_data_signal,
    normalize_result_types,
)

from .asset_state_manager import AssetStateManager

if TYPE_CHECKING:  # pragma: no cover - only for type hints
    from data.unified_store import UnifiedDataStore

# ───────────────────────────── Логування ─────────────────────────────
logger = logging.getLogger("app.screening_producer")
if not logger.handlers:
    logger.setLevel(logging.DEBUG)
    logger.addHandler(RichHandler(console=Console(stderr=True), show_path=False))
    logger.propagate = False


if TYPE_CHECKING:  # pragma: no cover - лише для тайпінгів
    from smc_core.engine import SmcCoreEngine
    from smc_core.smc_types import SmcHint


_SMC_ENGINE: SmcCoreEngine | None = None
_SMC_TO_PLAIN: Callable[[Any], dict[str, Any] | None] | None = None


async def _get_smc_engine() -> SmcCoreEngine | None:
    """Ліниво створює SmcCoreEngine при першому зверненні."""

    global _SMC_ENGINE
    if not SMC_PIPELINE_ENABLED:
        return None
    if _SMC_ENGINE is not None:
        return _SMC_ENGINE

    try:
        module_engine = importlib.import_module("smc_core.engine")
        engine_cls = module_engine.SmcCoreEngine
    except Exception as exc:  # pragma: no cover - best-effort
        logger.warning("[SMC] Не вдалося імпортувати SmcCoreEngine: %s", exc)
        return None

    _SMC_ENGINE = engine_cls()
    logger.info("[SMC] SmcCoreEngine ініціалізовано для пайплайна")
    return _SMC_ENGINE


def _get_smc_plain_serializer() -> Callable[[Any], dict[str, Any] | None] | None:
    """Повертає to_plain_smc_hint із core без глобального імпорту під час старту."""

    global _SMC_TO_PLAIN
    if not SMC_PIPELINE_ENABLED:
        return None
    if _SMC_TO_PLAIN is not None:
        return _SMC_TO_PLAIN

    try:
        module_serializers = importlib.import_module("smc_core.serializers")
        _SMC_TO_PLAIN = module_serializers.to_plain_smc_hint
        return _SMC_TO_PLAIN
    except Exception as exc:  # pragma: no cover
        logger.warning("[SMC] Не вдалося імпортувати to_plain_smc_hint: %s", exc)
        return None


async def _build_smc_hint(
    symbol: str,
    store: UnifiedDataStore,
) -> SmcHint | None:
    """Формує SmcHint для символу, не впливаючи на Stage1 при помилках."""

    if not SMC_PIPELINE_ENABLED:
        return None

    try:
        tf_primary = str(SMC_PIPELINE_CFG.get("tf_primary", "1m"))
        tfs_extra_cfg = SMC_PIPELINE_CFG.get("tfs_extra", ("5m", "15m", "1h"))
        tfs_extra = tuple(tfs_extra_cfg)
        limit = int(SMC_PIPELINE_CFG.get("limit", 300))
    except Exception as exc:
        logger.debug("[SMC] Некоректний SMC_PIPELINE_CFG: %s", exc)
        return None

    try:
        module_adapter = importlib.import_module("smc_core.input_adapter")
        build_smc_input_from_store = module_adapter.build_smc_input_from_store
    except Exception as exc:  # pragma: no cover - best-effort
        logger.warning("[SMC] Не вдалося імпортувати input_adapter: %s", exc)
        return None

    engine = await _get_smc_engine()
    if engine is None:
        return None

    t0 = time.perf_counter()
    try:
        smc_input = await build_smc_input_from_store(
            store=store,
            symbol=symbol,
            tf_primary=tf_primary,
            tfs_extra=tfs_extra,
            limit=limit,
        )
        hint = engine.process_snapshot(smc_input)
    except Exception as exc:
        logger.debug("[SMC] Помилка під час побудови SMC hint для %s: %s", symbol, exc)
        return None

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    if SMC_PIPELINE_CFG.get("log_latency", False):
        liq = getattr(hint, "liquidity", None)
        meta = getattr(liq, "meta", {}) if liq is not None else {}
        pool_count = meta.get("pool_count")
        magnet_count = meta.get("magnet_count")
        amd_phase = getattr(liq, "amd_phase", None)
        amd_phase_name = getattr(amd_phase, "name", None) or "UNKNOWN"
        logger.debug(
            "[SMC] symbol=%s tf=%s latency_ms=%.2f pools=%s magnets=%s amd_phase=%s",
            symbol,
            getattr(smc_input, "tf_primary", tf_primary),
            elapsed_ms,
            pool_count,
            magnet_count,
            amd_phase_name,
        )

    return hint


async def process_asset_batch(
    symbols: list[str],
    monitor: AssetMonitorStage1,
    store: UnifiedDataStore,
    timeframe: str,
    lookback: int,
    state_manager: AssetStateManager,
) -> None:
    """
    Обробляє батч символів через UnifiedDataStore, синхронізує статуси та оновлює сигнали моніторингу.
    Args:
        symbols (list[str]): Символи активів для обробки.
        monitor (AssetMonitorStage1): Модуль первинного моніторингу та виявлення аномалій.
        store (UnifiedDataStore): Джерело маркет-даних і службових статусів.
        timeframe (str): Таймфрейм свічок для вибірки історії.
        lookback (int): Кількість свічок, що завантажуються для аналізу.
        state_manager (AssetStateManager): Менеджер стану активів для оновлення результатів перевірки.
    Raises:
        Exception: У разі помилок під час отримання даних чи обчислення сигналів,
        що логуються та відображаються як помилки активу.
    Очікується: store.get_df(symbol, interval, limit=lookback) -> DataFrame з open_time.
    """
    resync_payload = await store.redis.jget(*WS_GAP_STATUS_PATH, default={})
    resync_meta: dict[str, dict[str, Any]] = {}
    if isinstance(resync_payload, dict):
        for sym_key, meta in resync_payload.items():
            try:
                if not isinstance(meta, dict):
                    continue
                if str(meta.get("status", "")).lower() != "syncing":
                    continue
                resync_meta[sym_key.lower()] = meta
            except Exception:
                continue

    for symbol in symbols:
        try:
            lower_symbol = symbol.lower()
            sync_meta = resync_meta.get(lower_symbol)
            if sync_meta:
                missing = (
                    int(sync_meta.get("missing", 0))
                    if sync_meta.get("missing")
                    else None
                )
                hint = (
                    f"WS ресинхронізація ({missing} хв)"
                    if missing
                    else "WS ресинхронізація триває"
                )
                stats_update = {}
                start_ot = sync_meta.get("start_open_time")
                end_ot = sync_meta.get("end_open_time")
                if start_ot is not None:
                    stats_update["gap_start_open_time"] = start_ot
                if end_ot is not None:
                    stats_update["gap_end_open_time"] = end_ot
                if missing is not None:
                    stats_update["gap_missing_bars"] = missing

                existing = state_manager.state.get(symbol, {})
                existing_stats = (
                    existing.get(K_STATS, {}) if isinstance(existing, dict) else {}
                )
                merged_stats = (
                    {**existing_stats, **stats_update}
                    if isinstance(existing_stats, dict)
                    else stats_update
                )

                state_manager.update_asset(
                    symbol,
                    {
                        K_SIGNAL: "SYNCING",
                        "state": ASSET_STATE["SYNCING"],
                        "hints": [hint],
                        K_STATS: merged_stats,
                    },
                )
                continue

            # Якщо дані є і їх достатньо, додаємо до ready_assets
            df = await store.get_df(symbol, timeframe, limit=lookback)
            if df is None or df.empty or len(df) < 5:
                state_manager.update_asset(symbol, create_no_data_signal(symbol))
                continue
            if "open_time" in df.columns and "timestamp" not in df.columns:
                df = df.rename(columns={"open_time": "timestamp"})
            # ── Базові метрики оновлюємо КОЖЕН цикл (щоб UI не «застирав») ──
            try:
                current_price = (
                    float(df["close"].iloc[-1]) if "close" in df.columns else None
                )
            except Exception:
                current_price = None
            try:
                volume_last = (
                    float(df["volume"].iloc[-1]) if "volume" in df.columns else None
                )
            except Exception:
                volume_last = None
            last_ts_val = None
            if "timestamp" in df.columns:
                try:
                    last_ts_val = df["timestamp"].iloc[-1]
                except Exception:
                    last_ts_val = None

            signal = await monitor.check_anomalies(symbol, df)
            if not isinstance(signal, dict):  # захист від невалідного повернення
                signal = {"symbol": symbol.lower(), "signal": "NONE", "stats": {}}

            # Гарантуємо наявність контейнера stats
            stats_container = signal.get("stats")
            if not isinstance(stats_container, dict):
                stats_container = {}
                signal["stats"] = stats_container

            # ВАЖЛИВО: ці базові метрики ОНОВЛЮЄМО КОЖЕН ЦИКЛ (інакше UI «зависає» на першому значенні)
            # Раніше тут було set-if-missing, що призводило до застиглих price/volume/ts → повертаємо always-overwrite.
            if current_price is not None:
                stats_container["current_price"] = current_price
            if volume_last is not None:
                stats_container["volume"] = volume_last
            if last_ts_val is not None:
                stats_container["timestamp"] = last_ts_val

            # Нормалізуємо типи (існуючі метрики збережуться)
            normalized = normalize_result_types(signal)
            # Переконуємось, що нормалізація не втратила базові stats
            try:
                norm_stats = normalized.get("stats")
                if not isinstance(norm_stats, dict):
                    normalized["stats"] = stats_container
                else:
                    for k, v in stats_container.items():
                        norm_stats.setdefault(k, v)
            except Exception:
                normalized["stats"] = stats_container

            # Додаємо SMC hint, якщо можливо
            smc_hint = None
            if SMC_PIPELINE_ENABLED:
                try:
                    smc_hint = await _build_smc_hint(symbol=symbol, store=store)
                except Exception as exc:  # pragma: no cover - захист від edge-case
                    logger.debug(
                        "[SMC] Спроба побудови SMC hint для %s завершилася помилкою: %s",
                        symbol,
                        exc,
                    )
            if smc_hint is not None:
                plain_fn = _get_smc_plain_serializer()
                if plain_fn is not None:
                    plain_hint = plain_fn(smc_hint)
                    if plain_hint is not None:
                        normalized["smc_hint"] = plain_hint
                else:
                    normalized["smc_hint"] = smc_hint

            state_manager.update_asset(symbol, normalized)
        except Exception as e:
            logger.error(f"Помилка AssetMonitor для {symbol}: {str(e)}")
            state_manager.update_asset(symbol, create_error_signal(symbol, str(e)))


async def screening_producer(
    monitor: AssetMonitorStage1,
    store: UnifiedDataStore,
    store_fast_symbols: UnifiedDataStore,
    assets: list[str],
    redis_conn: Redis[str],
    *,
    reference_symbol: str = "XAUUSD",
    timeframe: str = DEFAULT_TIMEFRAME,
    lookback: int = DEFAULT_LOOKBACK,
    interval_sec: int = TRADE_REFRESH_INTERVAL,
    min_ready_pct: float = MIN_READY_PCT,
    state_manager: AssetStateManager | None = None,
) -> None:
    logger.info(
        (
            "🚀 Старт screening_producer: %d активів, таймфрейм %s, глибина %d, "
            "оновлення кожні %d сек"
        ),
        len(assets),
        timeframe,
        lookback,
        interval_sec,
    )
    if state_manager is None:
        assets_current = [s.lower() for s in (assets or [])]
        state_manager = AssetStateManager(assets_current)
    else:
        assets_current = list(state_manager.state.keys())
    for sym in assets_current:
        state_manager.init_asset(sym)
    ref = (reference_symbol or "XAUUSD").lower()
    if ref not in state_manager.state:
        state_manager.init_asset(ref)
    logger.info(f"Ініціалізовано стан для {len(assets_current)} активів")

    # Забезпечуємо доступ кешу до UnifiedDataStore через state_manager.cache (для публікацій у Redis)
    try:
        if getattr(state_manager, "cache", None) is None:
            state_manager.set_cache_handler(store)
    except Exception:
        pass

    await publish_full_state(state_manager, store, redis_conn)
    while True:
        start_time = time.time()
        try:
            new_assets_raw = await store_fast_symbols.get_fast_symbols()
            if new_assets_raw:
                new_assets = [s.lower() for s in new_assets_raw]
                current_set = set(assets_current)
                new_set = set(new_assets)
                added = new_set - current_set
                removed = current_set - new_set
                for symbol in added:
                    state_manager.init_asset(symbol)
                assets_current = list(new_set)
                for symbol in removed:
                    state_manager.state.pop(symbol, None)
                if added or removed:
                    logger.info(
                        "🔄 Оновлено список активів: +%d/-%d (загалом: %d)",
                        len(added),
                        len(removed),
                        len(assets_current),
                    )
            else:
                logger.debug(
                    "get_fast_symbols() повернув порожньо — тримаємо попередній список (%d).",
                    len(assets_current),
                )
        except Exception as e:
            logger.error(f"Помилка оновлення активів: {str(e)}")
        ready_assets: list[str] = []
        ref_ready = False
        for symbol in assets_current:
            try:
                # Перевірка готовності даних для активу
                df_tmp = await store.get_df(symbol, timeframe, limit=lookback)
                # Якщо дані є і їх достатньо, додаємо до ready_assets
                if df_tmp is not None and not df_tmp.empty and len(df_tmp) >= lookback:
                    ready_assets.append(symbol)
            except Exception:
                continue
        try:
            # Перевірка готовності даних для референсного активу
            ref_df = await store.get_df(
                reference_symbol.lower(), timeframe, limit=lookback
            )
            ref_ready = bool(
                ref_df is not None and not ref_df.empty and len(ref_df) >= lookback
            )
        except Exception:
            ref_ready = False
        ready_count = len(ready_assets)
        min_ready = max(1, int(len(assets_current) * min_ready_pct))
        if ready_count < min_ready:
            logger.warning(
                "⏳ Недостатньо даних: %d/%d активів готові. Очікування %d сек...",
                ready_count,
                min_ready,
                interval_sec,
            )
            # Встановлюємо явний стан NO_DATA для неготових активів,
            # щоб UI не зависав у стані 'init'.
            try:
                not_ready = [s for s in assets_current if s not in ready_assets]
                for symbol in not_ready:
                    state_manager.update_asset(symbol, create_no_data_signal(symbol))
                if not_ready:
                    logger.info(
                        "📭 NO_DATA для неготових активів: %d (публікація проміжного стану)",
                        len(not_ready),
                    )
                # Публікуємо частковий стан, щоб UI одразу побачив NO_DATA
                await publish_full_state(state_manager, store, redis_conn)
            except Exception as e:
                logger.error("Помилка під час оновлення NO_DATA: %s", str(e))
            await asyncio.sleep(interval_sec)
            # Переходимо до наступної ітерації while True
            continue
        logger.info(
            f"📊 Дані готові для {ready_count}/{len(assets_current)} активів"
            + (" (+reference ready)" if ref_ready else "")
        )
        try:
            batch_size = int(SCREENING_BATCH_SIZE or 20)
            tasks: list[asyncio.Task[Any]] = []
            for i in range(0, len(ready_assets), batch_size):
                batch = ready_assets[i : i + batch_size]
                tasks.append(
                    asyncio.create_task(
                        process_asset_batch(
                            batch, monitor, store, timeframe, lookback, state_manager
                        )
                    )
                )
            if tasks:
                await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(f"Критична помилка Stage1: {str(e)}")

        logger.info("📢 Публікація стану активів...")
        await publish_full_state(state_manager, store, redis_conn)

        processing_time = time.time() - start_time
        logger.info(f"⏳ Час обробки циклу: {processing_time:.2f} сек")
        sleep_time = (
            1
            if processing_time >= interval_sec
            else max(1, interval_sec - int(processing_time))
        )
        logger.info(f"⏱ Час очікування до наступного циклу: {sleep_time} сек")
        await asyncio.sleep(sleep_time)
