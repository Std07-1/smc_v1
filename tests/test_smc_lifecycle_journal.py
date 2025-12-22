"""Тести для SMC Lifecycle Journal.

Мета цих тестів — зафіксувати базову поведінку diff/touch/reason без залежності
від повного SMC пайплайна.
"""

from __future__ import annotations

from smc_core.lifecycle_journal import (
    BarSnapshot,
    SmcLifecycleJournal,
    build_frame_record,
    extract_active_ids_from_hint,
)


def test_journal_created_and_removed_invalidated_rule() -> None:
    j = SmcLifecycleJournal(symbol="XAUUSD", tf="5m")

    hint1 = {
        "zones": {
            "zones": [
                {
                    "zone_id": "z1",
                    "zone_type": "ORDER_BLOCK",
                    "direction": "LONG",
                    "role": "DEMAND",
                    "price_min": 100.0,
                    "price_max": 110.0,
                }
            ]
        }
    }

    ev1 = j.process_snapshot(
        hint=hint1,
        now_ms=1_000,
        bar=BarSnapshot(open=100, high=101, low=99, close=100, close_time_ms=1_000),
    )
    assert any(e["event"] == "created" and e["id"] == "z1" for e in ev1)

    hint2 = {"zones": {"zones": []}}
    ev2 = j.process_snapshot(
        hint=hint2,
        now_ms=2_000,
        bar=BarSnapshot(open=100, high=101, low=99, close=100, close_time_ms=2_000),
    )
    removed = [e for e in ev2 if e["event"] == "removed" and e["id"] == "z1"]
    assert removed
    assert removed[0]["ctx"]["reason"] == "invalidated_rule"


def test_journal_touched_emits_on_second_snapshot() -> None:
    j = SmcLifecycleJournal(symbol="XAUUSD", tf="5m")

    hint = {
        "zones": {
            "zones": [
                {
                    "zone_id": "z_touch",
                    "zone_type": "FVG",
                    "direction": "LONG",
                    "role": "DEMAND",
                    "price_min": 100.0,
                    "price_max": 110.0,
                }
            ]
        }
    }

    ev1 = j.process_snapshot(
        hint=hint,
        now_ms=1_000,
        bar=BarSnapshot(open=90, high=95, low=85, close=92, close_time_ms=1_000),
    )
    assert any(e["event"] == "created" and e["id"] == "z_touch" for e in ev1)
    assert not any(e["event"] == "touched" and e["id"] == "z_touch" for e in ev1)

    # Другий крок: зона вже існує => touched може згенеритись.
    ev2 = j.process_snapshot(
        hint=hint,
        now_ms=2_000,
        bar=BarSnapshot(open=95, high=112, low=90, close=105, close_time_ms=2_000),
    )
    touched = [e for e in ev2 if e["event"] == "touched" and e["id"] == "z_touch"]
    assert touched
    assert touched[0]["ctx"]["touch_type"] == "close"
    assert touched[0]["ctx"]["late"] is False


def test_journal_touched_respects_touch_epsilon() -> None:
    j = SmcLifecycleJournal(symbol="XAUUSD", tf="5m")

    # eps=1.0 розширює межі до [99..111]
    hint = {
        "zones": {
            "meta": {"touch_epsilon": 1.0},
            "zones": [
                {
                    "zone_id": "z_eps",
                    "zone_type": "FVG",
                    "direction": "LONG",
                    "role": "DEMAND",
                    "price_min": 100.0,
                    "price_max": 110.0,
                }
            ],
        }
    }

    _ = j.process_snapshot(
        hint=hint,
        now_ms=1_000,
        bar=BarSnapshot(open=90, high=95, low=85, close=92, close_time_ms=1_000),
    )

    # Без eps це не touch (high=99.2 < 100.0), але з eps=1.0 => touch.
    ev2 = j.process_snapshot(
        hint=hint,
        now_ms=2_000,
        bar=BarSnapshot(
            open=98.0, high=99.2, low=97.5, close=98.8, close_time_ms=2_000
        ),
    )
    touched = [e for e in ev2 if e["event"] == "touched" and e["id"] == "z_eps"]
    assert touched


