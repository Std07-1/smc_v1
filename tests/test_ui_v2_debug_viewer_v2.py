"""Тести для UI_v2.debug_viewer_v2 (pure-хелпери)."""

from __future__ import annotations

from typing import Any

from rich.panel import Panel

from UI_v2.debug_viewer_v2 import (
    DebugViewerState,
    _apply_snapshot_payload,
    _render_layout,
)
from UI_v2.rich_viewer_extended import SmcRichViewerExtended


def _make_viewer_state(symbol: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "price": 2412.5,
        "session": "LDN",
        "payload_ts": "2025-12-08T08:05:00+00:00",
        "payload_seq": 1,
        "schema": "smc_viewer_v1",
        "meta": {"ts": "2025-12-08T08:05:00+00:00", "seq": 1},
        "structure": {
            "trend": "up",
            "bias": "long",
            "range_state": "dev_up",
            "legs": [],
            "swings": [],
            "ranges": [],
            "events": [],
            "ote_zones": [],
        },
        "liquidity": {
            "amd_phase": "MANIP",
            "pools": [],
            "magnets": [],
        },
        "zones": {"raw": {"zones": []}},
    }


def test_apply_snapshot_payload_filters_to_known_symbols() -> None:
    viewer_state = DebugViewerState(symbols=["XAUUSD", "EURUSD"])
    snapshot = {
        "XAUUSD": _make_viewer_state("XAUUSD"),
        "GBPUSD": _make_viewer_state("GBPUSD"),
    }

    _apply_snapshot_payload(snapshot, viewer_state)

    assert set(viewer_state.states_by_symbol.keys()) == {"XAUUSD"}


def test_render_layout_returns_panel_for_available_state() -> None:
    viewer_state = DebugViewerState(symbols=["XAUUSD"])
    viewer_state.states_by_symbol["XAUUSD"] = _make_viewer_state("XAUUSD")  # type: ignore[index]
    renderer = SmcRichViewerExtended()

    layout = _render_layout(viewer_state, renderer)

    assert isinstance(layout, Panel)


def test_render_layout_placeholder_when_missing_state() -> None:
    viewer_state = DebugViewerState(symbols=["XAUUSD"])
    renderer = SmcRichViewerExtended()

    layout = _render_layout(viewer_state, renderer)

    assert isinstance(layout, Panel)
