"""Тести для QA-звіту по lifecycle journal: конкретні приклади (B/C/D/F) + audit_todo.

Це тести інструмента `tools/smc_journal_report.py`.
Фокус: зацементувати вимірювання (приклади для replay) без зміни логіки детектора.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tools.smc_journal_report import (
    _collect_case_b_removed_then_late_touch_examples,
    _collect_case_c_short_lifetime_examples,
    _collect_case_d_widest_zone_examples,
    _collect_case_f_missed_touch_examples,
    _Row,
    _write_audit_todo_md,
)


def test_case_b_removed_then_late_touch_examples_has_bars_to_touch() -> None:
    dt_touch = datetime.fromtimestamp(900_000 / 1000.0, tz=UTC)  # +15m

    rows = [
        _Row(
            dt=dt_touch,
            symbol="XAUUSD",
            tf="5m",
            entity="zone",
            event="touched",
            id="z1",
            type="FVG",
            direction="bull",
            role=None,
            price_min=1.0,
            price_max=2.0,
            level=None,
            ctx={
                "late": True,
                "removed_ms": 0,
                "primary_close_ms": 900_000,
                "removed_reason": "invalidated_rule",
                "removed_reason_sub": "rebucket_time_window",
                "touch_type": "wick",
            },
        )
    ]

    ex = _collect_case_b_removed_then_late_touch_examples(rows)
    assert len(ex) == 1
    assert ex[0]["id"] == "z1"
    assert ex[0]["bars_to_touch"] == "3"  # 15m / 5m
    assert ex[0]["removed_reason_sub"] == "rebucket_time_window"


def test_case_c_short_lifetime_examples_filters_lifetime_bars_le_1() -> None:
    dt = datetime(2025, 1, 1, tzinfo=UTC)

    rows = [
        _Row(
            dt=dt,
            symbol="XAUUSD",
            tf="5m",
            entity="zone",
            event="removed",
            id="z1",
            type="FVG",
            direction="bull",
            role=None,
            price_min=1.0,
            price_max=2.0,
            level=None,
            ctx={
                "lifetime_bars": 1,
                "primary_close_ms": 123,
                "reason": "invalidated_rule",
                "reason_sub": "too_small",
            },
        ),
        _Row(
            dt=dt,
            symbol="XAUUSD",
            tf="5m",
            entity="zone",
            event="removed",
            id="z2",
            type="FVG",
            direction="bull",
            role=None,
            price_min=1.0,
            price_max=2.0,
            level=None,
            ctx={
                "lifetime_bars": 2,
                "primary_close_ms": 124,
                "reason": "invalidated_rule",
                "reason_sub": "too_small",
            },
        ),
    ]

    ex = _collect_case_c_short_lifetime_examples(rows, lifetime_le=1)
    assert [x["id"] for x in ex] == ["z1"]


def test_case_d_widest_zone_examples_computes_span_atr() -> None:
    dt = datetime(2025, 1, 1, tzinfo=UTC)

    rows = [
        _Row(
            dt=dt,
            symbol="XAUUSD",
            tf="5m",
            entity="zone",
            event="created",
            id="z1",
            type="OB",
            direction="bear",
            role=None,
            price_min=10.0,
            price_max=16.0,
            level=None,
            ctx={"atr_last": 2.0, "primary_close_ms": 555, "compute_kind": "close"},
        )
    ]

    ex = _collect_case_d_widest_zone_examples(rows)
    assert len(ex) == 1
    assert ex[0]["id"] == "z1"
    assert ex[0]["span_atr"] == "3.000"  # (16-10)/2


def test_case_f_missed_touch_examples_detects_should_touch_but_no_journal_touch() -> (
    None
):
    dt_created = datetime.fromtimestamp(0 / 1000.0, tz=UTC)
    dt_removed = datetime.fromtimestamp(900_000 / 1000.0, tz=UTC)  # +15m

    rows = [
        _Row(
            dt=dt_created,
            symbol="XAUUSD",
            tf="5m",
            entity="zone",
            event="created",
            id="z1",
            type="FVG",
            direction="bull",
            role=None,
            price_min=10.0,
            price_max=12.0,
            level=None,
            ctx={"compute_kind": "close"},
        ),
        _Row(
            dt=dt_removed,
            symbol="XAUUSD",
            tf="5m",
            entity="zone",
            event="removed",
            id="z1",
            type="FVG",
            direction="bull",
            role=None,
            price_min=10.0,
            price_max=12.0,
            level=None,
            ctx={"reason": "invalidated_rule"},
        ),
    ]

    close_ms = [300_000, 600_000, 900_000]  # 5m, 10m, 15m
    lows = [11.0, 13.0, 13.0]
    highs = [11.5, 13.5, 13.5]

    ex = _collect_case_f_missed_touch_examples(
        rows,
        close_ms=close_ms,
        lows=lows,
        highs=highs,
    )

    assert len(ex) == 1
    assert ex[0]["id"] == "z1"
    assert ex[0]["first_touch_close_ms"] == "300000"


def test_write_audit_todo_md_writes_markdown_table(tmp_path) -> None:
    path = tmp_path / "audit_todo.md"
    _write_audit_todo_md(
        path,
        [
            {
                "case": "B",
                "dt_utc": "2025-01-01T00:00:00Z",
                "symbol": "XAUUSD",
                "tf": "5m",
                "primary_close_ms": "123",
                "entity": "zone",
                "id": "z1",
                "note": "late_touch",
            }
        ],
    )

    txt = path.read_text(encoding="utf-8")
    assert "# audit_todo" in txt
    assert "| case |" in txt
    assert "| B |" in txt
