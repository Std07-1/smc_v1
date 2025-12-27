"""Тести для 3.2.3b: SESSION кандидати (ASH/ASL, LSH/LSL, NYH/NYL).

Фокус:
- readiness (не будуємо на надто малому наборі барів),
- коректний window_ts (через get_session_window_utc),
- анти-lookahead (майбутні бари не впливають).
"""

from __future__ import annotations

from typing import Any

import pytest


def _mk_bar(
    *,
    t: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    complete: bool = True,
) -> dict[str, Any]:
    return {
        "time": float(t),
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "complete": bool(complete),
    }


def test_session_london_readiness_requires_2_bars_for_1h() -> None:
    from core.contracts import get_session_window_utc
    from UI_v2.viewer_state_builder import build_session_high_low_candidates_v1

    # Візьмемо asof у межах London (07–13). Достатньо 2 1h барів.
    asof = 1_000_000.0
    start, _end = get_session_window_utc(
        asof, session_start_hour_utc=7, session_end_hour_utc=13
    )

    frames_by_tf = {
        "1h": [
            _mk_bar(t=int(start + 0 * 3600), open_=1, high=2, low=0.5, close=1.5),
        ]
    }

    out = build_session_high_low_candidates_v1(
        symbol="XAUUSD",
        asof_ts=float(start + 2 * 3600 + 1),
        session_start_hour_utc=7,
        session_end_hour_utc=13,
        label_high="LSH",
        label_low="LSL",
        frames_by_tf=frames_by_tf,
    )
    assert out == []


def test_session_london_builds_6_candidates_and_window_ts_matches() -> None:
    from core.contracts import get_session_window_utc
    from UI_v2.viewer_state_builder import build_session_high_low_candidates_v1

    asof = 1_000_000.0
    start, end = get_session_window_utc(
        asof, session_start_hour_utc=7, session_end_hour_utc=13
    )

    frames_by_tf = {
        "1h": [
            _mk_bar(t=int(start + 0 * 3600), open_=1, high=10, low=1.0, close=2),
            _mk_bar(t=int(start + 1 * 3600), open_=1, high=11, low=0.9, close=2),
        ]
    }

    out = build_session_high_low_candidates_v1(
        symbol="XAUUSD",
        asof_ts=float(start + 2 * 3600 + 1),
        session_start_hour_utc=7,
        session_end_hour_utc=13,
        label_high="LSH",
        label_low="LSL",
        frames_by_tf=frames_by_tf,
    )

    assert len(out) == 6
    labels = {str(c.get("label") or "") for c in out}
    owner_tfs = {str(c.get("owner_tf") or "") for c in out}
    assert labels == {"LSH", "LSL"}
    assert owner_tfs == {"5m", "1h", "4h"}

    windows: set[tuple[int, int]] = set()
    for c in out:
        w = c.get("window_ts")
        assert w is not None
        windows.add((int(w[0]), int(w[1])))
    assert windows == {(int(start), int(end))}

    by_label = {str(c.get("label") or ""): c for c in out if c.get("owner_tf") == "1h"}
    p_h = by_label["LSH"].get("price")
    p_l = by_label["LSL"].get("price")
    assert p_h is not None
    assert p_l is not None
    assert float(p_h) == pytest.approx(11.0)
    assert float(p_l) == pytest.approx(0.9)


def test_session_no_lookahead_future_bar_not_counted() -> None:
    from core.contracts import get_session_window_utc
    from UI_v2.viewer_state_builder import build_session_high_low_candidates_v1

    asof = 1_000_000.0
    start, _end = get_session_window_utc(
        asof, session_start_hour_utc=22, session_end_hour_utc=7
    )

    # ASIA: readiness для 1h тут потребує 3 барів, тому додаємо 3-й бар у межах asof.
    # Далі додаємо майбутній бар з high=999 — він не має вплинути.
    asof_ts = float(start + 2 * 3600 + 1)
    frames_by_tf = {
        "1h": [
            _mk_bar(t=int(start + 0 * 3600), open_=1, high=10, low=1.0, close=2),
            _mk_bar(t=int(start + 1 * 3600), open_=1, high=11, low=0.9, close=2),
            _mk_bar(t=int(start + 2 * 3600), open_=1, high=7, low=0.95, close=2),
            _mk_bar(t=int(start + 3 * 3600), open_=1, high=999, low=0.1, close=2),
        ]
    }

    out = build_session_high_low_candidates_v1(
        symbol="XAUUSD",
        asof_ts=asof_ts,
        session_start_hour_utc=22,
        session_end_hour_utc=7,
        label_high="ASH",
        label_low="ASL",
        frames_by_tf=frames_by_tf,
    )

    assert len(out) == 6
    by_label = {str(c.get("label") or ""): c for c in out if c.get("owner_tf") == "1h"}
    p_h = by_label["ASH"].get("price")
    p_l = by_label["ASL"].get("price")
    assert p_h is not None
    assert p_l is not None
    assert float(p_h) == pytest.approx(11.0)
    assert float(p_l) == pytest.approx(0.9)