def test_journal_removed_reason_replaced_by_merge() -> None:
    j = SmcLifecycleJournal(symbol="XAUUSD", tf="5m")

    hint1 = {
        "zones": {
            "zones": [
                {
                    "zone_id": "z_old",
                    "zone_type": "FVG",
                    "direction": "LONG",
                    "role": "DEMAND",
                    "price_min": 100.0,
                    "price_max": 110.0,
                }
            ]
        }
    }

    _ = j.process_snapshot(
        hint=hint1,
        now_ms=1_000,
        bar=BarSnapshot(open=100, high=101, low=99, close=100, close_time_ms=1_000),
    )

    hint2 = {
        "zones": {
            "zones": [
                {
                    "zone_id": "z_new",
                    "zone_type": "FVG",
                    "direction": "LONG",
                    "role": "DEMAND",
                    "price_min": 100.0,
                    "price_max": 110.0,
                    "meta": {"merged_from": ["z_old"]},
                }
            ]
        }
    }

    ev2 = j.process_snapshot(
        hint=hint2,
        now_ms=2_000,
        bar=BarSnapshot(open=100, high=101, low=99, close=100, close_time_ms=2_000),
    )

    removed = [e for e in ev2 if e["event"] == "removed" and e["id"] == "z_old"]
    assert removed
    assert removed[0]["ctx"]["reason"] == "replaced_by_merge"


def test_extract_active_ids_and_build_frame_record() -> None:
    hint = {
        "zones": {
            "zones": [{"zone_id": "z1"}, {"zone_id": "z2"}],
            "active_zones": [
                {"zone_id": "z1", "price_min": 0.0, "price_max": 2.0},
                {"zone_id": "z2", "price_min": 1.0, "price_max": 3.0},
            ],
        },
        "liquidity": {
            "pools": [
                {
                    "liq_type": "EQH",
                    "role": "BUY",
                    "level": 100.0,
                    "first_time": "t1",
                    "last_time": "t2",
                }
            ],
            "magnets": [
                {
                    "liq_type": "EQL",
                    "role": "SELL",
                    "center": 105.0,
                    "price_min": 104.0,
                    "price_max": 106.0,
                }
            ],
        },
    }

    ids = extract_active_ids_from_hint(hint)
    assert ids["zone"] == {"z1", "z2"}
    assert len(ids["pool"]) == 1
    assert len(ids["magnet"]) == 1
    assert "structure_event" in ids
    assert "active_range" in ids
    assert "range_state" in ids
    assert "ote" in ids
    assert "amd_phase" in ids
    assert "wick_cluster" in ids

    fr = build_frame_record(
        symbol="XAUUSD",
        tf="5m",
        now_ms=1_700_000,
        kind="preview",
        primary_close_ms=1_800_000,
        bar_complete=False,
        hint=hint,
    )
    assert fr["symbol"] == "XAUUSD"
    assert fr["tf"] == "5m"
    assert fr["kind"] == "preview"
    assert fr["primary_close_ms"] == 1_800_000
    assert fr["bar_complete"] is False
    assert fr["counts"]["zone"] == 2
    assert fr["active_ids"]["zone"] == ["z1", "z2"]
    assert fr["counts"]["structure_event"] == 0
    assert fr["counts"]["wick_cluster"] == 0

    overlap = fr.get("zone_overlap_active")
    assert isinstance(overlap, dict)
    assert overlap.get("n_active") == 2
    assert overlap.get("total_pairs") == 1
    pairs = overlap.get("pairs_iou_ge")
    assert isinstance(pairs, dict)
    assert pairs.get("0.2") == 1
    assert pairs.get("0.4") == 0
    assert pairs.get("0.6") == 0
