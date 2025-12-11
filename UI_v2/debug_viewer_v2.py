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
from dataclasses import dataclass, field
from typing import Any, Mapping

try:  # pragma: no cover - опційна залежність у runtime
    from redis.asyncio import Redis
except Exception:  # pragma: no cover
    Redis = Any  # type: ignore[assignment]

from rich.columns import Columns
from rich.console import Console
from rich.live import Live
from rich.panel import Panel

from app.settings import settings
from config.config import (
    REDIS_CHANNEL_SMC_VIEWER_EXTENDED,
    REDIS_SNAPSHOT_KEY_SMC_VIEWER,
    UI_V2_DEBUG_VIEWER_SYMBOLS,
)
from UI_v2.rich_viewer_extended import SmcRichViewerExtended
from UI_v2.schemas import SmcViewerState

logger = logging.getLogger("ui_v2.debug_viewer_v2")


@dataclass(slots=True)
class DebugViewerState:
    """Поточний стан debug viewer-а (список символів + кешовані стейти)."""

    symbols: list[str]
    states_by_symbol: dict[str, SmcViewerState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.symbols = [_normalize_symbol(sym) for sym in self.symbols if sym]


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
            panels.append(renderer.render_panel(state))
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


async def _load_initial_snapshot(redis: Redis, viewer_state: DebugViewerState) -> None:
    try:
        raw = await redis.get(REDIS_SNAPSHOT_KEY_SMC_VIEWER)
    except Exception:
        logger.warning(
            "[UI_v2 debug viewer] Не вдалося прочитати snapshot", exc_info=True
        )
        return
    snapshot = _parse_snapshot(raw)
    _apply_snapshot_payload(snapshot, viewer_state)


async def _listen_updates(redis: Redis, viewer_state: DebugViewerState) -> None:
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


async def run_debug_viewer_v2() -> None:
    console = Console(force_terminal=True)
    renderer = SmcRichViewerExtended()
    redis = _create_redis_client()
    viewer_state = DebugViewerState(symbols=UI_V2_DEBUG_VIEWER_SYMBOLS)
    await _load_initial_snapshot(redis, viewer_state)

    listener = asyncio.create_task(_listen_updates(redis, viewer_state))
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
        try:
            await listener
        except asyncio.CancelledError:
            pass
        await redis.close()


def main() -> None:
    asyncio.run(run_debug_viewer_v2())


if __name__ == "__main__":  # pragma: no cover
    main()
