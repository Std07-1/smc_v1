"""Debug viewer для UI_v2, що читає SmcViewerState з Redis snapshot + каналу.

Призначення:
- завантажити поточний snapshot `REDIS_SNAPSHOT_KEY_SMC_VIEWER`;
- підписатися на канал `REDIS_CHANNEL_SMC_VIEWER_EXTENDED` для live-оновлень;
- відрендерити стан через `SmcRichViewerExtended` для символів із конфігу.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

try:  # pragma: no cover - опційна залежність у runtime
    from redis.asyncio import Redis
except Exception:  # pragma: no cover
    Redis = Any  # type: ignore[assignment]

from rich.columns import Columns
from rich.console import Console
from rich.live import Live
from rich.panel import Panel

from app.settings import load_datastore_cfg, settings
from config.config import (
    REDIS_CHANNEL_SMC_VIEWER_EXTENDED,
    REDIS_SNAPSHOT_KEY_SMC_VIEWER,
    UI_V2_DEBUG_VIEWER_SYMBOLS,
)
from data.unified_store import StoreConfig, StoreProfile, UnifiedDataStore
from UI_v2.rich_viewer_extended import SmcRichViewerExtended
from UI_v2.schemas import SmcViewerState

logger = logging.getLogger("ui_v2.debug_viewer_v2")


FXCM_OHLCV_CHANNEL: str = "fxcm:ohlcv"
DEFAULT_OHLCV_TF: str = "5m"
DEFAULT_OHLCV_LIMIT: int = 24
SYNTHETIC_WINDOW_MS: int = 60 * 60 * 1000
OHLCV_POLL_SECONDS: float = 2.0


@dataclass(slots=True)
class DebugViewerState:
    """Поточний стан debug viewer-а (список символів + кешовані стейти)."""

    symbols: list[str]
    states_by_symbol: dict[str, SmcViewerState] = field(default_factory=dict)
    ohlcv_by_symbol: dict[str, OhlcvDebugState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.symbols = [_normalize_symbol(sym) for sym in self.symbols if sym]
        for sym in self.symbols:
            self.ohlcv_by_symbol.setdefault(sym, OhlcvDebugState(symbol=sym))


@dataclass(slots=True)
class OhlcvDebugState:
    """Локальний OHLCV-стан для debug viewer (complete+live+synth window)."""

    symbol: str
    tf: str = DEFAULT_OHLCV_TF
    limit: int = DEFAULT_OHLCV_LIMIT
    complete_bars: list[dict[str, Any]] = field(default_factory=list)
    live_bar: dict[str, Any] | None = None
    # (close_ms, synthetic)
    synthetic_window: deque[tuple[int, bool]] = field(default_factory=deque)
    synthetic_total_60m: int = 0
    synthetic_synth_60m: int = 0


def _normalize_symbol(symbol: str | None) -> str:
    return str(symbol or "").upper()


def _create_redis_client() -> Redis:  # type: ignore[return-type]
    return Redis(  # type: ignore[call-arg]
        host=settings.redis_host,
        port=settings.redis_port,
        decode_responses=False,
    )


def _apply_snapshot_payload(
    snapshot: Mapping[str, Any],
    viewer_state: DebugViewerState,
) -> None:
    for symbol in viewer_state.symbols:
        state = snapshot.get(symbol)
        if isinstance(state, Mapping):
            viewer_state.states_by_symbol[symbol] = dict(state)  # type: ignore[arg-type]


def _render_layout(
    viewer_state: DebugViewerState,
    renderer: SmcRichViewerExtended,
) -> Panel | Columns:
    panels = []
    for symbol in viewer_state.symbols:
        state = viewer_state.states_by_symbol.get(symbol)
        if state:
            patched = dict(state)
            ohlcv_state = viewer_state.ohlcv_by_symbol.get(symbol)
            if ohlcv_state is not None:
                patched["ohlcv_debug"] = _export_ohlcv_debug_payload(ohlcv_state)
            panels.append(renderer.render_panel(cast(SmcViewerState, patched)))
        else:
            panels.append(
                Panel(
                    f"Очікуємо дані для {symbol}",
                    title=f"{symbol}",
                    border_style="yellow",
                )
            )
    if not panels:
        return Panel("Символи для відображення не задані", border_style="red")
    if len(panels) == 1:
        return panels[0]
    return Columns(panels, expand=True)


def _parse_snapshot(raw: bytes | str | None) -> Mapping[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except Exception:
        logger.warning(
            "[UI_v2 debug viewer] Некоректний JSON у snapshot", exc_info=True
        )
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _parse_update_message(data: Any) -> tuple[str, SmcViewerState] | None:
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            logger.debug(
                "[UI_v2 debug viewer] Не вдалося розпарсити update", exc_info=True
            )
            return None
    if not isinstance(data, Mapping):
        return None
    symbol = _normalize_symbol(data.get("symbol"))
    viewer_state = data.get("viewer_state")
    if not symbol or not isinstance(viewer_state, Mapping):
        return None
    return symbol, viewer_state  # type: ignore[return-value]


def _parse_fxcm_ohlcv_message(
    data: Any,
) -> tuple[str, str, list[Mapping[str, Any]]] | None:
    """Парсить повідомлення з каналу fxcm:ohlcv.

    Очікуємо JSON: {"symbol": "XAUUSD", "tf": "5m", "bars": [ ... ]}
    """

    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except Exception:
            return None
    if not isinstance(data, Mapping):
        return None

    symbol = _normalize_symbol(data.get("symbol"))
    tf = str(data.get("tf") or data.get("timeframe") or "").strip().lower()
    bars = data.get("bars")
    if not symbol or not tf or not isinstance(bars, list):
        return None

    safe_bars: list[Mapping[str, Any]] = [b for b in bars if isinstance(b, Mapping)]
    if not safe_bars:
        return None
    return symbol, tf, safe_bars


def _update_ohlcv_debug_from_stream(
    state: OhlcvDebugState,
    *,
    tf: str,
    bars: list[Mapping[str, Any]],
    now_ms: int,
) -> None:
    if tf != state.tf:
        return

    for bar in bars:
        open_ms = int(bar.get("open_time") or 0)
        close_ms = int(bar.get("close_time") or open_ms or 0)
        complete = bar.get("complete", True) is not False
        synthetic = bar.get("synthetic") is True

        normalized = {
            "open_time": open_ms,
            "close_time": close_ms,
            "open": bar.get("open"),
            "high": bar.get("high"),
            "low": bar.get("low"),
            "close": bar.get("close"),
            "volume": bar.get("volume"),
            "complete": complete,
            "synthetic": synthetic,
        }

        if not complete:
            state.live_bar = normalized
            continue

        state.synthetic_window.append((close_ms or now_ms, synthetic))

    # prune window
    cutoff = now_ms - SYNTHETIC_WINDOW_MS
    while state.synthetic_window and state.synthetic_window[0][0] < cutoff:
        state.synthetic_window.popleft()

    total = len(state.synthetic_window)
    synth = sum(1 for _ts, is_synth in state.synthetic_window if is_synth)
    state.synthetic_total_60m = total
    state.synthetic_synth_60m = synth


def _export_ohlcv_debug_payload(state: OhlcvDebugState) -> dict[str, Any]:
    total = int(state.synthetic_total_60m or 0)
    synth = int(state.synthetic_synth_60m or 0)
    pct = (float(synth) / float(total) * 100.0) if total else 0.0
    return {
        "tf": state.tf,
        "limit": state.limit,
        "complete_bars": list(state.complete_bars),
        "live_bar": dict(state.live_bar) if state.live_bar else None,
        "synthetic_60m_total": total,
        "synthetic_60m_synth": synth,
        "synthetic_60m_pct": pct,
    }


async def _load_initial_snapshot(redis: Any, viewer_state: DebugViewerState) -> None:
    try:
        raw = await redis.get(REDIS_SNAPSHOT_KEY_SMC_VIEWER)
    except Exception:
        logger.warning(
            "[UI_v2 debug viewer] Не вдалося прочитати snapshot", exc_info=True
        )
        return
    snapshot = _parse_snapshot(raw)
    _apply_snapshot_payload(snapshot, viewer_state)


async def _listen_updates(redis: Any, viewer_state: DebugViewerState) -> None:
    pubsub = redis.pubsub()
    await pubsub.subscribe(REDIS_CHANNEL_SMC_VIEWER_EXTENDED)
    try:
        async for message in pubsub.listen():
            if not isinstance(message, Mapping):
                continue
            if message.get("type") != "message":
                continue
            parsed = _parse_update_message(message.get("data"))
            if parsed is None:
                continue
            symbol, state = parsed
            if symbol in viewer_state.symbols:
                viewer_state.states_by_symbol[symbol] = state
    except asyncio.CancelledError:
        raise
    finally:
        try:
            await pubsub.unsubscribe(REDIS_CHANNEL_SMC_VIEWER_EXTENDED)
        except Exception:
            pass
        await pubsub.close()


async def _listen_fxcm_ohlcv(redis: Any, viewer_state: DebugViewerState) -> None:
    pubsub = redis.pubsub()
    await pubsub.subscribe(FXCM_OHLCV_CHANNEL)
    try:
        async for message in pubsub.listen():
            if not isinstance(message, Mapping):
                continue
            if message.get("type") != "message":
                continue
            parsed = _parse_fxcm_ohlcv_message(message.get("data"))
            if parsed is None:
                continue
            symbol, tf, bars = parsed
            state = viewer_state.ohlcv_by_symbol.get(symbol)
            if state is None:
                continue
            now_ms = int(time.time() * 1000)
            _update_ohlcv_debug_from_stream(state, tf=tf, bars=bars, now_ms=now_ms)
    except asyncio.CancelledError:
        raise
    finally:
        try:
            await pubsub.unsubscribe(FXCM_OHLCV_CHANNEL)
        except Exception:
            pass
        await pubsub.close()


def _build_store(redis: Any) -> UnifiedDataStore:
    cfg = load_datastore_cfg()
    try:
        profile_data = cfg.profile.model_dump()
    except Exception:
        profile_data = cfg.profile.dict()
    store_cfg = StoreConfig(
        namespace=cfg.namespace,
        base_dir=cfg.base_dir,
        profile=StoreProfile(**profile_data),
        intervals_ttl=cfg.intervals_ttl,
        write_behind=cfg.write_behind,
        validate_on_read=cfg.validate_on_read,
        validate_on_write=cfg.validate_on_write,
        io_retry_attempts=cfg.io_retry_attempts,
        io_retry_backoff=cfg.io_retry_backoff,
    )
    return UnifiedDataStore(redis=redis, cfg=store_cfg)


async def _poll_complete_ohlcv(
    store: UnifiedDataStore, viewer_state: DebugViewerState
) -> None:
    while True:
        for symbol in viewer_state.symbols:
            ohlcv_state = viewer_state.ohlcv_by_symbol.get(symbol)
            if ohlcv_state is None:
                continue
            try:
                df = await store.get_df(symbol, ohlcv_state.tf, limit=ohlcv_state.limit)
            except Exception:
                logger.debug(
                    "[UI_v2 debug viewer] Не вдалося прочитати OHLCV з UDS: %s %s",
                    symbol,
                    ohlcv_state.tf,
                    exc_info=True,
                )
                continue
            if df is None or df.empty:
                ohlcv_state.complete_bars = []
                continue
            # Беремо тільки потрібні колонки й мінімізуємо payload для рендера.
            rows = df.tail(ohlcv_state.limit).to_dict(orient="records")
            ohlcv_state.complete_bars = [
                {
                    "open_time": int(row.get("open_time") or 0),
                    "close_time": int(row.get("close_time") or 0),
                    "open": row.get("open"),
                    "high": row.get("high"),
                    "low": row.get("low"),
                    "close": row.get("close"),
                    "volume": row.get("volume"),
                    "complete": True,
                }
                for row in rows
            ]
        await asyncio.sleep(OHLCV_POLL_SECONDS)


async def run_debug_viewer_v2() -> None:
    console = Console(force_terminal=True)
    renderer = SmcRichViewerExtended()
    redis = _create_redis_client()
    viewer_state = DebugViewerState(symbols=UI_V2_DEBUG_VIEWER_SYMBOLS)
    await _load_initial_snapshot(redis, viewer_state)

    store = _build_store(redis)
    await store.start_maintenance()

    listener = asyncio.create_task(_listen_updates(redis, viewer_state))
    fxcm_listener = asyncio.create_task(_listen_fxcm_ohlcv(redis, viewer_state))
    ohlcv_poller = asyncio.create_task(_poll_complete_ohlcv(store, viewer_state))
    refresh_interval = 0.5
    try:
        with Live(
            _render_layout(viewer_state, renderer),
            console=console,
            refresh_per_second=max(1, int(1 / refresh_interval)),
            screen=False,
        ) as live:
            while True:
                live.update(_render_layout(viewer_state, renderer), refresh=True)
                await asyncio.sleep(refresh_interval)
    except asyncio.CancelledError:
        raise
    except KeyboardInterrupt:
        logger.info("[UI_v2 debug viewer] Завершено користувачем")
    finally:
        listener.cancel()
        fxcm_listener.cancel()
        ohlcv_poller.cancel()
        try:
            await listener
        except asyncio.CancelledError:
            pass
        try:
            await fxcm_listener
        except asyncio.CancelledError:
            pass
        try:
            await ohlcv_poller
        except asyncio.CancelledError:
            pass
        await store.stop_maintenance()
        await redis.close()


def main() -> None:
    asyncio.run(run_debug_viewer_v2())


if __name__ == "__main__":  # pragma: no cover
    main()
