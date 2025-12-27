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
from typing import TYPE_CHECKING, Any

import pandas as pd
from redis.asyncio import Redis

from app.fxcm_history_state import classify_history, timeframe_to_ms
from app.smc_state_manager import SmcStateManager
from config.config import (
    DEFAULT_LOOKBACK,
    DEFAULT_TIMEFRAME,
    MIN_READY_PCT,
    SMC_BATCH_SIZE,
    SMC_CYCLE_BUDGET_MS,
    SMC_MAX_ASSETS_PER_CYCLE,
    SMC_PIPELINE_ENABLED,
    SMC_REFRESH_INTERVAL,
    SMC_RUNTIME_PARAMS,
    SMC_S2_STALE_K,
    SMC_TF_PLAN,
    SMC_VIEWER_OHLCV_FRAMES_BY_TF_ENABLED,
    SMC_VIEWER_OHLCV_FRAMES_MIN_BARS_BY_TF,
)
from config.constants import ASSET_STATE, K_STATS
from core.serialization import (
    safe_float,
    utc_ms_to_iso_z,
    utc_now_human_utc,
    utc_seconds_to_human_utc,
)
from data.fxcm_status_listener import get_fxcm_feed_state
from data.unified_store import UnifiedDataStore
from smc_core.smc_types import SmcHint
from UI.publish_smc_state import publish_smc_state

if TYPE_CHECKING:  # pragma: no cover - лише для тайпінгів
    from smc_core.engine import SmcCoreEngine
    from smc_core.smc_types import SmcHint

logger = logging.getLogger("app.smc_producer")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler())
    logger.propagate = False


_SMC_ENGINE: SmcCoreEngine | None = None
_SMC_PLAIN_SERIALIZER: Callable[[Any], dict[str, Any] | None] | None = None

# Case G: тримаємо попередні wick_clusters (WICK_CLUSTER tracker) на рівні app,
# щоб SMC-core лишався детермінованим відносно context.
_PREV_WICK_CLUSTERS_BY_SYMBOL: dict[str, list[dict[str, Any]]] = {}


