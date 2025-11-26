"""Тести нормалізації часу в SmcExperimentalViewer."""

from __future__ import annotations

from datetime import UTC, datetime

from UI.experimental_viewer import SmcExperimentalViewer


def _ts_ms(year: int, month: int, day: int, hour: int, minute: int) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp() * 1000)


def test_coerce_iso_ts_handles_milliseconds(tmp_path) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    ms_value = _ts_ms(2025, 11, 25, 10, 30)

    iso = viewer._coerce_iso_ts(ms_value)

    assert iso == "2025-11-25 10:30"


def test_coerce_iso_ts_rejects_small_epoch_values(tmp_path) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))

    assert viewer._coerce_iso_ts(29) is None
    assert viewer._coerce_iso_ts(1_000) is None


def test_normalize_time_value_skips_epoch_iso_strings(tmp_path) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))

    assert viewer._normalize_time_value("1970-01-01 00:29") is None


def test_build_state_normalizes_all_structure_times(tmp_path) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    base_ms = _ts_ms(2025, 11, 25, 10, 30)
    asset = {
        "symbol": "xauusd",
        "stats": {
            "current_price": 100.0,
            "session_tag": "LONDON",
            "session_seq": 7,
            "session_start_ts": base_ms,
            "session_end_ts": base_ms + 3_600_000,
        },
        "smc": {
            "structure": {
                "trend": "UP",
                "bias": "LONG",
                "range_state": "INSIDE",
                "events": [
                    {
                        "event_type": "BOS",
                        "direction": "LONG",
                        "price_level": 101.0,
                        "ts": base_ms + 120_000,
                    }
                ],
                "legs": [
                    {
                        "label": "L1",
                        "from_swing": {
                            "price": 99.5,
                            "ts": base_ms,
                        },
                        "to_swing": {
                            "price": 101.2,
                            "ts": base_ms + 60_000,
                        },
                    }
                ],
                "swings": [
                    {
                        "kind": "HIGH",
                        "price": 101.2,
                        "ts": base_ms + 60_000,
                    }
                ],
                "ranges": [
                    {
                        "high": 102.0,
                        "low": 99.0,
                        "state": "INSIDE",
                        "start_ts": base_ms,
                        "end_ts": base_ms + 300_000,
                    }
                ],
                "ote_zones": [],
            },
            "liquidity": {"amd_phase": None, "pools": [], "magnets": []},
        },
    }
    payload_meta = {"ts": base_ms + 600_000, "seq": 1}

    state = viewer.build_state(asset, payload_meta)

    swing_time = state["structure"]["swings"][0]["time"]
    range_start = state["structure"]["ranges"][0]["start"]
    leg_start = state["structure"]["legs"][0]["from_time"]
    event_time = state["structure"]["events"][0]["time"]
    session_start = state["sessions"][0]["start_ts"]

    assert swing_time == "2025-11-25 10:31"
    assert range_start == "2025-11-25 10:30"
    assert leg_start == "2025-11-25 10:30"
    assert event_time == "2025-11-25 10:32"
    assert session_start == "2025-11-25 10:30"
