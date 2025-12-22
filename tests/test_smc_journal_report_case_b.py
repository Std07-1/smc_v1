"""Тести для QA-звіту по lifecycle journal (випадок B).

Фокус: evicted_then_touched_rate_by_reason_sub — rate touched_late / removed
по (entity, reason, reason_sub).
"""

from __future__ import annotations

from datetime import UTC, datetime

from tools.smc_journal_report import _report_evicted_then_touched_by_reason_sub, _Row


def test_evicted_then_touched_rate_by_reason_sub_zone_vs_pool() -> None:
    dt = datetime(2025, 1, 1, tzinfo=UTC)

    rows = [
        # zone: 2 removed із reason_sub=rebucket_time_window, 1 late touch
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
            ctx={"reason": "invalidated_rule", "reason_sub": "rebucket_time_window"},
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
            ctx={"reason": "invalidated_rule", "reason_sub": "rebucket_time_window"},
        ),
        _Row(
            dt=dt,
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
                "removed_reason": "invalidated_rule",
                "removed_reason_sub": "rebucket_time_window",
            },
        ),
        # pool: 1 removed із reason_sub=context_flip, 1 late touch
        _Row(
            dt=dt,
            symbol="XAUUSD",
            tf="5m",
            entity="pool",
            event="removed",
            id="p1",
            type="EQ",
            direction=None,
            role="BUY",
            price_min=None,
            price_max=None,
            level=10.0,
            ctx={"reason": "invalidated_rule", "reason_sub": "context_flip"},
        ),
        _Row(
            dt=dt,
            symbol="XAUUSD",
            tf="5m",
            entity="pool",
            event="touched",
            id="p1",
            type="EQ",
            direction=None,
            role="BUY",
            price_min=None,
            price_max=None,
            level=10.0,
            ctx={
                "late": True,
                "removed_reason": "invalidated_rule",
                "removed_reason_sub": "context_flip",
            },
        ),
        # Інші entity мають ігноруватись дефолтним фільтром
        _Row(
            dt=dt,
            symbol="XAUUSD",
            tf="5m",
            entity="magnet",
            event="removed",
            id="m1",
            type=None,
            direction=None,
            role=None,
            price_min=None,
            price_max=None,
            level=None,
            ctx={"reason": "invalidated_rule", "reason_sub": "rebucket_time_window"},
        ),
        _Row(
            dt=dt,
            symbol="XAUUSD",
            tf="5m",
            entity="magnet",
            event="touched",
            id="m1",
            type=None,
            direction=None,
            role=None,
            price_min=None,
            price_max=None,
            level=None,
            ctx={
                "late": True,
                "removed_reason": "invalidated_rule",
                "removed_reason_sub": "rebucket_time_window",
            },
        ),
    ]

    headers, data = _report_evicted_then_touched_by_reason_sub(rows)
    assert headers == [
        "entity",
        "removed_reason",
        "removed_reason_sub",
        "removed",
        "touched_late",
        "rate",
    ]

    # Перетворимо у мапу для стабільних асертів.
    mp = {(r[0], r[1], r[2]): r for r in data}

    z = mp[("zone", "invalidated_rule", "rebucket_time_window")]
    assert z[3] == "2"  # removed
    assert z[4] == "1"  # touched_late
    assert z[5] == "50.0%"

    p = mp[("pool", "invalidated_rule", "context_flip")]
    assert p[3] == "1"
    assert p[4] == "1"
    assert p[5] == "100.0%"

    assert ("magnet", "invalidated_rule", "rebucket_time_window") not in mp