def _df_to_ohlcv_bars_for_viewer(df: pd.DataFrame, *, tf: str) -> list[dict[str, Any]]:
    """Конвертує DataFrame OHLCV у bars-форму для UI_v2 viewer_state_builder.

    Важливо:
    - `time` кодуємо як close_time (ms), якщо він є; інакше як open_time+tf_ms;
    - додаємо `complete=True`, щоб policy у presentation шарі була однозначною.
    """

    if df is None or df.empty:
        return []

    tf_ms_raw = timeframe_to_ms(str(tf))
    tf_ms = int(tf_ms_raw) if tf_ms_raw is not None else 0

    # Рахуємо close_time як окремий Series (best-effort), щоб не мутувати df.
    close_time_ms: pd.Series | None = None
    if "close_time" in df.columns:
        close_time_ms = pd.to_numeric(df["close_time"], errors="coerce")
    elif "open_time" in df.columns:
        open_time = pd.to_numeric(df["open_time"], errors="coerce")
        close_time_ms = open_time + tf_ms
    elif "timestamp" in df.columns:
        ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        open_ns = ts.astype("int64")
        open_ms = (open_ns // 1_000_000).where(open_ns > 0)
        close_time_ms = open_ms + tf_ms

    records = df.to_dict("records")
    out: list[dict[str, Any]] = []
    for idx, r in enumerate(records):
        if not isinstance(r, dict):
            continue

        t_any = None
        if close_time_ms is not None and idx < len(close_time_ms):
            try:
                t_any = close_time_ms.iloc[idx]
            except Exception:
                t_any = None
        if t_any is None:
            t_any = r.get("close_time") or r.get("open_time")

        if t_any is None:
            continue
        try:
            t_ms = int(float(t_any))
        except (TypeError, ValueError):
            continue

        o = safe_float(r.get("open"))
        h = safe_float(r.get("high"))
        low = safe_float(r.get("low"))
        c = safe_float(r.get("close"))
        v = safe_float(r.get("volume")) or 0.0
        if o is None or h is None or low is None or c is None:
            continue

        out.append(
            {
                "time": int(t_ms),
                "open": float(o),
                "high": float(h),
                "low": float(low),
                "close": float(c),
                "volume": float(v),
                "complete": True,
            }
        )
    return out


async def _build_ohlcv_frames_by_tf_for_viewer(
    *,
    symbol: str,
    store: UnifiedDataStore,
) -> dict[str, list[dict[str, Any]]]:
    """Формує `asset["ohlcv_frames_by_tf"]` для live payload.

    Це presentation input для Levels-V1 (PDH/PDL та ін.), не для SMC-core.
    """

    out: dict[str, list[dict[str, Any]]] = {}
    for tf, min_bars in (SMC_VIEWER_OHLCV_FRAMES_MIN_BARS_BY_TF or {}).items():
        if not isinstance(tf, str):
            continue
        try:
            limit = int(min_bars)
        except Exception:
            continue
        if limit <= 0:
            continue
        try:
            df = await store.get_df(symbol, tf, limit=limit)
        except Exception:
            df = None
        if df is None or df.empty:
            out[tf] = []
            continue
        out[tf] = _df_to_ohlcv_bars_for_viewer(df, tf=tf)
    return out


def _create_error_signal(symbol: str, error: str) -> dict[str, Any]:
    """Формує стандартний payload помилки для UI.

    Це локальна compat-реалізація замість legacy `utils.utils.create_error_signal`.
    """

    return {
        "symbol": symbol,
        "signal": "NONE",
        "trigger_reasons": ["processing_error"],
        "confidence": 0.0,
        "hints": [f"Помилка: {error}"],
        "state": ASSET_STATE["ERROR"],
        "visible": True,
    }


def _history_ok_for_compute(*, history_state: str, allow_stale_tail: bool) -> bool:
    """Повертає True, якщо історія достатня для обчислень SMC.

    UX/операційна вимога: у неробочі години/вихідні tail може бути "stale" за
    простим wall-clock критерієм, але ми все одно хочемо показати UI останній
    відомий стан (не порожній екран). Тому stale_tail дозволяємо лише коли
    ринок не постачає OHLCV (market!=open або ohlcv delayed/down).
    """

    state = str(history_state or "unknown").strip().lower()
    if state == "ok":
        return True
    if allow_stale_tail and state == "stale_tail":
        return True
    return False


def _preserve_previous_hint_if_gated(
    *,
    previous_hint: dict[str, Any] | None,
    new_hint: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, bool]:
    """Зберігає попередній `smc_hint`, якщо новий є gated-empty.

    Проблема:
    - Stage0 гейти можуть повертати hint з `structure/liquidity/zones=None`.
    - Якщо ми безумовно перезаписуємо ним `asset.smc_hint`, UI стає порожнім
      (Trend/Bias/AMD UNKNOWN), хоча «останній відомий стан» був валідним.

    Політика:
    - Якщо `new_hint` має gates і всі core-блоки None, але `previous_hint` містить
      будь-який з блоків (structure/liquidity/zones), то повертаємо previous_hint,
      оновивши лише `meta` з нового (gates/tf_health/history_state тощо).
    """

    if not isinstance(previous_hint, dict) or not previous_hint:
        return new_hint, False
    if not isinstance(new_hint, dict) or not new_hint:
        return new_hint, False

    new_meta_any = new_hint.get("meta")
    new_meta: dict[str, Any] = new_meta_any if isinstance(new_meta_any, dict) else {}
    gates_any = new_meta.get("gates")
    has_gates = isinstance(gates_any, list) and any(
        isinstance(g, dict) for g in gates_any
    )

    if not has_gates:
        return new_hint, False

    # gated-empty: core-блоки відсутні
    if (
        new_hint.get("structure") is not None
        or new_hint.get("liquidity") is not None
        or new_hint.get("zones") is not None
    ):
        return new_hint, False

    # previous має хоч щось, що можна показувати
    if (
        previous_hint.get("structure") is None
        and previous_hint.get("liquidity") is None
        and previous_hint.get("zones") is None
    ):
        return new_hint, False

    merged = dict(previous_hint)
    prev_meta_any = merged.get("meta")
    prev_meta: dict[str, Any] = prev_meta_any if isinstance(prev_meta_any, dict) else {}

    merged_meta = {**prev_meta, **new_meta, "smc_hint_preserved": True}
    merged["meta"] = merged_meta
    return merged, True


def _apply_fast_symbols_update(
    *,
    state_manager: SmcStateManager,
    assets_current: list[str],
    fresh_symbols: list[str] | None,
) -> list[str]:
    """Оновлює список активів з `fast_symbols` без затирання стану.

    Причина: список `fast_symbols` може тимчасово повертати неповний набір.
    Якщо видаляти asset зі `state_manager.state`, UI отримує «порожній» INIT
    при наступному додаванні → ефект «то все є, то нічого».

    Політика:
    - Додаємо нові символи (якщо їх немає у state) через `init_asset`.
    - Символи, які зникли зі списку, НЕ видаляємо зі state; лише позначаємо
      сигналом `SMC_PAUSED` та підказкою (останній `smc_hint` зберігається).
    - Повертаємо оновлений `assets_current` для обчислень.
    """

    if not fresh_symbols:
        return assets_current

    new_assets = [str(s).lower() for s in fresh_symbols]
    current_set = set([str(s).lower() for s in (assets_current or [])])
    new_set = set(new_assets)

    added = new_set - current_set
    removed = current_set - new_set

    for sym in sorted(added):
        if sym not in state_manager.state:
            state_manager.init_asset(sym)
        else:
            state_manager.update_asset(
                sym,
                {
                    "hints": [
                        "SMC: символ повернувся у fast_symbols — очікуємо оновлення"
                    ],
                    K_STATS: {"smc_fast_list_member": True},
                },
            )
        logger.info("SMC: символ %s повернувся у fast_symbols", sym)

    for sym in sorted(removed):
        # Не затираємо `smc_hint`: лише сигналізуємо, що символ зараз не оновлюється.
        state_manager.update_asset(
            sym,
            {
                "signal": "SMC_PAUSED",
                "hints": [
                    "SMC: символ тимчасово відсутній у fast_symbols (стан збережено)"
                ],
                K_STATS: {"smc_fast_list_member": False},
            },
        )
        logger.info("SMC: символ %s тимчасово відсутній у fast_symbols", sym)

    return list(new_set)


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
    *,
    assets_total: int,
    ready_assets: int,
    min_ready: int,
    ready_assets_min: int | None = None,
    pipeline_min_ready_bars: int | None = None,
    pipeline_target_bars: int | None = None,
) -> dict[str, Any]:
    """Повертає компактний стан пайплайна для UI мета-блоку.

    Семантика:
    - ready_assets: кількість активів, що досягли target_bars (для SMC/READY).
    - ready_assets_min: кількість активів, що досягли min_ready_bars (вихід з COLD).
    - min_ready: legacy-поріг по кількості активів (min_ready_pct), залишаємо для сумісності UI.
    """

    total = max(0, int(assets_total))
    ready_target = (
        max(0, min(int(ready_assets), total)) if total else max(0, int(ready_assets))
    )
    ready_min = (
        max(0, min(int(ready_assets_min), total))
        if isinstance(ready_assets_min, (int, float))
        else None
    )
    required_assets = max(1, int(min_ready)) if total else max(1, int(min_ready))

    # FSM для UI: будуємо від contract min_ready_bars + target_bars.
    # Якщо ці пороги не задані — падаємо назад на legacy-поведінку.
    if isinstance(pipeline_target_bars, (int, float)) and isinstance(
        pipeline_min_ready_bars, (int, float)
    ):
        if (ready_min or 0) <= 0 and total > 0:
            state = "COLD"
        elif ready_target >= total and total > 0:
            state = "LIVE"
        else:
            state = "WARMUP"
    else:
        if ready_target == 0:
            state = "COLD"
        elif ready_target < required_assets:
            state = "WARMUP"
        else:
            state = "LIVE"

    ready_pct = 0.0 if total == 0 else round(ready_target / total, 4)

    meta: dict[str, Any] = {
        "pipeline_state": state,
        "pipeline_ready_assets": ready_target,
        "pipeline_min_ready": required_assets,
        "pipeline_assets_total": total,
        "pipeline_ready_pct": ready_pct,
    }
    if ready_min is not None:
        meta["pipeline_ready_assets_min"] = ready_min
    if isinstance(pipeline_min_ready_bars, (int, float)):
        meta["pipeline_min_ready_bars"] = int(pipeline_min_ready_bars)
    if isinstance(pipeline_target_bars, (int, float)):
        meta["pipeline_target_bars"] = int(pipeline_target_bars)
    return meta


