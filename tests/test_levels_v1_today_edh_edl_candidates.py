"""Тести для 3.2.2c: кандидати EDH/EDL у межах поточного торгового дня.

Фокус: коректність today HL, readiness та монотонність без lookahead.
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
    # Формат має відповідати очікуванням UI_v2.viewer_state_builder (high/low/open/close).
    return {
        # viewer_state_builder._bar_time_s підтримує time у секундах (s).
        "time": float(t),
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "complete": bool(complete),
    }


def test_today_edh_edl_readiness_1h_requires_3_bars() -> None:
    from core.contracts.levels_v1_time import get_day_window_utc
    from UI_v2.viewer_state_builder import build_today_edh_edl_candidates_v1

    day_start, day_end = get_day_window_utc(1_000.0, day_start_hour_utc=0)
    asof = float(day_start + 2 * 3600)  # 2h into day

    frames_by_tf = {
        "1h": [
            _mk_bar(t=int(day_start + 0 * 3600), open_=1, high=2, low=0.5, close=1.5),
            _mk_bar(t=int(day_start + 1 * 3600), open_=1, high=2.5, low=0.4, close=1.7),
        ]
    }

    out = build_today_edh_edl_candidates_v1(
        symbol="XAUUSD",
        asof_ts=asof,
        frames_by_tf=frames_by_tf,
        day_start_hour_utc=0,
    )
    assert out == []


def test_today_edh_edl_builds_6_candidates_and_window_ts_matches_today() -> None:
    from core.contracts.levels_v1_time import get_day_window_utc
    from UI_v2.viewer_state_builder import build_today_edh_edl_candidates_v1

    day_start, day_end = get_day_window_utc(1_000.0, day_start_hour_utc=0)
    asof = float(day_start + 3 * 3600 + 1)

    frames_by_tf = {
        "1h": [
            _mk_bar(t=int(day_start + 0 * 3600), open_=1, high=10, low=0.5, close=1.5),
            _mk_bar(t=int(day_start + 1 * 3600), open_=1, high=9, low=0.4, close=1.7),
            _mk_bar(t=int(day_start + 2 * 3600), open_=1, high=8, low=0.3, close=1.2),
        ]
    }

    out = build_today_edh_edl_candidates_v1(
        symbol="XAUUSD",
        asof_ts=asof,
        frames_by_tf=frames_by_tf,
        day_start_hour_utc=0,
    )
    assert len(out) == 6

    # 2 labels × 3 owner_tf
    labels = {c["label"] for c in out}
    owner_tfs = {c["owner_tf"] for c in out}
    assert labels == {"EDH", "EDL"}
    assert owner_tfs == {"5m", "1h", "4h"}

    # window_ts має бути today window
    windows = {tuple(c["window_ts"]) for c in out}
    assert windows == {(int(day_start), int(day_end))}

    # EDH = max(high), EDL = min(low)
    by_label = {c["label"]: c for c in out if c["owner_tf"] == "1h"}
    assert float(by_label["EDH"]["price"]) == pytest.approx(10.0)
    assert float(by_label["EDL"]["price"]) == pytest.approx(0.3)


def test_today_edh_edl_filters_future_bars_no_lookahead() -> None:
    from core.contracts.levels_v1_time import get_day_window_utc
    from UI_v2.viewer_state_builder import build_today_edh_edl_candidates_v1

    day_start, _day_end = get_day_window_utc(1_000.0, day_start_hour_utc=0)
    asof = float(day_start + 2 * 3600 + 1)

    # Є бар з high=999 у майбутньому (t > asof) — його не можна врахувати.
    frames_by_tf = {
        "1h": [
            _mk_bar(t=int(day_start + 0 * 3600), open_=1, high=10, low=1.0, close=2),
            _mk_bar(t=int(day_start + 1 * 3600), open_=1, high=11, low=0.9, close=2),
            # Третій бар в межах asof для readiness.
            _mk_bar(t=int(day_start + 2 * 3600), open_=1, high=7, low=0.95, close=2),
            # Майбутній бар (t > asof), не повинен впливати.
            _mk_bar(t=int(day_start + 3 * 3600), open_=1, high=999, low=0.1, close=2),
        ]
    }

    out = build_today_edh_edl_candidates_v1(
        symbol="XAUUSD",
        asof_ts=asof,
        frames_by_tf=frames_by_tf,
        day_start_hour_utc=0,
    )
    assert len(out) == 6
    by_label = {c["label"]: c for c in out if c["owner_tf"] == "1h"}
    assert float(by_label["EDH"]["price"]) == pytest.approx(11.0)
    assert float(by_label["EDL"]["price"]) == pytest.approx(0.9)
