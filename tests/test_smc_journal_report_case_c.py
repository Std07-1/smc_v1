"""Тести для метрик QA-звіту (Випадок C).

Перевіряємо:
- short_lifetime_share_by_type (lifetime_bars<=1/<=2) по type;
- flicker_short_lived_by_type (removed_reason_sub=flicker_short_lived) по type.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tools.smc_journal_report import (
    _report_flicker_short_lived_by_type,
    _report_short_lifetime_share_by_type,
    _Row,
)


def test_short_lifetime_share_by_type_counts() -> None:
    dt = datetime(2025, 1, 1, tzinfo=UTC)

    rows = [
        # type=WICK_CLUSTER, close: lifetime 0,1,3
        _Row(
            dt=dt,
            symbol="XAUUSD",
            tf="5m",
            entity="pool",
            event="removed",
            id="p1",
            type="WICK_CLUSTER",
            direction=None,
            role="PRIMARY",
            price_min=None,
            price_max=None,
            level=10.0,
            ctx={"compute_kind": "close", "lifetime_bars": 0},
        ),
        _Row(
            dt=dt,
            symbol="XAUUSD",
            tf="5m",
            entity="pool",
            event="removed",
            id="p2",
            type="WICK_CLUSTER",
            direction=None,
            role="PRIMARY",
            price_min=None,
            price_max=None,
            level=11.0,
            ctx={"compute_kind": "close", "lifetime_bars": 1},
        ),
        _Row(
            dt=dt,
            symbol="XAUUSD",
            tf="5m",
            entity="pool",
            event="removed",
            id="p3",
            type="WICK_CLUSTER",
            direction=None,
            role="PRIMARY",
            price_min=None,
            price_max=None,
            level=12.0,
            ctx={"compute_kind": "close", "lifetime_bars": 3},
        ),
        # type=OB, close: lifetime 2
        _Row(
            dt=dt,
            symbol="XAUUSD",
            tf="5m",
            entity="zone",
            event="removed",
            id="z1",
            type="OB",
            direction="LONG",
            role="PRIMARY",
            price_min=1.0,
            price_max=2.0,
            level=None,
            ctx={"compute_kind": "close", "lifetime_bars": 2},
        ),
    ]

    headers, data = _report_short_lifetime_share_by_type(rows, thresholds=(1, 2))
    assert headers[:5] == [
        "entity",
        "compute_kind",
        "type",
        "removed_total",
        "removed_with_lifetime",
    ]

    mp = {(r[0], r[1], r[2]): r for r in data}

    wick = mp[("pool", "close", "WICK_CLUSTER")]
    # total=3, with_life=3, <=1:2 (0,1), <=2:2 (0,1)
    assert wick[3] == "3"
    assert wick[4] == "3"
    assert wick[5] == "2" and wick[6] == "66.7%"  # <=1
    assert wick[7] == "2" and wick[8] == "66.7%"  # <=2

    ob = mp[("zone", "close", "OB")]
    # total=1, <=1:0, <=2:1
    assert ob[3] == "1"
    assert ob[4] == "1"
    assert ob[5] == "0" and ob[6] == "0.0%"
    assert ob[7] == "1" and ob[8] == "100.0%"


def test_flicker_short_lived_by_type_share() -> None:
    dt = datetime(2025, 1, 1, tzinfo=UTC)

    rows = [
        # pool/WICK_CLUSTER close: 2 removed total, 1 flicker
        _Row(
            dt=dt,
            symbol="XAUUSD",
            tf="5m",
            entity="pool",
            event="removed",
            id="p1",
            type="WICK_CLUSTER",
            direction=None,
            role="PRIMARY",
            price_min=None,
            price_max=None,
            level=10.0,
            ctx={"compute_kind": "close", "reason_sub": "flicker_short_lived"},
        ),
        _Row(
            dt=dt,
            symbol="XAUUSD",
            tf="5m",
            entity="pool",
            event="removed",
            id="p2",
            type="WICK_CLUSTER",
            direction=None,
            role="PRIMARY",
            price_min=None,
            price_max=None,
            level=11.0,
            ctx={"compute_kind": "close", "reason_sub": "rebucket_time_window"},
        ),
        # zone/OB close: 1 removed total, 1 flicker
        _Row(
            dt=dt,
            symbol="XAUUSD",
            tf="5m",
            entity="zone",
            event="removed",
            id="z1",
            type="OB",
            direction="LONG",
            role="PRIMARY",
            price_min=1.0,
            price_max=2.0,
            level=None,
            ctx={"compute_kind": "close", "reason_sub": "flicker_short_lived"},
        ),
    ]

    headers, data = _report_flicker_short_lived_by_type(rows)
    assert headers == [
        "entity",
        "compute_kind",
        "type",
        "removed_total",
        "removed_flicker_short_lived",
        "share_of_removed_for_type",
    ]

    mp = {(r[0], r[1], r[2]): r for r in data}
    wick = mp[("pool", "close", "WICK_CLUSTER")]
    assert wick[3] == "2"
    assert wick[4] == "1"
    assert wick[5] == "50.0%"

    ob = mp[("zone", "close", "OB")]
    assert ob[3] == "1"
    assert ob[4] == "1"
    assert ob[5] == "100.0%"