def _classify_pipeline_state_local(
    *, bars: int, min_ready_bars: int, target_bars: int
) -> str:
    """Класифікація локального pipeline-стану для одного символу."""

    ready = max(0, int(bars))
    min_ready = max(1, int(min_ready_bars))
    target = max(min_ready, int(target_bars))

    if ready < min_ready:
        return "COLD"
    if ready < target:
        return "WARMUP"
    return "LIVE"


def _local_pipeline_payload(
    *, bars: int, min_ready_bars: int, target_bars: int
) -> dict[str, Any]:
    """Формує компактний локальний pipeline-блок для одного символу."""

    ready = max(0, int(bars))
    min_ready = max(1, int(min_ready_bars))
    target = max(min_ready, int(target_bars))
    ratio = min(1.0, max(0.0, ready / target))
    return {
        "state": _classify_pipeline_state_local(
            bars=ready,
            min_ready_bars=min_ready,
            target_bars=target,
        ),
        "ready_bars": ready,
        "required_bars": target,
        "required_bars_min": min_ready,
        "ready_ratio": round(ratio, 4),
    }


def _apply_local_pipeline_stats(
    *,
    state_manager: SmcStateManager,
    bars_by_symbol: dict[str, int],
    min_ready_bars_by_symbol: dict[str, int],
    target_bars_by_symbol: dict[str, int],
) -> None:
    """Дописує локальний pipeline-стан у stats для кожного активу."""

    for symbol, asset in (state_manager.state or {}).items():
        if not isinstance(asset, dict):
            continue

        stats = asset.get("stats")
        if not isinstance(stats, dict):
            stats = {}
            asset["stats"] = stats

        sym_norm = str(symbol).lower()
        bars = int(bars_by_symbol.get(sym_norm, 0))
        min_ready = int(min_ready_bars_by_symbol.get(sym_norm, 0) or 0)
        target = int(target_bars_by_symbol.get(sym_norm, 0) or 0)
        local = _local_pipeline_payload(
            bars=bars,
            min_ready_bars=max(1, min_ready) if min_ready > 0 else max(1, target),
            target_bars=max(1, target),
        )

        stats["pipeline_state_local"] = local["state"]
        stats["pipeline_ready_bars"] = local["ready_bars"]
        stats["pipeline_required_bars"] = local["required_bars"]
        stats["pipeline_required_bars_min"] = local["required_bars_min"]
        stats["pipeline_ready_ratio"] = local["ready_ratio"]


def _select_symbols_for_cycle(
    *, ready_symbols: list[str], max_per_cycle: int
) -> tuple[list[str], list[str]]:
    """Scheduler v0: повертає (selected, skipped) як slice від ready_symbols."""

    if max_per_cycle <= 0:
        return list(ready_symbols), []
    selected = list(ready_symbols[: int(max_per_cycle)])
    skipped = list(ready_symbols[len(selected) :])
    return selected, skipped


def _build_capacity_meta(*, ready_assets: int, processed_assets: int) -> dict[str, Any]:
    """Мета-поля для capacity guard (processed/skipped)."""

    ready = max(0, int(ready_assets))
    processed = max(0, int(processed_assets))
    skipped = max(0, ready - processed)
    return {
        "pipeline_processed_assets": processed,
        "pipeline_skipped_assets": skipped,
    }


def _should_run_smc_cycle_by_fxcm_status() -> tuple[bool, str]:
    """Визначає, чи варто запускати важкий SMC-цикл.

    Логіка:
    - market=closed -> IDLE (не рахуємо);
    - market=open і price/ohlcv != ok -> IDLE;
    - unknown/none -> не блокуємо (cold-start), але причина фіксується.
    """

    state = get_fxcm_feed_state()
    market = (state.market_state or "").strip().lower() or "unknown"
    price = (state.price_state or "").strip().lower() or ""
    ohlcv = (state.ohlcv_state or "").strip().lower() or ""

    status_ts = None
    try:
        if state.status_ts is not None:
            status_ts = float(state.status_ts)
    except (TypeError, ValueError):
        status_ts = None
    status_age_sec = None
    if status_ts is not None:
        status_age_sec = max(0.0, time.time() - status_ts)

    if market == "closed":
        # Інколи конектор може віддати суперечливий статус: market=closed, але ticks_alive.
        # У такому разі не блокуємо SMC, якщо статус свіжий і price_state=ok.
        if price == "ok" and (status_age_sec is None or status_age_sec <= 60.0):
            return True, "fxcm_market_closed_but_ticks_ok"
        return False, "fxcm_market_closed"
    if market == "open":
        if price and price != "ok":
            return False, f"fxcm_price_{price}"
        # UX/контракт: `ohlcv` у fxcm:status — діагностичний.
        # Не блокуємо SMC цикл лише через delayed/lag/down, щоб UI бачив live ціну.
        if ohlcv and ohlcv != "ok":
            return True, f"fxcm_ohlcv_{ohlcv}_ignored"
        return True, "fxcm_ok"

    return True, "fxcm_status_unknown"


def _extract_last_open_time_ms(frame: Any) -> int | None:
    """Повертає open_time останнього бара у мс (best-effort)."""

    if frame is None or getattr(frame, "empty", True):
        return None
    try:
        last_row = frame.iloc[-1]
        last_open_raw = last_row.get("open_time") or last_row.get("close_time")
    except Exception:
        last_open_raw = None
    try:
        if last_open_raw is None:
            return None
        val = float(last_open_raw)
        return int(val) if val > 1e12 else int(val * 1000.0)
    except Exception:
        return None


