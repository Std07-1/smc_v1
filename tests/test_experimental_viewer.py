"""Перевірки побудови стану для SmcExperimentalViewer."""

from rich.panel import Panel

from UI.experimental_viewer import SmcExperimentalViewer


def _sample_asset() -> dict:
    return {
        "symbol": "xauusd",
        "stats": {
            "current_price": 2375.5,
            "session_tag": "LONDON",
            "session_seq": 128,
            "session_start_ts": "2025-11-25T08:00:00Z",
            "session_end_ts": "2025-11-25T15:59:00Z",
        },
        "smc": {
            "structure": {
                "trend": "UP",
                "bias": "LONG",
                "range_state": "INSIDE",
                "meta": {
                    "snapshot_start_ts": "2025-11-25T07:00:00Z",
                    "snapshot_end_ts": "2025-11-25T12:10:00Z",
                    "last_choch_ts": "2025-11-25T11:55:00Z",
                },
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
        },
    }


def test_build_state_returns_compact_sections(tmp_path) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    asset = _sample_asset()
    payload_meta = {"ts": "2025-11-25T12:10:00Z", "seq": 42}

    state = viewer.build_state(asset, payload_meta)

    assert state["structure"]["bias"] == "LONG"
    assert state["liquidity"]["pools"][0]["liq_type"] == "EQH"
    assert state["payload_seq"] == 42
    assert state["sessions"][0]["label"] == "LONDON"
    assert state["structure"]["meta"]["session_id"] == "128"

    viewer.dump_snapshot(state)
    assert viewer.snapshot_path.exists()


def test_render_panel_builds_extended_layout(tmp_path) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    state = viewer.build_state(_sample_asset(), {"ts": "2025-11-25", "seq": 1})

    panel = viewer.render_panel(state)

    assert isinstance(panel, Panel)
    assert str(panel.title) == "SMC Experimental Viewer · XAUUSD"


def test_render_panel_handles_empty_sections(tmp_path) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    empty_asset = {"symbol": "xauusd", "stats": {}, "smc": {}}
    state = viewer.build_state(empty_asset, {"ts": None, "seq": None})

    panel = viewer.render_panel(state)

    assert isinstance(panel, Panel)