def test_session_excludes_out_of_window_bars_even_if_extreme() -> None:
    from core.contracts import get_session_window_utc
    from UI_v2.viewer_state_builder import build_session_high_low_candidates_v1

    asof = 1_000_000.0
    start, end = get_session_window_utc(
        asof, session_start_hour_utc=7, session_end_hour_utc=13
    )

    frames_by_tf = {
        "1h": [
            # Out of window: before start (high=999 має ігноруватись)
            _mk_bar(t=int(start - 3600), open_=1, high=999, low=0.5, close=1.5),
            # In window (2 бари => readiness для London)
            _mk_bar(t=int(start + 0 * 3600), open_=1, high=10, low=1.0, close=2),
            _mk_bar(t=int(start + 1 * 3600), open_=1, high=11, low=0.9, close=2),
            # Out of window: після end (low=0.1 має ігноруватись)
            _mk_bar(t=int(end + 0 * 3600), open_=1, high=12, low=0.1, close=2),
        ]
    }

    out = build_session_high_low_candidates_v1(
        symbol="XAUUSD",
        asof_ts=float(start + 2 * 3600 + 1),
        session_start_hour_utc=7,
        session_end_hour_utc=13,
        label_high="LSH",
        label_low="LSL",
        frames_by_tf=frames_by_tf,
    )

    assert len(out) == 6
    by_label = {str(c.get("label") or ""): c for c in out if c.get("owner_tf") == "1h"}
    p_h = by_label["LSH"].get("price")
    p_l = by_label["LSL"].get("price")
    assert p_h is not None
    assert p_l is not None
    assert float(p_h) == pytest.approx(11.0)
    assert float(p_l) == pytest.approx(0.9)


def test_session_5m_fallback_readiness_and_no_lookahead() -> None:
    from core.contracts import get_session_window_utc
    from UI_v2.viewer_state_builder import build_session_high_low_candidates_v1

    # NY: 13–22 (9 год) => readiness для 5m = 20 барів.
    asof = 1_000_000.0
    start, _end = get_session_window_utc(
        asof, session_start_hour_utc=13, session_end_hour_utc=22
    )

    bars_5m = []
    # 20 барів у межах вікна до asof.
    for i in range(20):
        t = int(start + i * 300)
        bars_5m.append(_mk_bar(t=t, open_=1, high=100 + i, low=50 - i * 0.1, close=2))

    # Майбутній бар у межах вікна, але після asof: не має впливати.
    future_t = int(start + 20 * 300)
    bars_5m.append(_mk_bar(t=future_t, open_=1, high=999, low=0.1, close=2))

    asof_ts = float(start + 19 * 300 + 1)
    out = build_session_high_low_candidates_v1(
        symbol="XAUUSD",
        asof_ts=asof_ts,
        session_start_hour_utc=13,
        session_end_hour_utc=22,
        label_high="NYH",
        label_low="NYL",
        frames_by_tf={"5m": bars_5m},
    )

    assert len(out) == 6
    by_label = {str(c.get("label") or ""): c for c in out if c.get("owner_tf") == "5m"}
    p_h = by_label["NYH"].get("price")
    p_l = by_label["NYL"].get("price")
    assert p_h is not None
    assert p_l is not None
    assert float(p_h) == pytest.approx(119.0)
    assert float(p_l) == pytest.approx(50.0 - 19 * 0.1)
