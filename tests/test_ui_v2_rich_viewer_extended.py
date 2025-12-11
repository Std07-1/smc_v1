"""Smoke-тести для SmcRichViewerExtended.

Перевіряємо, що рендерер працює на типовому SmcViewerState.
"""

from __future__ import annotations

from typing import Any

from rich.panel import Panel

from UI_v2.rich_viewer_extended import SmcRichViewerExtended
from UI_v2.schemas import SmcViewerState, UiSmcAssetPayload, UiSmcMeta
from UI_v2.viewer_state_builder import ViewerStateCache, build_viewer_state


def _make_basic_asset(**overrides: Any) -> UiSmcAssetPayload:
    base: UiSmcAssetPayload = {
        "symbol": "XAUUSD",
        "stats": {
            "session_tag": "London",
            "current_price": 2412.5,
        },
        "smc_hint": {
            "structure": {},
            "liquidity": {},
            "zones": {},
            "signals": [],
            "meta": {},
        },
        "smc_structure": {
            "trend": "up",
            "bias": "long",
            "range_state": "dev_up",
            "legs": [],
            "swings": [],
            "ranges": [],
            "events": [],
            "ote_zones": [],
        },
        "smc_liquidity": {
            "amd_phase": "MANIP",
            "pools": [],
            "magnets": [],
        },
        "smc_zones": {"zones": []},
        "price": 2412.5,
        "price_str": "2412.5",
        "live_price_mid": 2412.5,
        "live_price_mid_str": "2412.5",
        "live_price_bid": 2412.4,
        "live_price_bid_str": "2412.4",
        "live_price_ask": 2412.6,
        "live_price_ask_str": "2412.6",
        "live_price_spread": 0.2,
    }
    base.update(overrides)  # type: ignore
    return base


def _make_basic_meta(**overrides: Any) -> UiSmcMeta:
    base: UiSmcMeta = {
        "ts": "2025-12-08T08:05:00+00:00",
        "seq": 1,
        "schema_version": "smc_state_v1",
    }
    base.update(overrides)  # type: ignore
    return base


def test_extended_viewer_smoke() -> None:
    """Без винятків повертає Panel і виводить символ у заголовку."""

    asset = _make_basic_asset()
    meta = _make_basic_meta()
    cache = ViewerStateCache()

    state: SmcViewerState = build_viewer_state(
        asset, meta, fxcm_block=None, cache=cache
    )

    viewer = SmcRichViewerExtended()
    panel = viewer.render_panel(state)

    assert isinstance(panel, Panel)
    if panel.title is not None:
        title_plain = getattr(panel.title, "plain", str(panel.title))
        assert "XAUUSD" in title_plain
