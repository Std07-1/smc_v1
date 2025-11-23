"""Тести для офлайнової утиліти аналізу порогів BOS/CHOCH."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from tools import smc_structure_threshold_study as study


def _sample_snapshot() -> dict:
    legs = [
        {
            "from_swing": {"index": 0, "price": 100.0, "kind": "LOW"},
            "to_swing": {"index": 1, "price": 110.0, "kind": "HIGH"},
            "label": "HH",
        },
        {
            "from_swing": {"index": 1, "price": 110.0, "kind": "HIGH"},
            "to_swing": {"index": 2, "price": 90.0, "kind": "LOW"},
            "label": "LL",
        },
    ]
    return {
        "structure": {
            "legs": legs,
            "events": [
                {
                    "event_type": "BOS",
                    "direction": "SHORT",
                    "price_level": 90.0,
                    "time": "2025-01-01T00:10:00Z",
                    "source_leg": legs[1],
                }
            ],
            "meta": {
                "symbol": "xauusd",
                "tf_input": "5m",
                "snapshot_start_ts": "2025-01-01T00:00:00Z",
                "snapshot_end_ts": "2025-01-01T00:15:00Z",
                "atr_available": True,
                "atr_last": 5.0,
                "atr_median": 5.0,
            },
        }
    }


def test_threshold_study_generates_csv(tmp_path: Path) -> None:
    snapshot = _sample_snapshot()
    input_path = tmp_path / "snapshot.json"
    input_path.write_text(json.dumps(snapshot), encoding="utf-8")
    out_path = tmp_path / "stats.csv"

    study.main(["--inputs", str(input_path), "--out", str(out_path)])

    assert out_path.exists()
    with out_path.open(encoding="utf-8") as handle:
        reader = list(csv.DictReader(handle))

    assert len(reader) == len(snapshot["structure"]["legs"])

    event_row = next(row for row in reader if row["leg_index"] == "1")
    assert event_row["is_event_leg"] == "True"
    assert event_row["event_type"] == "BOS"
    assert event_row["passes_loose_atr"] == "True"
    assert event_row["passes_tight_atr"] == "True"
    assert event_row["passes_loose_pct"] == "True"
    assert event_row["passes_tight_pct"] == "True"

    control_row = next(row for row in reader if row["leg_index"] == "0")
    assert control_row["is_event_leg"] == "False"
    assert control_row["passes_loose_atr"] == "True"
    assert control_row["passes_tight_atr"] == "True"
