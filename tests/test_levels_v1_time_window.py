"""Тести для Levels‑V1 day window (Крок 3.2.2a).

Гейт:
- day window [D@start, D+1@start) у UTC працює детерміновано,
- межі на start/end коректні,
- prev day window рівно попередній день.
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.contracts import get_day_window_utc, get_prev_day_window_utc


def _ts(y: int, m: int, d: int, hh: int, mm: int = 0, ss: int = 0) -> float:
    return datetime(y, m, d, hh, mm, ss, tzinfo=UTC).timestamp()


def test_day_window_start0_basic() -> None:
    ts = _ts(2025, 12, 27, 12, 34, 56)
    start, end = get_day_window_utc(ts, day_start_hour_utc=0)

    assert start == _ts(2025, 12, 27, 0, 0, 0)
    assert end == _ts(2025, 12, 28, 0, 0, 0)
    assert start <= ts < end


def test_day_window_nonzero_start_boundary() -> None:
    # start=05:00 UTC
    start_hour = 5

    just_before = _ts(2025, 12, 27, 4, 59, 59)
    s1, e1 = get_day_window_utc(just_before, day_start_hour_utc=start_hour)
    assert s1 == _ts(2025, 12, 26, 5, 0, 0)
    assert e1 == _ts(2025, 12, 27, 5, 0, 0)

    at_boundary = _ts(2025, 12, 27, 5, 0, 0)
    s2, e2 = get_day_window_utc(at_boundary, day_start_hour_utc=start_hour)
    assert s2 == _ts(2025, 12, 27, 5, 0, 0)
    assert e2 == _ts(2025, 12, 28, 5, 0, 0)


def test_prev_day_window_is_adjacent() -> None:
    ts = _ts(2025, 12, 27, 12, 0, 0)
    cur_start, cur_end = get_day_window_utc(ts, day_start_hour_utc=0)
    prev_start, prev_end = get_prev_day_window_utc(ts, day_start_hour_utc=0)

    assert prev_end == cur_start
    assert prev_end - prev_start == 24 * 60 * 60
    assert cur_end - cur_start == 24 * 60 * 60
