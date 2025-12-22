"""QA-тести для Проходу 1: нові зрізи у smc_journal_report.

Мета:
- lifetime_histogram_by_type: коректно рахує частку lifetime<=1/<=2 та бін-и.
- active_count_distribution: коректно рахує mean/p50/p90/p99/max по кількості active.

Ці тести не чіпають продакшн-логіку: тільки офлайн-агрегації звіту.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tools.smc_journal_report import (
    _Frame,
    _report_active_count_distribution,
    _report_lifetime_histogram_by_type,
    _Row,
)


def _dt() -> datetime:
    return datetime.fromtimestamp(0, tz=UTC)


def test_lifetime_histogram_by_type_bins_and_shares() -> None:
    rows = [
        _Row(
            dt=_dt(),
            symbol="XAUUSD",
            tf="5m",
            entity="pool",
            event="removed",
            id="p1",
            type="WICK_CLUSTER",
            direction=None,
            role=None,
            price_min=None,
            price_max=None,
            level=None,
            ctx={"compute_kind": "preview", "lifetime_bars": 0},
        ),
        _Row(
            dt=_dt(),
            symbol="XAUUSD",
            tf="5m",
            entity="pool",
            event="removed",
            id="p2",
            type="WICK_CLUSTER",
            direction=None,
            role=None,
            price_min=None,
            price_max=None,
            level=None,
            ctx={"compute_kind": "preview", "lifetime_bars": 1},
        ),
        _Row(
            dt=_dt(),
            symbol="XAUUSD",
            tf="5m",
            entity="pool",
            event="removed",
            id="p3",
            type="WICK_CLUSTER",
            direction=None,
            role=None,
            price_min=None,
            price_max=None,
            level=None,
            ctx={"compute_kind": "preview", "lifetime_bars": 2},
        ),
        _Row(
            dt=_dt(),
            symbol="XAUUSD",
            tf="5m",
            entity="pool",
            event="removed",
            id="p4",
            type="WICK_CLUSTER",
            direction=None,
            role=None,
            price_min=None,
            price_max=None,
            level=None,
            ctx={"compute_kind": "preview", "lifetime_bars": 7},
        ),
    ]

    headers, data = _report_lifetime_histogram_by_type(rows, thresholds=(1, 2))

    assert "removed_with_lifetime" in headers
    assert "lifetime_le_1" in headers
    assert "share_le_1" in headers
    assert "lifetime_le_2" in headers
    assert "share_le_2" in headers
    assert "bin_0" in headers
    assert "bin_1" in headers
    assert "bin_2" in headers
    assert "bin_6_10" in headers

    assert len(data) == 1
    row = data[0]
    idx = {h: i for i, h in enumerate(headers)}

    assert row[idx["entity"]] == "pool"
    assert row[idx["compute_kind"]] == "preview"
    assert row[idx["type"]] == "WICK_CLUSTER"
    assert row[idx["removed_with_lifetime"]] == "4"

    # <=1: {0,1} => 2/4 = 50.0%
    assert row[idx["lifetime_le_1"]] == "2"
    assert row[idx["share_le_1"]] == "50.0%"

    # <=2: {0,1,2} => 3/4 = 75.0%
    assert row[idx["lifetime_le_2"]] == "3"
    assert row[idx["share_le_2"]] == "75.0%"

    # bins
    assert row[idx["bin_0"]] == "1"
    assert row[idx["bin_1"]] == "1"
    assert row[idx["bin_2"]] == "1"
    assert row[idx["bin_6_10"]] == "1"


def test_active_count_distribution_basic_stats() -> None:
    frames = [
        _Frame(
            dt=_dt(),
            symbol="XAUUSD",
            tf="5m",
            kind="preview",
            primary_close_ms=1,
            bar_complete=True,
            active_ids={"pool": {"a", "b", "c"}},
            zone_overlap_n_active=0,
            zone_overlap_total_pairs=0,
            zone_overlap_pairs_iou_ge={"0.2": 0, "0.4": 0, "0.6": 0},
        ),
        _Frame(
            dt=_dt(),
            symbol="XAUUSD",
            tf="5m",
            kind="preview",
            primary_close_ms=2,
            bar_complete=True,
            active_ids={"pool": {"a"}},
            zone_overlap_n_active=0,
            zone_overlap_total_pairs=0,
            zone_overlap_pairs_iou_ge={"0.2": 0, "0.4": 0, "0.6": 0},
        ),
        _Frame(
            dt=_dt(),
            symbol="XAUUSD",
            tf="5m",
            kind="preview",
            primary_close_ms=3,
            bar_complete=True,
            active_ids={"pool": {"a", "b"}},
            zone_overlap_n_active=0,
            zone_overlap_total_pairs=0,
            zone_overlap_pairs_iou_ge={"0.2": 0, "0.4": 0, "0.6": 0},
        ),
    ]

    headers, data = _report_active_count_distribution(frames)
    assert len(data) == 1

    idx = {h: i for i, h in enumerate(headers)}
    row = data[0]

    assert row[idx["kind"]] == "preview"
    assert row[idx["entity"]] == "pool"
    assert row[idx["n_frames"]] == "3"

    # vals = [3,1,2] => mean=2.0, p50=2, p90=2, p99=2, max=3
    assert row[idx["active_mean"]] == "2.00"
    assert row[idx["active_p50"]] == "2.0"
    assert row[idx["active_max"]] == "3.0"
