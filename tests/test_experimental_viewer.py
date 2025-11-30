"""Перевірки побудови стану для SmcExperimentalViewer."""

from UI.experimental_viewer import SmcExperimentalViewer


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
