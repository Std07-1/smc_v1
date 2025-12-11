"""SMC-only продюсер стану.

Цілі:
    • збирати сирі дані з UnifiedDataStore;
    • будувати SmcHints для кожного символу;
    • зберігати результат у SmcStateManager і публікувати через UI.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from redis.asyncio import Redis
from rich.console import Console
from rich.logging import RichHandler

from app.smc_state_manager import SmcStateManager
from config.config import (
    DEFAULT_LOOKBACK,
    DEFAULT_TIMEFRAME,
    MIN_READY_PCT,
    SMC_BATCH_SIZE,
    SMC_PIPELINE_ENABLED,
    SMC_REFRESH_INTERVAL,
    SMC_RUNTIME_PARAMS,
)
from config.constants import ASSET_STATE, K_STATS
from data.unified_store import UnifiedDataStore
from UI.publish_smc_state import publish_smc_state
from utils.utils import create_error_signal, create_no_data_signal

if TYPE_CHECKING:  # pragma: no cover - лише для тайпінгів
    from smc_core.engine import SmcCoreEngine
    from smc_core.smc_types import SmcHint

logger = logging.getLogger("app.smc_producer")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    logger.addHandler(RichHandler(console=Console(stderr=True), show_path=False))
    logger.propagate = False


_SMC_ENGINE: SmcCoreEngine | None = None
_SMC_PLAIN_SERIALIZER: Callable[[Any], dict[str, Any] | None] | None = None


async def _get_smc_engine() -> SmcCoreEngine | None:
    """Ліниво створює SmcCoreEngine з smc_core.engine."""

    global _SMC_ENGINE
    if not SMC_RUNTIME_PARAMS.get("enabled", True):
        return None
    if _SMC_ENGINE is not None:
        return _SMC_ENGINE
    try:
        module_engine = importlib.import_module("smc_core.engine")
        engine_cls = module_engine.SmcCoreEngine
        _SMC_ENGINE = engine_cls()
        logger.info("[SMC] SmcCoreEngine ініціалізовано")
    except Exception as exc:  # pragma: no cover - best-effort
        logger.warning("[SMC] Не вдалося ініціалізувати SmcCoreEngine: %s", exc)
        _SMC_ENGINE = None
    return _SMC_ENGINE


def _get_smc_plain_serializer() -> Callable[[Any], dict[str, Any] | None] | None:
    """Повертає функцію to_plain_smc_hint для безпечної публікації."""

    global _SMC_PLAIN_SERIALIZER
    if not SMC_RUNTIME_PARAMS.get("enabled", True):
        return None
    if _SMC_PLAIN_SERIALIZER is not None:
        return _SMC_PLAIN_SERIALIZER
    try:
        module_serializers = importlib.import_module("smc_core.serializers")
        _SMC_PLAIN_SERIALIZER = module_serializers.to_plain_smc_hint
    except Exception as exc:  # pragma: no cover - best-effort
        logger.debug("[SMC] Не вдалося імпортувати to_plain_smc_hint: %s", exc)
        _SMC_PLAIN_SERIALIZER = None
    return _SMC_PLAIN_SERIALIZER


def _build_pipeline_meta(
    *, assets_total: int, ready_assets: int, min_ready: int
) -> dict[str, Any]:
    """Повертає компактний стан пайплайна для UI мета-блоку."""

    total = max(0, assets_total)
    ready = max(0, min(ready_assets, total)) if total else max(0, ready_assets)
    required = max(1, min_ready) if total else max(1, min_ready)

    if ready == 0:
        state = "COLD"
    elif ready < required:
        state = "WARMUP"
    else:
        state = "LIVE"

    ready_pct = 0.0 if total == 0 else round(ready / total, 4)

    return {
        "pipeline_state": state,
        "pipeline_ready_assets": ready,
        "pipeline_min_ready": required,
        "pipeline_assets_total": total,
        "pipeline_ready_pct": ready_pct,
    }


async def _build_smc_hint(*, symbol: str, store: UnifiedDataStore) -> SmcHint | None:
    """Формує SmcHint через smc_core.input_adapter."""

    params = SMC_RUNTIME_PARAMS
    if not params.get("enabled", True):
        return None
    try:
        tf_primary = str(params.get("tf_primary", DEFAULT_TIMEFRAME))
        tfs_extra_cfg = params.get("tfs_extra", ("5m", "15m", "1h"))
        tfs_extra = tuple(tfs_extra_cfg)
        limit = int(params.get("limit", DEFAULT_LOOKBACK))
    except Exception as exc:
        logger.debug("[SMC] Некоректні runtime параметри: %s", exc)
        return None

    try:
        module_adapter = importlib.import_module("smc_core.input_adapter")
        build_input = module_adapter.build_smc_input_from_store
    except Exception as exc:  # pragma: no cover - best-effort
        logger.warning("[SMC] Не вдалося імпортувати input_adapter: %s", exc)
        return None

    engine = await _get_smc_engine()
    if engine is None:
        return None

    t0 = time.perf_counter()
    try:
        smc_input = await build_input(
            store=store,
            symbol=symbol,
            tf_primary=tf_primary,
            tfs_extra=tfs_extra,
            limit=limit,
        )
        hint = engine.process_snapshot(smc_input)
    except Exception as exc:
        logger.debug("[SMC] Помилка побудови hint для %s: %s", symbol, exc)
        return None

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    if params.get("log_latency", False):
        logger.debug(
            "[SMC] symbol=%s tf=%s latency_ms=%.2f",
            symbol,
            getattr(smc_input, "tf_primary", tf_primary),
            elapsed_ms,
        )

    return hint


async def process_smc_batch(
    symbols: Iterable[str],
    store: UnifiedDataStore,
    state_manager: SmcStateManager,
    *,
    timeframe: str = DEFAULT_TIMEFRAME,
    lookback: int = DEFAULT_LOOKBACK,
) -> None:
    """Формуємо smc_hint та базові stats для кожного символу."""

    for symbol in symbols:
        sym = str(symbol).lower()
        try:
            df = await store.get_df(sym, timeframe, limit=lookback)
            if df is None or df.empty or len(df) < max(5, lookback // 2):
                state_manager.update_asset(sym, create_no_data_signal(sym))
                continue

            stats: dict[str, Any] = {}
            try:
                stats["current_price"] = float(df["close"].iloc[-1])
            except Exception:
                stats["current_price"] = None
            stats["smc_df_rows"] = int(len(df))
            stats["smc_timeframe"] = timeframe
            if "volume" in df.columns:
                try:
                    stats["volume"] = float(df["volume"].iloc[-1])
                except Exception:
                    pass
            if "timestamp" in df.columns:
                try:
                    stats["timestamp"] = df["timestamp"].iloc[-1]
                except Exception:
                    pass

            price_tick = store.get_price_tick(sym)
            if isinstance(price_tick, dict):
                stats.update(
                    {
                        "live_price_mid": price_tick.get("mid"),
                        "live_price_bid": price_tick.get("bid"),
                        "live_price_ask": price_tick.get("ask"),
                        "tick_ts": price_tick.get("tick_ts"),
                        "tick_snap_ts": price_tick.get("snap_ts"),
                        "tick_age_sec": price_tick.get("age"),
                        "tick_is_stale": price_tick.get("is_stale", False),
                    }
                )
                if price_tick.get("mid") is not None:
                    stats["current_price"] = float(price_tick["mid"])
                    stats["price_source"] = "price_stream"

            t0 = time.perf_counter()
            smc_hint = await _build_smc_hint(symbol=sym, store=store)
            stats["smc_latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
            if smc_hint is None:
                state_manager.update_asset(
                    sym,
                    {
                        "signal": "SMC_PENDING",
                        "state": ASSET_STATE["NORMAL"],
                        K_STATS: stats,
                        "hints": ["SMC: очікуємо оновлення snapshot"],
                    },
                )
                continue

            plain_serializer = _get_smc_plain_serializer()
            plain_hint = plain_serializer(smc_hint) if plain_serializer else smc_hint
            state_manager.update_asset(
                sym,
                {
                    "signal": "SMC_HINT",
                    "state": ASSET_STATE["NORMAL"],
                    K_STATS: stats,
                    "smc_hint": plain_hint,
                    "hints": ["SMC: дані оновлено"],
                },
            )
        except Exception as exc:  # pragma: no cover - захист від edge-case
            logger.error("[SMC] Помилка обробки %s: %s", sym, exc, exc_info=True)
            err_payload = create_error_signal(sym, str(exc))
            err_payload["signal"] = "SMC_ERROR"
            err_payload["state"] = ASSET_STATE["ERROR"]
            state_manager.update_asset(sym, err_payload)


async def smc_producer(
    *,
    store: UnifiedDataStore,
    store_fast_symbols: UnifiedDataStore,
    assets: list[str],
    redis_conn: Redis[str],
    timeframe: str = DEFAULT_TIMEFRAME,
    lookback: int = DEFAULT_LOOKBACK,
    interval_sec: int = SMC_REFRESH_INTERVAL,
    min_ready_pct: float = MIN_READY_PCT,
    state_manager: SmcStateManager | None = None,
) -> None:
    """Продюсер для SMC-пайплайна."""

    if not SMC_PIPELINE_ENABLED:
        logger.info("[SMC] Pipeline disabled флагом, task exit")
        return

    assets_current = [s.lower() for s in (assets or [])]
    state_manager = state_manager or SmcStateManager(assets_current)
    state_manager.set_cache_handler(store)

    min_ready = (
        max(1, int(len(assets_current) * min_ready_pct)) if assets_current else 1
    )
    pipeline_meta = _build_pipeline_meta(
        assets_total=len(assets_current), ready_assets=0, min_ready=min_ready
    )

    cycle_seq = 0
    await publish_smc_state(
        state_manager,
        store,
        redis_conn,
        meta_extra={
            "cycle_seq": cycle_seq,
            "cycle_started_ts": datetime.utcnow().isoformat() + "Z",
            "cycle_reason": "smc_bootstrap",
            **pipeline_meta,
        },
    )

    while True:
        cycle_seq += 1
        cycle_started_ts = time.time()

        try:
            fresh_symbols = await store_fast_symbols.get_fast_symbols()
            if fresh_symbols:
                new_assets = [s.lower() for s in fresh_symbols]
                current_set = set(assets_current)
                new_set = set(new_assets)
                added = new_set - current_set
                removed = current_set - new_set
                for sym in added:
                    state_manager.init_asset(sym)
                for sym in removed:
                    state_manager.state.pop(sym, None)
                assets_current = list(new_set)
        except Exception as exc:
            logger.debug("[SMC] Не вдалося оновити список активів: %s", exc)

        ready_assets: list[str] = []
        for symbol in assets_current:
            try:
                df_tmp = await store.get_df(symbol, timeframe, limit=lookback)
                if df_tmp is not None and not df_tmp.empty and len(df_tmp) >= lookback:
                    ready_assets.append(symbol)
            except Exception:
                continue

        min_ready = (
            max(1, int(len(assets_current) * min_ready_pct)) if assets_current else 1
        )
        if len(ready_assets) < min_ready:
            for symbol in assets_current:
                if symbol not in ready_assets:
                    state_manager.update_asset(symbol, create_no_data_signal(symbol))
            pipeline_meta = _build_pipeline_meta(
                assets_total=len(assets_current),
                ready_assets=len(ready_assets),
                min_ready=min_ready,
            )
            await publish_smc_state(
                state_manager,
                store,
                redis_conn,
                meta_extra={
                    "cycle_seq": cycle_seq,
                    "cycle_started_ts": datetime.utcnow().isoformat() + "Z",
                    "cycle_reason": "smc_insufficient_data",
                    **pipeline_meta,
                },
            )
            await asyncio.sleep(interval_sec)
            continue

        tasks: list[asyncio.Task[Any]] = []
        for i in range(0, len(ready_assets), SMC_BATCH_SIZE):
            batch = ready_assets[i : i + SMC_BATCH_SIZE]
            tasks.append(
                asyncio.create_task(
                    process_smc_batch(
                        batch,
                        store,
                        state_manager,
                        timeframe=timeframe,
                        lookback=lookback,
                    )
                )
            )
        if tasks:
            await asyncio.gather(*tasks)

        cycle_ready_ts = time.time()
        pipeline_meta = _build_pipeline_meta(
            assets_total=len(assets_current),
            ready_assets=len(ready_assets),
            min_ready=min_ready,
        )
        await publish_smc_state(
            state_manager,
            store,
            redis_conn,
            meta_extra={
                "cycle_seq": cycle_seq,
                "cycle_started_ts": datetime.fromtimestamp(cycle_started_ts).isoformat()
                + "Z",
                "cycle_ready_ts": datetime.fromtimestamp(cycle_ready_ts).isoformat()
                + "Z",
                "cycle_compute_ms": round(
                    (cycle_ready_ts - cycle_started_ts) * 1000.0, 2
                ),
                "cycle_reason": "smc_screening",
                **pipeline_meta,
            },
        )

        elapsed = time.time() - cycle_started_ts
        sleep_time = (
            max(1, int(interval_sec - elapsed)) if elapsed < interval_sec else 1
        )
        await asyncio.sleep(sleep_time)