def _build_tf_health(
    *,
    tf_plan: dict[str, Any],
    ohlc_by_tf: Any,
    store: Any,
    symbol: str,
) -> dict[str, Any]:
    """Будує compact tf_health для UI/діагностики.

    Мінімальний Stage0 набір: has_data/bars/last_ts/lag_ms.
    """

    tfs_raw = [
        str(tf_plan.get("tf_exec", "1m")),
        str(tf_plan.get("tf_structure", "5m")),
        *list(tf_plan.get("tf_context", ("1h", "4h")) or ()),
    ]
    seen: set[str] = set()
    tfs: list[str] = []
    for tf in tfs_raw:
        if tf and tf not in seen:
            seen.add(tf)
            tfs.append(tf)

    now_ms = int(time.time() * 1000.0)
    out: dict[str, Any] = {}
    for tf in tfs:
        frame = None
        try:
            frame = ohlc_by_tf.get(tf) if ohlc_by_tf is not None else None
        except Exception:
            frame = None

        has_data = bool(frame is not None and not getattr(frame, "empty", True))
        bars_window = 0
        if has_data and frame is not None:
            try:
                bars_window = int(len(frame))
            except Exception:
                bars_window = 0

        # Важливо: `frame` тут зазвичай обмежений `limit` (runtime compute-вікно).
        # Для прозорості в UI хочемо показувати total історію, яка є в UDS.
        bars_total = bars_window
        try:
            ram = getattr(store, "ram", None)
            if ram is not None and hasattr(ram, "inspect_entry"):
                total_count, _last_open_sec = ram.inspect_entry(str(symbol), str(tf))
                if isinstance(total_count, int) and total_count >= 0:
                    bars_total = int(total_count)
        except Exception:
            # best-effort: не ламаємо hot-path
            pass

        last_open_time_ms = _extract_last_open_time_ms(frame) if has_data else None
        last_ts = None
        lag_ms = None
        if last_open_time_ms is not None:
            lag_ms = max(0, now_ms - int(last_open_time_ms))
            try:
                last_ts = utc_ms_to_iso_z(int(last_open_time_ms))
            except Exception:
                last_ts = None

        out[tf] = {
            "has_data": bool(has_data),
            "bars": int(bars_total),
            "bars_window": int(bars_window),
            "last_ts": last_ts,
            "lag_ms": lag_ms,
        }

    return out


