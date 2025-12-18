"""Тести для UI_v2.viewer_state_builder.

Перевіряємо:
- базову побудову SmcViewerState;
- бекфіл подій/зон через ViewerStateCache;
- пріоритет FXCM-блоку над meta.fxcm і вплив на session.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from core.contracts.viewer_state import (
    VIEWER_STATE_SCHEMA_VERSION,
    FxcmMeta,
    SmcViewerState,
    UiSmcAssetPayload,
    UiSmcMeta,
)
from UI_v2.viewer_state_builder import (
    ViewerStateCache,
    build_viewer_state,
)


def _make_basic_asset(**overrides: Any) -> UiSmcAssetPayload:
    """Формує мінімальний UiSmcAssetPayload для тестів."""

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
            "legs": [
                {
                    "label": "L1",
                    "direction": "up",
                    "from_index": 10,
                    "to_index": 20,
                    "strength": 0.8,
                }
            ],
            "swings": [
                {
                    "kind": "HH",
                    "price": 2410.0,
                    "time": "2025-12-08T07:55:00+00:00",
                }
            ],
            "ranges": [
                {
                    "high": 2415.0,
                    "low": 2400.0,
                    "state": "inside",
                    "start_time": "2025-12-08T07:00:00+00:00",
                    "end_time": "2025-12-08T08:00:00+00:00",
                }
            ],
            "events": [
                {
                    "event_type": "BOS_UP",
                    "direction": "up",
                    "price": 2412.5,
                    "time": 1_701_721_500_000,
                    "status": "confirmed",
                }
            ],
            "ote_zones": [
                {
                    "direction": "up",
                    "role": "primary",
                    "ote_min": 0.62,
                    "ote_max": 0.79,
                }
            ],
        },
        "smc_liquidity": {
            "amd_phase": "MANIP",
            "pools": [
                {
                    "level": 2415.0,
                    "liq_type": "EQH",
                    "role": "target",
                    "strength": 0.9,
                    "meta": {},
                }
            ],
            "magnets": [
                {
                    "kind": "FVG",
                    "level": 2413.0,
                    "meta": {},
                }
            ],
        },
        "smc_zones": {
            "zones": [
                {
                    "kind": "OB",
                    "direction": "up",
                    "price_min": 2408.0,
                    "price_max": 2410.0,
                }
            ]
        },
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
        "seq": 123,
        "schema_version": "smc_state_v1",
    }
    base.update(overrides)  # type: ignore
    return base


def test_build_viewer_state_basic() -> None:
    """Базовий сценарій: будуємо SmcViewerState з повного asset + meta."""

    asset = _make_basic_asset()
    meta = _make_basic_meta()

    state: SmcViewerState = build_viewer_state(asset, meta, fxcm_block=None, cache=None)

    assert state["symbol"] == "XAUUSD"  # type: ignore
    assert state["payload_ts"] == meta["ts"]  # type: ignore
    assert state["payload_seq"] == meta["seq"]  # type: ignore
    assert state["schema"] == VIEWER_STATE_SCHEMA_VERSION  # type: ignore
    assert state["price"] == pytest.approx(2412.5)  # type: ignore
    assert state["session"] == "London"  # type: ignore

    structure = cast(dict, state["structure"])  # type: ignore
    assert structure["trend"] == "up"
    assert structure["bias"] == "long"
    assert structure["range_state"] == "dev_up"
    assert isinstance(structure["legs"], list) and structure["legs"]
    assert isinstance(structure["swings"], list) and structure["swings"]
    assert isinstance(structure["ranges"], list) and structure["ranges"]
    assert isinstance(structure["events"], list) and structure["events"]
    assert isinstance(structure["ote_zones"], list) and structure["ote_zones"]

    liquidity = cast(dict, state["liquidity"])  # type: ignore
    assert liquidity["amd_phase"] == "MANIP"
    assert isinstance(liquidity["pools"], list) and liquidity["pools"]
    assert isinstance(liquidity["magnets"], list) and liquidity["magnets"]
    first_pool = cast(dict, liquidity["pools"][0])
    assert first_pool["price"] == pytest.approx(2415.0)
    assert first_pool["type"] == "EQH"
    assert first_pool["liq_type"] == "EQH"

    zones = cast(dict, state["zones"])  # type: ignore
    assert "raw" in zones
    raw_zones = cast(dict, zones["raw"])
    assert "zones" in raw_zones
    assert isinstance(raw_zones["zones"], list) and raw_zones["zones"]

    assert "fxcm" not in state or state["fxcm"] is None


def test_build_viewer_state_includes_pipeline_local_from_stats() -> None:
    asset = _make_basic_asset(
        stats={
            "session_tag": "London",
            "current_price": 2412.5,
            "pipeline_state_local": "WARMUP",
            "pipeline_ready_bars": 120,
            "pipeline_required_bars": 200,
            "pipeline_ready_ratio": 0.6,
        }
    )
    meta = _make_basic_meta()

    state: SmcViewerState = build_viewer_state(asset, meta, fxcm_block=None, cache=None)

    pipeline_local = cast(dict, state.get("pipeline_local"))
    assert pipeline_local["state"] == "WARMUP"
    assert pipeline_local["ready_bars"] == 120
    assert pipeline_local["required_bars"] == 200
    assert pipeline_local["ready_ratio"] == pytest.approx(0.6)


def test_build_viewer_state_cache_backfills_events_and_zones() -> None:
    """Кеш має бекфілити події та зони, якщо в новому пейлоаді їх немає."""

    cache = ViewerStateCache()
    asset_with_events = _make_basic_asset()
    meta1 = _make_basic_meta(seq=1)

    state1 = build_viewer_state(asset_with_events, meta1, fxcm_block=None, cache=cache)

    events1 = state1["structure"]["events"]  # type: ignore[index]
    zones1 = state1["zones"]["raw"]  # type: ignore[index]

    assert events1
    assert zones1

    asset_without_events = _make_basic_asset(
        smc_structure={
            "trend": "up",
            "bias": "long",
            "range_state": "dev_up",
        },
        smc_liquidity={"amd_phase": "MANIP", "pools": [], "magnets": []},
        smc_zones={},
    )
    meta2 = _make_basic_meta(seq=2)

    state2 = build_viewer_state(
        asset_without_events, meta2, fxcm_block=None, cache=cache
    )

    events2 = state2["structure"]["events"]  # type: ignore[index]
    zones2 = state2["zones"]["raw"]  # type: ignore[index]

    assert events2 == events1
    assert zones2 == zones1


def test_build_viewer_state_fxcm_priority_and_session_override() -> None:
    """FXCM-блок має пріоритет і може переписувати session."""

    asset = _make_basic_asset()
    meta = _make_basic_meta(
        fxcm={
            "market_state": "closed",
            "process_state": "idle",
            "price_state": "stale",
            "ohlcv_state": "idle",
            "lag_seconds": 1.5,
            "last_bar_close_utc": "2025-12-08T07:59:00+00:00",
            "next_open_utc": "2025-12-09T00:00:00+00:00",
            "session": {
                "tag": "Asia",
                "name": "Asia",
                "next_open_utc": "2025-12-09T00:00:00+00:00",
                "seconds_to_open": 0.0,
                "seconds_to_close": 3600.0,
            },
        }
    )

    fxcm_block: FxcmMeta = {
        "market_state": "open",
        "process_state": "streaming",
        "price_state": "live",
        "ohlcv_state": "streaming",
        "lag_seconds": 0.3,
        "last_bar_close_utc": "2025-12-08T08:04:00+00:00",
        "next_open_utc": "2025-12-09T00:00:00+00:00",
        "session": {
            "tag": "London",
            "name": "London",
            "next_open_utc": "2025-12-09T00:00:00+00:00",
            "seconds_to_open": 0.0,
            "seconds_to_close": 7200.0,
        },
    }

    cache = ViewerStateCache()

    state = build_viewer_state(asset, meta, fxcm_block=fxcm_block, cache=cache)

    fxcm_state = cast(dict, state["fxcm"])  # type: ignore
    assert fxcm_state["market_state"] == "open"
    assert fxcm_state["process_state"] == "streaming"
    assert state["session"] == "London"  # type: ignore
    assert cache.last_fxcm_meta == fxcm_block


def test_build_viewer_state_preserves_pipeline_meta() -> None:
    """Pipeline-поля з meta мають доходити до viewer_state.meta."""

    asset = _make_basic_asset()
    meta = _make_basic_meta(
        pipeline_state="WARMUP",
        pipeline_ready_assets=2,
        pipeline_min_ready=3,
        pipeline_assets_total=5,
        pipeline_ready_pct=0.4,
    )

    state: SmcViewerState = build_viewer_state(asset, meta, fxcm_block=None, cache=None)

    meta_block = cast(dict[str, Any], state["meta"])  # type: ignore
    assert meta_block["pipeline_state"] == "WARMUP"
    assert meta_block["pipeline_ready_assets"] == 2
    assert meta_block["pipeline_min_ready"] == 3
    assert meta_block["pipeline_assets_total"] == 5
    assert meta_block["pipeline_ready_pct"] == 0.4
