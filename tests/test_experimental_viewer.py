"""Перевірки побудови стану для SmcExperimentalViewer."""

import copy

import pytest
from rich.panel import Panel

from UI.experimental_viewer import SmcExperimentalViewer
from UI.experimental_viewer_extended import SmcExperimentalViewerExtended


def _sample_asset() -> dict:
    return {
        "symbol": "xauusd",
        "stats": {"current_price": 2375.5, "session_tag": "LONDON"},
        "smc": {
            "structure": {
                "trend": "UP",
                "bias": "LONG",
                "range_state": "INSIDE",
                "events": [
                    {
                        "event_type": "BOS",
                        "direction": "LONG",
                        "price_level": 2376.0,
                        "time": "2025-11-25T12:00:00Z",
                    }
                ],
                "legs": [
                    {
                        "label": "HH",
                        "from_swing": {"price": 2370.0, "time": "2025-11-25T11:45:00Z"},
                        "to_swing": {"price": 2378.0, "time": "2025-11-25T12:05:00Z"},
                    }
                ],
                "swings": [
                    {"kind": "HIGH", "price": 2378.0, "time": "2025-11-25T12:05:00Z"}
                ],
                "ranges": [
                    {
                        "high": 2385.0,
                        "low": 2360.0,
                        "state": "INSIDE",
                        "start_time": "2025-11-25T08:00:00Z",
                        "end_time": None,
                    }
                ],
                "ote_zones": [
                    {
                        "direction": "LONG",
                        "role": "PRIMARY",
                        "ote_min": 2368.0,
                        "ote_max": 2372.0,
                    }
                ],
            },
            "liquidity": {
                "amd_phase": "ACCUMULATION",
                "pools": [
                    {
                        "level": 2379.0,
                        "liq_type": "EQH",
                        "role": "PRIMARY",
                        "strength": 0.72,
                    }
                ],
                "magnets": [
                    {
                        "price_min": 2370.0,
                        "price_max": 2380.0,
                        "role": "PRIMARY",
                    }
                ],
            },
            "zones": {
                "zones": [
                    {
                        "zone_type": "ORDER_BLOCK",
                        "price_min": 2371.0,
                        "price_max": 2373.0,
                        "entry_hint": 2372.0,
                        "role": "PRIMARY",
                        "strength": 4.2,
                        "quality": "MEDIUM",
                    }
                ],
                "active_zones": [],
                "meta": {},
            },
        },
    }


def test_build_state_returns_compact_sections(tmp_path) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    asset = _sample_asset()
    payload_meta = {"ts": "2025-11-25T12:10:00Z", "seq": 42}

    state = viewer.build_state(asset, payload_meta)

    assert state["structure"]["bias"] == "LONG"
    assert state["liquidity"]["pools"][0]["liq_type"] == "EQH"
    assert "fxcm" in state
    assert state["payload_seq"] == 42

    viewer.dump_snapshot(state)
    assert viewer.snapshot_path.exists()


def test_extended_viewer_renders_with_heatmap(tmp_path) -> None:
    viewer = SmcExperimentalViewerExtended("xauusd", snapshot_dir=str(tmp_path))
    asset = _sample_asset()
    payload_meta = {"ts": "2025-11-25T12:10:00Z", "seq": 99}

    state = viewer.build_state(asset, payload_meta)
    panel = viewer.render_panel(state)

    assert isinstance(panel, Panel)


def test_price_fallbacks_to_price_str(tmp_path) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    asset = _sample_asset()
    asset["stats"].pop("current_price", None)
    asset["price_str"] = "2 345.67 USD"
    payload_meta = {"ts": "2025-11-25T12:10:00Z", "seq": 1}

    state = viewer.build_state(asset, payload_meta)

    assert state["price"] == pytest.approx(2345.67)


def test_price_parses_stats_string(tmp_path) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    asset = _sample_asset()
    asset["stats"].pop("current_price", None)
    asset.pop("price_str", None)
    asset["stats"]["price_str"] = "≈ 1 234,50 EUR"
    payload_meta = {"ts": "2025-11-25T12:10:00Z", "seq": 2}

    state = viewer.build_state(asset, payload_meta)

    assert state["price"] == pytest.approx(1234.5)


def test_price_keeps_last_known_value(tmp_path) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    asset = _sample_asset()
    payload_meta = {"ts": "2025-11-25T12:10:00Z", "seq": 3}

    baseline = viewer.build_state(asset, payload_meta)
    assert baseline["price"] == pytest.approx(2375.5)

    asset["stats"].pop("current_price", None)
    asset.pop("price_str", None)
    second = viewer.build_state(asset, payload_meta)

    assert second["price"] == pytest.approx(2375.5)


def test_session_cached_when_missing(tmp_path) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    asset = _sample_asset()
    payload_meta = {"ts": "2025-11-25T12:10:00Z", "seq": 4}

    first = viewer.build_state(asset, payload_meta)
    assert first["session"] == "LONDON"

    asset["stats"].pop("session_tag", None)
    second = viewer.build_state(asset, payload_meta)

    assert second["session"] == "LONDON"


def test_schema_uses_schema_version_and_caches(tmp_path) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    asset = _sample_asset()
    meta_with_schema = {
        "ts": "2025-11-25T12:10:00Z",
        "seq": 5,
        "schema_version": "1.7",
    }
    state = viewer.build_state(asset, meta_with_schema)
    assert state["schema"] == "1.7"

    meta_without_schema = {"ts": "2025-11-25T12:11:00Z", "seq": 6}
    next_state = viewer.build_state(asset, meta_without_schema)
    assert next_state["schema"] == "1.7"


def test_events_persist_when_source_empty(tmp_path) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    asset = _sample_asset()
    payload_meta = {"ts": "2025-11-25T12:10:00Z", "seq": 7}

    first = viewer.build_state(asset, payload_meta)
    assert len(first["structure"]["events"]) == 1

    asset_without_events = copy.deepcopy(asset)
    asset_without_events["smc"]["structure"]["events"] = []
    second = viewer.build_state(asset_without_events, payload_meta)

    assert len(second["structure"]["events"]) == 1
