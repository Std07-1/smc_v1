"""Тести для Levels‑V1 session windows (Крок 3.2.3a).

Гейт:
- UTC-вікна сесій детерміновані,
- коректно обробляється перехід через північ (ASIA 22→07),
- active-session детектор працює на межах (start інклюзивний, end ексклюзивний).
"""

from __future__ import annotations

from datetime import UTC, datetime

from core.contracts import find_active_session_tag_utc, get_session_window_utc


def _ts(y: int, m: int, d: int, hh: int, mm: int = 0, ss: int = 0) -> float:
    return datetime(y, m, d, hh, mm, ss, tzinfo=UTC).timestamp()


def test_session_window_asia_crosses_midnight() -> None:
    # ASIA: 22:00 -> 07:00 (наступного дня)
    ts = _ts(2025, 1, 2, 2, 0, 0)
    start, end = get_session_window_utc(
        ts, session_start_hour_utc=22, session_end_hour_utc=7
    )

    assert start == _ts(2025, 1, 1, 22, 0, 0)
    assert end == _ts(2025, 1, 2, 7, 0, 0)
    assert start <= ts < end


def test_session_window_london_before_start_returns_prev_occurrence() -> None:
    # LONDON: 07:00 -> 13:00; о 06:00 ще не стартувала, тож беремо вчорашнє вікно.
    ts = _ts(2025, 1, 2, 6, 0, 0)
    start, end = get_session_window_utc(
        ts, session_start_hour_utc=7, session_end_hour_utc=13
    )

    assert start == _ts(2025, 1, 1, 7, 0, 0)
    assert end == _ts(2025, 1, 1, 13, 0, 0)
    assert not (start <= ts < end)


def test_find_active_session_tag_utc_boundaries() -> None:
    windows = {
        "ASIA": (22, 7),
        "LONDON": (7, 13),
        "NY": (13, 22),
    }

    assert (
        find_active_session_tag_utc(
            _ts(2025, 1, 2, 6, 59, 59), session_windows_utc=windows
        )
        == "ASIA"
    )
    assert (
        find_active_session_tag_utc(
            _ts(2025, 1, 2, 7, 0, 0), session_windows_utc=windows
        )
        == "LONDON"
    )
    assert (
        find_active_session_tag_utc(
            _ts(2025, 1, 2, 12, 59, 59), session_windows_utc=windows
        )
        == "LONDON"
    )
    assert (
        find_active_session_tag_utc(
            _ts(2025, 1, 2, 13, 0, 0), session_windows_utc=windows
        )
        == "NY"
    )
    assert (
        find_active_session_tag_utc(
            _ts(2025, 1, 2, 21, 59, 59), session_windows_utc=windows
        )
        == "NY"
    )
    assert (
        find_active_session_tag_utc(
            _ts(2025, 1, 2, 22, 0, 0), session_windows_utc=windows
        )
        == "ASIA"
    )
