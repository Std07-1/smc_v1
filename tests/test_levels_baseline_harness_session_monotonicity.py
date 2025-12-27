"""Тести для strict-гейту 3.2.3 (SESSION) у baseline harness.

Перевіряємо монотонність SESSION high/low у межах одного window_ts та reset при зміні window.
"""

from __future__ import annotations


def _sample(
    i: int,
    *,
    tf: str,
    window: tuple[int, int],
    label_high: str,
    label_low: str,
    high: float,
    low: float,
) -> dict:
    return {
        "i": i,
        "per_tf": {
            tf: {
                "candidates_v1": {
                    "raw": [
                        {
                            "label": label_high,
                            "source": "SESSION",
                            "kind": "line",
                            "window_ts": window,
                            "price": high,
                        },
                        {
                            "label": label_low,
                            "source": "SESSION",
                            "kind": "line",
                            "window_ts": window,
                            "price": low,
                        },
                    ]
                }
            }
        },
    }


def test_session_monotonicity_passes_within_window() -> None:
    from tools.levels_baseline_harness import validate_session_high_low_monotonicity

    w = (1, 10)
    samples = [
        _sample(
            0, tf="1h", window=w, label_high="LSH", label_low="LSL", high=10.0, low=5.0
        ),
        _sample(
            1, tf="1h", window=w, label_high="LSH", label_low="LSL", high=10.0, low=4.9
        ),
        _sample(
            2, tf="1h", window=w, label_high="LSH", label_low="LSL", high=10.2, low=4.9
        ),
    ]

    issues = validate_session_high_low_monotonicity(
        samples=samples,
        tf="1h",
        label_high="LSH",
        label_low="LSL",
        require_present=True,
    )
    assert issues == []


def test_session_reset_allowed_when_window_changes() -> None:
    from tools.levels_baseline_harness import validate_session_high_low_monotonicity

    w1 = (1, 10)
    w2 = (10, 20)
    samples = [
        _sample(
            0, tf="1h", window=w1, label_high="NYH", label_low="NYL", high=10.0, low=5.0
        ),
        _sample(
            1, tf="1h", window=w1, label_high="NYH", label_low="NYL", high=11.0, low=4.0
        ),
        # Нове вікно: reset дозволений.
        _sample(
            2, tf="1h", window=w2, label_high="NYH", label_low="NYL", high=7.0, low=6.0
        ),
    ]

    issues = validate_session_high_low_monotonicity(
        samples=samples,
        tf="1h",
        label_high="NYH",
        label_low="NYL",
        require_present=True,
    )
    assert issues == []


def test_session_monotonicity_detects_violation_within_window() -> None:
    from tools.levels_baseline_harness import validate_session_high_low_monotonicity

    w = (1, 10)
    samples = [
        _sample(
            0, tf="1h", window=w, label_high="ASH", label_low="ASL", high=10.0, low=5.0
        ),
        # Порушення: HIGH зменшився, LOW збільшився у тому ж window_ts
        _sample(
            1, tf="1h", window=w, label_high="ASH", label_low="ASL", high=9.9, low=5.1
        ),
    ]

    issues = validate_session_high_low_monotonicity(
        samples=samples,
        tf="1h",
        label_high="ASH",
        label_low="ASL",
        require_present=True,
    )
    assert issues


def test_session_monotonicity_skips_missing_when_not_required() -> None:
    from tools.levels_baseline_harness import validate_session_high_low_monotonicity

    w = (1, 10)
    # Немає кандидатів у sample(0) => при require_present=False інваріанти не перевіряються.
    samples = [
        {"i": 0, "per_tf": {"1h": {"candidates_v1": {"raw": []}}}},
        _sample(
            1, tf="1h", window=w, label_high="LSH", label_low="LSL", high=10.0, low=5.0
        ),
    ]

    issues = validate_session_high_low_monotonicity(
        samples=samples,
        tf="1h",
        label_high="LSH",
        label_low="LSL",
        require_present=False,
    )
    assert issues == []
