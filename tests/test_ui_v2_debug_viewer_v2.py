"""Тести для UI_v2.debug_viewer_v2 (pure-хелпери)."""

from __future__ import annotations

from typing import Any

from rich.panel import Panel

from UI_v2.debug_viewer_v2 import (
    DebugViewerState,
    OhlcvDebugState,
    _apply_snapshot_payload,
    _parse_fxcm_ohlcv_message,
    _render_layout,
    _update_ohlcv_debug_from_stream,
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


def test_parse_fxcm_ohlcv_message_accepts_bytes_json() -> None:
    raw = b'{"symbol":"XAUUSD","tf":"5m","bars":[{"open_time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0, "complete": false}]}'

    parsed = _parse_fxcm_ohlcv_message(raw)

    assert parsed is not None
    symbol, tf, bars = parsed
    assert symbol == "XAUUSD"
    assert tf == "5m"
    assert len(bars) == 1


def test_update_ohlcv_debug_tracks_live_and_synthetic_window() -> None:
    state = OhlcvDebugState(symbol="XAUUSD", tf="5m", limit=10)
    now_ms = 10_000

    bars = [
        {
            "open_time": 5_000,
            "close_time": 8_000,
            "complete": False,
            "synthetic": False,
        },
        {"open_time": 0, "close_time": 4_000, "complete": True, "synthetic": True},
        {"open_time": 5_000, "close_time": 9_000, "complete": True, "synthetic": False},
    ]

    _update_ohlcv_debug_from_stream(state, tf="5m", bars=bars, now_ms=now_ms)

    assert state.live_bar is not None
    assert state.live_bar["complete"] is False
    assert state.synthetic_total_60m == 2
    assert state.synthetic_synth_60m == 1