async def _build_smc_hint(*, symbol: str, store: UnifiedDataStore) -> SmcHint | None:
    """Формує SmcHint через smc_core.input_adapter."""

    params = SMC_RUNTIME_PARAMS
    if not params.get("enabled", True):
        return None
    try:
        # Stage0 TF-правда: tf_primary завжди дорівнює tf_structure (5m).
        tf_plan = {
            "tf_exec": str(SMC_TF_PLAN.get("tf_exec", DEFAULT_TIMEFRAME)),
            "tf_structure": str(SMC_TF_PLAN.get("tf_structure", "5m")),
            "tf_context": tuple(SMC_TF_PLAN.get("tf_context", ("1h", "4h"))),
        }
        tf_primary = str(tf_plan["tf_structure"])
        tfs_extra = (
            str(tf_plan["tf_exec"]),
            *tuple(tf_plan["tf_context"]),
        )
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

        # Case G: прокидуємо prev_wick_clusters (stateful tracker) в context.
        try:
            ctx = smc_input.context
            if not isinstance(ctx, dict):
                ctx = {}
                smc_input.context = ctx
            sym_norm = str(symbol).lower()
            prev_wc = _PREV_WICK_CLUSTERS_BY_SYMBOL.get(sym_norm)
            if isinstance(prev_wc, list) and prev_wc:
                ctx.setdefault("prev_wick_clusters", prev_wc)
        except Exception:
            pass

        tf_health = _build_tf_health(
            tf_plan=tf_plan,
            ohlc_by_tf=smc_input.ohlc_by_tf,
            store=store,
            symbol=symbol,
        )
        primary_frame = None
        try:
            primary_frame = smc_input.ohlc_by_tf.get(tf_primary)
        except Exception:
            primary_frame = None

        gates: list[dict[str, str]] = []
        history_state: str = "unknown"
        last_open_time_ms: int | None = None
        age_ms: int | None = None
        last_ts: str | None = None
        lag_ms: int | None = None
        bars_5m: int | None = None

        # Stage0 гейт: якщо немає 5m-даних — не рахуємо SMC-core.
        if primary_frame is None or getattr(primary_frame, "empty", True):
            history_state = "missing"
            gates.append(
                {
                    "code": "NO_5M_DATA",
                    "message": "Немає OHLCV по 5m — пропускаємо обчислення SMC.",
                }
            )
        else:
            bars = 0
            try:
                bars = int(len(primary_frame))
            except Exception:
                bars = 0
            bars_5m = bars

            # Мінімальна історія для старту compute: використовуємо runtime limit як target,
            # але не блокуємося — приймаємо половину як мінімум (best-effort).
            min_bars = max(5, int(limit) // 2)
            if bars < min_bars:
                history_state = "insufficient"
                gates.append(
                    {
                        "code": "INSUFFICIENT_5M",
                        "message": f"Замало 5m барів ({bars} < {min_bars}) — пропускаємо обчислення SMC.",
                    }
                )
            else:
                history_state = "ok"

            # Додатковий гейт stale_tail по 5m (узгоджено з S2).
            try:
                last_open_raw = primary_frame.iloc[-1].get(
                    "open_time"
                ) or primary_frame.iloc[-1].get("close_time")
            except Exception:
                last_open_raw = None
            try:
                if last_open_raw is None:
                    last_open_time_ms = None
                else:
                    val = float(last_open_raw)
                    last_open_time_ms = int(val) if val > 1e12 else int(val * 1000.0)
            except Exception:
                last_open_time_ms = None

            if last_open_time_ms is not None:
                now_ms = int(time.time() * 1000.0)
                age_ms = max(0, now_ms - int(last_open_time_ms))
                lag_ms = age_ms
                try:
                    last_ts = utc_ms_to_iso_z(int(last_open_time_ms))
                except Exception:
                    last_ts = None

            if last_open_time_ms is not None:
                try:
                    s2 = classify_history(
                        now_ms=int(time.time() * 1000.0),
                        bars_count=bars,
                        last_open_time_ms=last_open_time_ms,
                        min_history_bars=min_bars,
                        tf_ms=timeframe_to_ms(tf_primary) or 300_000,
                        stale_k=float(SMC_S2_STALE_K),
                    )
                except Exception:
                    s2 = None
                if s2 is not None and getattr(s2, "state", None) == "stale_tail":
                    history_state = "stale_tail"
                    gates.append(
                        {
                            "code": "STALE_5M",
                            "message": "5m хвіст протух (stale_tail) — пропускаємо обчислення SMC.",
                        }
                    )

        if gates:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            tf_effective: list[str] = []
            try:
                for tf, frame in (smc_input.ohlc_by_tf or {}).items():
                    if frame is not None and not getattr(frame, "empty", True):
                        tf_effective.append(str(tf))
            except Exception:
                tf_effective = []
            return SmcHint(
                structure=None,
                liquidity=None,
                zones=None,
                signals=[],
                meta={
                    "snapshot_tf": tf_primary,
                    "tf_plan": tf_plan,
                    "tf_effective": tf_effective,
                    "tf_health": tf_health,
                    "gates": gates,
                    "history_state": history_state,
                    "age_ms": age_ms,
                    "last_open_time_ms": last_open_time_ms,
                    "last_ts": last_ts,
                    "lag_ms": lag_ms,
                    "bars_5m": bars_5m,
                    "telemetry": {
                        "build_input_ms": round(elapsed_ms, 2),
                        "compute_ms": 0.0,
                    },
                },
            )

        compute_t0 = time.perf_counter()
        hint = engine.process_snapshot(smc_input)
        compute_ms = (time.perf_counter() - compute_t0) * 1000.0

        # Case G: оновлюємо prev_wick_clusters з close compute (best-effort).
        try:
            sym_norm = str(symbol).lower()
            liq = getattr(hint, "liquidity", None)
            meta = getattr(liq, "meta", None) if liq is not None else None
            wc = meta.get("wick_clusters") if isinstance(meta, dict) else None
            if isinstance(wc, list):
                _PREV_WICK_CLUSTERS_BY_SYMBOL[sym_norm] = [
                    dict(x) for x in wc if isinstance(x, dict)
                ]
        except Exception:
            pass
        # Додаємо Stage0 мету поверх meta з engine.
        try:
            tf_effective: list[str] = []
            for tf, frame in (smc_input.ohlc_by_tf or {}).items():
                if frame is not None and not getattr(frame, "empty", True):
                    tf_effective.append(str(tf))

            # Stage0 мета по 5m (факт/вік), навіть коли compute успішний.
            pframe = smc_input.ohlc_by_tf.get(tf_primary)
            bars = int(len(pframe)) if pframe is not None else 0
            last_open_time_ms = None
            try:
                if pframe is not None and not getattr(pframe, "empty", True):
                    last_open_raw = pframe.iloc[-1].get("open_time") or pframe.iloc[
                        -1
                    ].get("close_time")
                else:
                    last_open_raw = None
            except Exception:
                last_open_raw = None
            try:
                if last_open_raw is None:
                    last_open_time_ms = None
                else:
                    val = float(last_open_raw)
                    last_open_time_ms = int(val) if val > 1e12 else int(val * 1000.0)
            except Exception:
                last_open_time_ms = None
            age_ms = None
            last_ts = None
            lag_ms = None
            if last_open_time_ms is not None:
                now_ms = int(time.time() * 1000.0)
                age_ms = max(0, now_ms - int(last_open_time_ms))
                lag_ms = age_ms
                try:
                    last_ts = utc_ms_to_iso_z(int(last_open_time_ms))
                except Exception:
                    last_ts = None

            hint.meta = dict(hint.meta or {})
            hint.meta.update(
                {
                    "tf_plan": tf_plan,
                    "tf_effective": tf_effective,
                    "tf_health": tf_health,
                    "gates": [],
                    "history_state": "ok" if bars > 0 else "missing",
                    "age_ms": age_ms,
                    "last_open_time_ms": last_open_time_ms,
                    "last_ts": last_ts,
                    "lag_ms": lag_ms,
                    "bars_5m": bars,
                    "telemetry": {
                        "build_input_ms": round((compute_t0 - t0) * 1000.0, 2),
                        "compute_ms": round(compute_ms, 2),
                    },
                }
            )
        except Exception:
            pass
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
            stats: dict[str, Any] = {}

            # Навіть якщо OHLCV історії замало — хочемо показати останню ціну з тика.
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

            df = await store.get_df(sym, timeframe, limit=lookback)
            if df is None or df.empty:
                state_manager.update_asset(
                    sym,
                    {
                        "signal": "SMC_NO_OHLCV",
                        "state": ASSET_STATE["NORMAL"],
                        K_STATS: stats,
                        "hints": ["SMC: немає OHLCV — показуємо лише тики"],
                    },
                )
                continue

            ohlcv_frames_by_tf: dict[str, list[dict[str, Any]]] | None = None
            if SMC_VIEWER_OHLCV_FRAMES_BY_TF_ENABLED:
                try:
                    ohlcv_frames_by_tf = await _build_ohlcv_frames_by_tf_for_viewer(
                        symbol=sym,
                        store=store,
                    )
                except Exception:
                    ohlcv_frames_by_tf = None

            try:
                stats["current_price"] = float(df["close"].iloc[-1])
            except Exception:
                stats.setdefault("current_price", None)
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

            # Якщо історії замало — не намагаємось рахувати SMC hint, але публікуємо stats.
            if len(df) < max(5, lookback // 2):
                payload_any: dict[str, Any] = {
                    "signal": "SMC_WARMUP",
                    "state": ASSET_STATE["NORMAL"],
                    K_STATS: stats,
                    "hints": [
                        "SMC: недостатньо історії для підказок — очікуємо warmup"
                    ],
                }
                if isinstance(ohlcv_frames_by_tf, dict):
                    payload_any["ohlcv_frames_by_tf"] = ohlcv_frames_by_tf

                state_manager.update_asset(
                    sym,
                    payload_any,
                )
                continue

            t0 = time.perf_counter()
            smc_hint = await _build_smc_hint(symbol=sym, store=store)
            stats["smc_latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
            if smc_hint is None:
                payload_any = {
                    "signal": "SMC_PENDING",
                    "state": ASSET_STATE["NORMAL"],
                    K_STATS: stats,
                    "hints": ["SMC: очікуємо оновлення snapshot"],
                }
                if isinstance(ohlcv_frames_by_tf, dict):
                    payload_any["ohlcv_frames_by_tf"] = ohlcv_frames_by_tf

                state_manager.update_asset(
                    sym,
                    payload_any,
                )
                continue

            plain_serializer = _get_smc_plain_serializer()
            plain_hint = plain_serializer(smc_hint) if plain_serializer else smc_hint

            # UX/стабільність: не затираємо останній валідний SMC стан gated-empty hint'ом.
            try:
                prev_any = state_manager.state.get(sym, {}).get("smc_hint")
                prev_hint = prev_any if isinstance(prev_any, dict) else None
                merged_hint, preserved = _preserve_previous_hint_if_gated(
                    previous_hint=prev_hint,
                    new_hint=plain_hint if isinstance(plain_hint, dict) else None,
                )
                if preserved and isinstance(merged_hint, dict):
                    plain_hint = merged_hint
                    stats["smc_hint_preserved"] = True
            except Exception:
                pass

            # Stage6: анти-фліп/TTL сценарію (живе поза core).
            try:
                stage6_cfg = SMC_RUNTIME_PARAMS.get("stage6") or {}
                ttl_sec = int(stage6_cfg.get("ttl_sec", 180) or 180)
                confirm_bars = int(stage6_cfg.get("confirm_bars", 2) or 2)
                switch_delta = float(stage6_cfg.get("switch_delta", 0.08) or 0.08)

                micro_confirm_enabled = bool(
                    stage6_cfg.get("micro_confirm_enabled", True)
                )
                micro_ttl_sec = int(stage6_cfg.get("micro_ttl_sec", 90) or 90)
                micro_dmax_atr = float(stage6_cfg.get("micro_dmax_atr", 0.80) or 0.80)
                micro_boost = float(stage6_cfg.get("micro_boost", 0.05) or 0.05)
                micro_boost_partial = float(
                    stage6_cfg.get("micro_boost_partial", 0.02) or 0.02
                )
                stage6_stats = state_manager.apply_stage6_hysteresis(
                    sym,
                    plain_hint if isinstance(plain_hint, dict) else None,
                    ttl_sec=ttl_sec,
                    confirm_bars=confirm_bars,
                    switch_delta=switch_delta,
                    micro_confirm_enabled=micro_confirm_enabled,
                    micro_ttl_sec=micro_ttl_sec,
                    micro_dmax_atr=micro_dmax_atr,
                    micro_boost=micro_boost,
                    micro_boost_partial=micro_boost_partial,
                )
                if isinstance(stage6_stats, dict):
                    stats.update(stage6_stats)
            except Exception:
                # best-effort: Stage6 анти-фліп не має ламати hot-path
                pass

            payload_any = {
                "signal": "SMC_HINT",
                "state": ASSET_STATE["NORMAL"],
                K_STATS: stats,
                "smc_hint": plain_hint,
                "hints": [
                    (
                        "SMC: дані оновлено"
                        if not stats.get("smc_hint_preserved")
                        else "SMC: compute пропущено гейтами — показуємо останній відомий стан"
                    )
                ],
            }
            if isinstance(ohlcv_frames_by_tf, dict):
                payload_any["ohlcv_frames_by_tf"] = ohlcv_frames_by_tf

            state_manager.update_asset(
                sym,
                payload_any,
            )
        except Exception as exc:  # pragma: no cover - захист від edge-case
            logger.error("[SMC] Помилка обробки %s: %s", sym, exc, exc_info=True)
            err_payload = _create_error_signal(sym, str(exc))
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
    contract_min_bars: dict[str, int] | None = None,
) -> None:
    """Продюсер для SMC-пайплайна."""

    if not SMC_PIPELINE_ENABLED:
        logger.info("[SMC] Pipeline disabled флагом, task exit")
        return

    assets_current = [s.lower() for s in (assets or [])]
    state_manager = state_manager or SmcStateManager(assets_current)
    state_manager.set_cache_handler(store)

    contract_min_bars = contract_min_bars or {}

    # UX: тримаємо lookback у межах SMC runtime limit (типово 300),
    # щоб не блокуватися на великих contract min_history_bars.
    try:
        desired_limit = int(SMC_RUNTIME_PARAMS.get("limit", lookback) or lookback)
    except Exception:
        desired_limit = int(lookback)
    desired_limit = max(1, int(desired_limit))

    min_ready_assets = (
        max(1, int(len(assets_current) * min_ready_pct)) if assets_current else 1
    )
    bars_by_symbol: dict[str, int] = {sym: 0 for sym in assets_current}
    min_ready_bars_by_symbol: dict[str, int] = {}
    target_bars_by_symbol: dict[str, int] = {}
    pipeline_min_ready_bars = None
    pipeline_target_bars = None

    if assets_current:
        mins: list[int] = []
        targets: list[int] = []
        for sym in assets_current:
            contract_bars = int(contract_min_bars.get(sym, 0) or 0)
            if contract_bars > 0:
                min_bars = max(1, min(int(contract_bars), desired_limit))
            else:
                min_bars = desired_limit
            target_bars = desired_limit
            min_ready_bars_by_symbol[sym] = min_bars
            target_bars_by_symbol[sym] = target_bars
            mins.append(min_bars)
            targets.append(target_bars)
        pipeline_min_ready_bars = min(mins) if mins else None
        pipeline_target_bars = max(targets) if targets else None

    pipeline_meta = _build_pipeline_meta(
        assets_total=len(assets_current),
        ready_assets=0,
        min_ready=min_ready_assets,
        ready_assets_min=0,
        pipeline_min_ready_bars=pipeline_min_ready_bars,
        pipeline_target_bars=pipeline_target_bars,
    )
    pipeline_meta_last = dict(pipeline_meta)
    s2_meta_last: dict[str, Any] = {}

    _apply_local_pipeline_stats(
        state_manager=state_manager,
        bars_by_symbol=bars_by_symbol,
        min_ready_bars_by_symbol=min_ready_bars_by_symbol,
        target_bars_by_symbol=target_bars_by_symbol,
    )

    cycle_seq = 0
    await publish_smc_state(
        state_manager,
        store,
        redis_conn,
        meta_extra={
            "cycle_seq": cycle_seq,
            "cycle_started_ts": utc_now_human_utc(),
            "cycle_reason": "smc_bootstrap",
            **pipeline_meta,
        },
    )

    while True:
        cycle_seq += 1
        cycle_started_ts = time.time()

        try:
            fresh_symbols = await store_fast_symbols.get_fast_symbols()
            prev_set = set(assets_current)
            assets_current = _apply_fast_symbols_update(
                state_manager=state_manager,
                assets_current=assets_current,
                fresh_symbols=fresh_symbols,
            )
            new_set = set(assets_current)
            if prev_set != new_set:
                added = sorted(list(new_set - prev_set))
                removed = sorted(list(prev_set - new_set))
                logger.info(
                    "[SMC] Оновлено fast_symbols: додано=%s прибрано=%s",
                    added,
                    removed,
                )
        except Exception as exc:
            logger.debug("[SMC] Не вдалося оновити список активів: %s", exc)

        should_run, fxcm_reason = _should_run_smc_cycle_by_fxcm_status()
        if not should_run:
            await publish_smc_state(
                state_manager,
                store,
                redis_conn,
                meta_extra={
                    "cycle_seq": cycle_seq,
                    "cycle_started_ts": utc_now_human_utc(),
                    "cycle_reason": "smc_idle_fxcm_status",
                    "fxcm_idle_reason": fxcm_reason,
                    "pipeline_state": "IDLE",
                    **pipeline_meta_last,
                    **s2_meta_last,
                },
            )
            await asyncio.sleep(interval_sec)
            continue

        ready_assets: list[str] = []
        ready_symbols_min: list[str] = []
        bars_by_symbol = {}
        history_by_symbol: dict[str, dict[str, Any]] = {}
        min_ready_bars_by_symbol = {}
        target_bars_by_symbol = {}
        mins: list[int] = []
        targets: list[int] = []
        ready_assets_min_count = 0

        stale_k = float(SMC_S2_STALE_K)

        feed = get_fxcm_feed_state()
        market_state = str(
            getattr(feed, "market_state", "unknown") or "unknown"
        ).lower()
        ohlcv_state = str(getattr(feed, "ohlcv_state", "unknown") or "unknown").lower()
        allow_stale_tail = market_state != "open" or ohlcv_state in {"delayed", "down"}

        for symbol in assets_current:
            try:
                sym_norm = str(symbol).lower()
                contract_bars = int(contract_min_bars.get(sym_norm, 0) or 0)
                if contract_bars > 0:
                    min_bars = max(1, min(int(contract_bars), desired_limit))
                else:
                    min_bars = desired_limit
                target_bars = desired_limit

                min_ready_bars_by_symbol[sym_norm] = min_bars
                target_bars_by_symbol[sym_norm] = target_bars
                mins.append(min_bars)
                targets.append(target_bars)

                df_tmp = await store.get_df(symbol, timeframe, limit=target_bars)
                bars_count = int(len(df_tmp)) if df_tmp is not None else 0
                bars_by_symbol[sym_norm] = bars_count

                # S2: перевірка stale_tail (хвіст протух) поверх UDS.
                tf_ms = timeframe_to_ms(timeframe) or 60_000
                last_open_time_ms = None
                if df_tmp is not None and not df_tmp.empty:
                    try:
                        last_open_raw = df_tmp.iloc[-1].get("open_time") or df_tmp.iloc[
                            -1
                        ].get("close_time")
                    except Exception:
                        last_open_raw = None
                    # Heuristic: значення в UDS може бути у секундах або мс.
                    try:
                        if last_open_raw is None:
                            last_open_time_ms = None
                        else:
                            val = float(last_open_raw)
                            last_open_time_ms = (
                                int(val) if val > 1e12 else int(val * 1000.0)
                            )
                    except Exception:
                        last_open_time_ms = None

                s2 = classify_history(
                    now_ms=int(time.time() * 1000.0),
                    bars_count=bars_count,
                    last_open_time_ms=last_open_time_ms,
                    min_history_bars=min_bars,
                    tf_ms=tf_ms,
                    stale_k=stale_k,
                )

                history_by_symbol[sym_norm] = {
                    "history_state": s2.state,
                    "needs_warmup": s2.needs_warmup,
                    "needs_backfill": s2.needs_backfill,
                    "last_open_time_ms": last_open_time_ms,
                    "age_ms": s2.age_ms,
                }

                if bars_count >= min_bars and _history_ok_for_compute(
                    history_state=s2.state,
                    allow_stale_tail=allow_stale_tail,
                ):
                    ready_assets_min_count += 1
                    ready_symbols_min.append(sym_norm)
                if (
                    df_tmp is not None
                    and not df_tmp.empty
                    and bars_count >= target_bars
                    and _history_ok_for_compute(
                        history_state=s2.state,
                        allow_stale_tail=allow_stale_tail,
                    )
                ):
                    ready_assets.append(symbol)
            except Exception:
                bars_by_symbol[str(symbol).lower()] = 0
                continue

        # S2 summary для meta: покажемо, що саме заважає READY.
        s2_insufficient_assets = 0
        s2_stale_tail_assets = 0
        s2_unknown_assets = 0
        s2_ok_assets = 0
        active_symbol: str | None = None
        active_state: str | None = None
        active_age_ms: int | None = None
        for sym, hist in sorted(history_by_symbol.items()):
            state = str((hist or {}).get("history_state") or "unknown")
            if state == "stale_tail" and allow_stale_tail:
                # У вихідні/поза сесією stale_tail очікуваний за wall-clock критерієм.
                s2_ok_assets += 1
                continue
            if state == "ok":
                s2_ok_assets += 1
                continue
            if state == "stale_tail":
                s2_stale_tail_assets += 1
            elif state == "insufficient":
                s2_insufficient_assets += 1
            else:
                s2_unknown_assets += 1
            if active_symbol is None:
                active_symbol = sym
                active_state = state
                age_raw = hist.get("age_ms") if isinstance(hist, dict) else None
                if age_raw is None:
                    active_age_ms = None
                else:
                    try:
                        active_age_ms = int(age_raw)
                    except (TypeError, ValueError):
                        active_age_ms = None

        # Пріоритет активної проблеми: stale_tail > insufficient > unknown.
        if history_by_symbol:
            for sym, hist in sorted(history_by_symbol.items()):
                state = str((hist or {}).get("history_state") or "unknown")
                if state == "stale_tail" and not allow_stale_tail:
                    active_symbol = sym
                    active_state = state
                    age_raw = hist.get("age_ms") if isinstance(hist, dict) else None
                    if age_raw is None:
                        active_age_ms = None
                    else:
                        try:
                            active_age_ms = int(age_raw)
                        except (TypeError, ValueError):
                            active_age_ms = None
                    break
            if active_state != "stale_tail":
                for sym, hist in sorted(history_by_symbol.items()):
                    state = str((hist or {}).get("history_state") or "unknown")
                    if state == "insufficient":
                        active_symbol = sym
                        active_state = state
                        age_raw = hist.get("age_ms") if isinstance(hist, dict) else None
                        if age_raw is None:
                            active_age_ms = None
                        else:
                            try:
                                active_age_ms = int(age_raw)
                            except (TypeError, ValueError):
                                active_age_ms = None
                        break

        s2_meta = {
            "s2_ok_assets": int(s2_ok_assets),
            "s2_insufficient_assets": int(s2_insufficient_assets),
            "s2_stale_tail_assets": int(s2_stale_tail_assets),
            "s2_unknown_assets": int(s2_unknown_assets),
            "s2_active_symbol": active_symbol,
            "s2_active_state": active_state,
            "s2_active_age_ms": active_age_ms,
            "s2_stale_k": stale_k,
            "s2_stale_tail_expected": bool(allow_stale_tail),
        }
        s2_meta_last = dict(s2_meta)

        min_ready_assets = (
            max(1, int(len(assets_current) * min_ready_pct)) if assets_current else 1
        )
        pipeline_min_ready_bars = min(mins) if mins else None
        pipeline_target_bars = max(targets) if targets else None

        # Вимога UX: не блокуємо SMC на S2 "insufficient/stale_tail".
        # Навіть коли OHLCV недостатньо, ми все одно публікуємо стан (зокрема last price з тика).
        selected_symbols, skipped_symbols = _select_symbols_for_cycle(
            ready_symbols=[str(s).lower() for s in assets_current],
            max_per_cycle=SMC_MAX_ASSETS_PER_CYCLE,
        )

        if skipped_symbols:
            logger.warning(
                "[SMC] cycle=%d capacity_guard: ready_min=%d processed=%d skipped=%d max_per_cycle=%d",
                cycle_seq,
                len(ready_symbols_min),
                len(selected_symbols),
                len(skipped_symbols),
                SMC_MAX_ASSETS_PER_CYCLE,
            )

        tasks: list[asyncio.Task[Any]] = []
        for i in range(0, len(selected_symbols), SMC_BATCH_SIZE):
            batch = selected_symbols[i : i + SMC_BATCH_SIZE]
            batch_lookback = max(
                int(target_bars_by_symbol.get(sym, lookback) or lookback)
                for sym in batch
            )
            tasks.append(
                asyncio.create_task(
                    process_smc_batch(
                        batch,
                        store,
                        state_manager,
                        timeframe=timeframe,
                        lookback=batch_lookback,
                    )
                )
            )
        if tasks:
            await asyncio.gather(*tasks)

        _apply_local_pipeline_stats(
            state_manager=state_manager,
            bars_by_symbol=bars_by_symbol,
            min_ready_bars_by_symbol=min_ready_bars_by_symbol,
            target_bars_by_symbol=target_bars_by_symbol,
        )

        cycle_ready_ts = time.time()
        pipeline_meta = _build_pipeline_meta(
            assets_total=len(assets_current),
            ready_assets=len(ready_assets),
            min_ready=min_ready_assets,
            ready_assets_min=ready_assets_min_count,
            pipeline_min_ready_bars=pipeline_min_ready_bars,
            pipeline_target_bars=pipeline_target_bars,
        )
        capacity_meta = _build_capacity_meta(
            ready_assets=len(ready_symbols_min),
            processed_assets=len(selected_symbols),
        )
        pipeline_meta_last = dict(pipeline_meta)
        await publish_smc_state(
            state_manager,
            store,
            redis_conn,
            meta_extra={
                "cycle_seq": cycle_seq,
                "cycle_started_ts": utc_seconds_to_human_utc(cycle_started_ts),
                "cycle_ready_ts": utc_seconds_to_human_utc(cycle_ready_ts),
                "cycle_compute_ms": round(
                    (cycle_ready_ts - cycle_started_ts) * 1000.0, 2
                ),
                "cycle_duration_ms": round(
                    (cycle_ready_ts - cycle_started_ts) * 1000.0, 2
                ),
                "cycle_reason": "smc_screening",
                **pipeline_meta,
                **capacity_meta,
                **s2_meta,
            },
        )

        # Легкий лог по циклу (без Prometheus — метрики підключувані окремо).
        duration_ms = (cycle_ready_ts - cycle_started_ts) * 1000.0
        budget_ms = int(SMC_CYCLE_BUDGET_MS)
        budget_note = "" if duration_ms <= budget_ms else " (budget exceeded)"
        logger.debug(
            "[SMC] cycle=%d ready_min=%d ready_target=%d processed=%d skipped=%d duration_ms=%.2f%s",
            cycle_seq,
            len(ready_symbols_min),
            len(ready_assets),
            len(selected_symbols),
            len(skipped_symbols),
            duration_ms,
            budget_note,
        )

        elapsed = time.time() - cycle_started_ts
        sleep_time = (
            max(1, int(interval_sec - elapsed)) if elapsed < interval_sec else 1
        )
        await asyncio.sleep(sleep_time)
