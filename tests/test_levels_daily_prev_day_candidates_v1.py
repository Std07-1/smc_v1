"""Тести Levels‑V1 (Крок 3.2.2b): PDH/PDL з попереднього day window.

Гейти:
- PDH/PDL обчислюються як max(high) / min(low) у prev-day вікні.
- Якщо барів у вікні менше порогу — кандидатів немає.
- Детермінізм id: однаковий вхід → той самий id.
"""

from __future__ import annotations

from UI_v2.viewer_state_builder import build_prev_day_pdh_pdl_candidates_v1


def _bar_ms(time_ms: int, *, high: float, low: float) -> dict:
    return {
        "time": int(time_ms),
        "open": low,
        "high": float(high),
        "low": float(low),
        "close": low,
        "volume": 1.0,
        "complete": True,
    }


def test_prev_day_pdh_pdl_1h_ok() -> None:
    # prev-day: 2025-12-26 00:00..2025-12-27 00:00 UTC
    # asof: 2025-12-27 12:00 UTC
    asof_ts = 1766836800.0  # 2025-12-27 12:00:00 UTC

    # 24x1h бари (close_time), 00:00..23:00 UTC → 24 bars у вікні.
    bars_1h = []
    base_close_ms = 1766707200_000  # 2025-12-26 00:00:00 UTC
    for i in range(24):
        t = base_close_ms + i * 3600_000
        bars_1h.append(_bar_ms(t, high=100.0 + i, low=50.0 - i))

    # Підкреслюємо PDH/PDL.
    bars_1h[7]["high"] = 250.0
    bars_1h[9]["low"] = 10.0

    frames_by_tf = {"1h": bars_1h, "5m": []}

    out = build_prev_day_pdh_pdl_candidates_v1(
        symbol="XAUUSD",
        asof_ts=asof_ts,
        day_start_hour_utc=0,
        frames_by_tf=frames_by_tf,
    )

    assert len(out) == 6

    by_label_tf = {(c.get("label"), c.get("owner_tf")): c for c in out}
    assert ("PDH", "5m") in by_label_tf
    assert ("PDL", "5m") in by_label_tf
    assert ("PDH", "1h") in by_label_tf
    assert ("PDL", "1h") in by_label_tf
    assert ("PDH", "4h") in by_label_tf
    assert ("PDL", "4h") in by_label_tf

    assert by_label_tf[("PDH", "1h")].get("price") == 250.0
    assert by_label_tf[("PDL", "1h")].get("price") == 10.0


def test_prev_day_readiness_guard_blocks_fake() -> None:
    asof_ts = 1766836800.0  # 2025-12-27 12:00:00 UTC

    # Тільки 11 барів 1h → поріг 12 не виконано → []
    bars_1h = []
    base_close_ms = 1766707200_000
    for i in range(11):
        t = base_close_ms + i * 3600_000
        bars_1h.append(_bar_ms(t, high=100.0 + i, low=50.0 - i))

    out = build_prev_day_pdh_pdl_candidates_v1(
        symbol=None,
        asof_ts=asof_ts,
        day_start_hour_utc=0,
        frames_by_tf={"1h": bars_1h},
    )
    assert out == []


def test_prev_day_id_is_deterministic() -> None:
    asof_ts = 1766836800.0
    base_close_ms = 1766707200_000
    bars_1h = [
        _bar_ms(base_close_ms + i * 3600_000, high=200.0, low=100.0) for i in range(24)
    ]

    frames_by_tf = {"1h": bars_1h}

    out1 = build_prev_day_pdh_pdl_candidates_v1(
        symbol="XAUUSD",
        asof_ts=asof_ts,
        day_start_hour_utc=0,
        frames_by_tf=frames_by_tf,
    )
    out2 = build_prev_day_pdh_pdl_candidates_v1(
        symbol="XAUUSD",
        asof_ts=asof_ts,
        day_start_hour_utc=0,
        frames_by_tf=frames_by_tf,
    )

    # Використовуємо .get(...) замість прямого індексування, бо "id" не є обов'язковим у LevelCandidateV1.
    assert [c.get("id") for c in out1] == [c.get("id") for c in out2]
