"""Тести для UI_v2.smc_viewer_broadcaster (pure-шар).

Перевіряємо:
- коректну побудову viewer_states із UiSmcStatePayload;
- коректну обробку кількох активів;
- ігнорування активів без symbol.
"""

from __future__ import annotations

from typing import Any

from UI_v2.schemas import (
    SmcViewerState,
    UiSmcAssetPayload,
    UiSmcMeta,
    UiSmcStatePayload,
)
from UI_v2.smc_viewer_broadcaster import build_viewer_states_from_payload
from UI_v2.viewer_state_builder import ViewerStateCache


def _make_basic_asset(symbol: str, **overrides: Any) -> UiSmcAssetPayload:
    base: UiSmcAssetPayload = {
        "symbol": symbol,
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


def _make_meta(**overrides: Any) -> UiSmcMeta:
    base: UiSmcMeta = {
        "ts": "2025-12-08T08:05:00+00:00",
        "seq": 1,
        "schema_version": "smc_state_v1",
    }
    base.update(overrides)  # type: ignore
    return base


def test_build_viewer_states_from_payload_two_assets() -> None:
    """Два активи -> дві записи у viewer_states, ключі за symbol."""
    asset1 = _make_basic_asset("XAUUSD")
    asset2 = _make_basic_asset("EURUSD")
    meta = _make_meta()
    cache_by_symbol: dict[str, ViewerStateCache] = {}

    payload: UiSmcStatePayload = {
        "type": "smc_state",
        "meta": meta,
        "counters": {"assets": 2},
        "assets": [asset1, asset2],
        "fxcm": None,  # type: ignore
        "analytics": {},
    }

    viewer_states = build_viewer_states_from_payload(payload, cache_by_symbol)

    assert set(viewer_states.keys()) == {"XAUUSD", "EURUSD"}

    xau_state: SmcViewerState = viewer_states["XAUUSD"]
    eur_state: SmcViewerState = viewer_states["EURUSD"]

    assert xau_state["symbol"] == "XAUUSD"  # type: ignore
    assert eur_state["symbol"] == "EURUSD"  # type: ignore
    # Переконуємось, що базові поля meta потрапили у стейт.
    assert xau_state["payload_ts"] == meta["ts"]  # type: ignore
    assert xau_state["payload_seq"] == meta["seq"]  # type: ignore


def test_build_viewer_states_skips_assets_without_symbol() -> None:
    """Актив без symbol пропускається й не потрапляє у viewer_states."""
    good_asset = _make_basic_asset("XAUUSD")
    bad_asset = _make_basic_asset("")
    bad_asset["symbol"] = None  # type: ignore
    meta = _make_meta()
    cache_by_symbol: dict[str, ViewerStateCache] = {}

    payload: UiSmcStatePayload = {
        "type": "smc_state",
        "meta": meta,
        "counters": {"assets": 2},
        "assets": [good_asset, bad_asset],
        "fxcm": None,  # type: ignore
        "analytics": {},
    }

    viewer_states = build_viewer_states_from_payload(payload, cache_by_symbol)

    assert set(viewer_states.keys()) == {"XAUUSD"}
    assert viewer_states["XAUUSD"]["symbol"] == "XAUUSD"  # type: ignore
