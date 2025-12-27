"""Тести для strict-гейту 3.2.2c (EDH/EDL) у baseline harness.

Перевіряємо монотонність у межах одного day window та reset при зміні window.
"""

from __future__ import annotations


def _sample(
    i: int, *, tf: str, window: tuple[int, int], edh: float, edl: float
) -> dict:
    return {
        "i": i,
        "per_tf": {
            tf: {
                "candidates_v1": {
                    "raw": [
                        {
                            "label": "EDH",
                            "source": "DAILY",
                            "kind": "line",
                            "window_ts": window,
                            "price": edh,
                        },
                        {
                            "label": "EDL",
                            "source": "DAILY",
                            "kind": "line",
                            "window_ts": window,
                            "price": edl,
                        },
                    ]
                }
            }
        },
    }


def test_edh_edl_monotonicity_passes_within_window() -> None:
    from tools.levels_baseline_harness import validate_today_edh_edl_monotonicity

    w = (1, 86401)
    samples = [
        _sample(0, tf="1h", window=w, edh=10.0, edl=5.0),
        _sample(1, tf="1h", window=w, edh=10.0, edl=4.9),
        _sample(2, tf="1h", window=w, edh=10.2, edl=4.9),
    ]

    issues = validate_today_edh_edl_monotonicity(
        samples=samples, tf="1h", require_present=True
    )
    assert issues == []


def test_edh_decrease_is_reported() -> None:
    from tools.levels_baseline_harness import validate_today_edh_edl_monotonicity

    w = (1, 86401)
    samples = [
        _sample(0, tf="1h", window=w, edh=10.0, edl=5.0),
        _sample(1, tf="1h", window=w, edh=9.9, edl=5.0),
    ]
    issues = validate_today_edh_edl_monotonicity(
        samples=samples, tf="1h", require_present=True
    )
    assert any("EDH зменшився" in x for x in issues)


def test_reset_allowed_when_window_changes() -> None:
    from tools.levels_baseline_harness import validate_today_edh_edl_monotonicity

    w1 = (1, 86401)
    w2 = (86401, 172801)
    samples = [
        _sample(0, tf="1h", window=w1, edh=10.0, edl=5.0),
        _sample(1, tf="1h", window=w1, edh=11.0, edl=4.0),
        # Новий день: дозволяємо reset (EDH може впасти, EDL може підрости).
        _sample(2, tf="1h", window=w2, edh=7.0, edl=6.0),
    ]
    issues = validate_today_edh_edl_monotonicity(
        samples=samples, tf="1h", require_present=True
    )
    assert issues == []
