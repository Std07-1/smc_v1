"""Тести для 3.2.4b1: RANGE кандидати (RANGE_H/RANGE_L).

Фокус:
- RANGE має єдине джерело правди: liquidity.magnets[*].pools[liq_type=RANGE_EXTREME].level.
- Інваріанти: потрібні обидва (high/low) і high > low.
- Формат: 0 або 6 записів (RANGE_H/RANGE_L × owner_tf=5m/1h/4h).
- window_ts: завжди None (бо RANGE не прив’язаний до UI-selected pools).
"""

from __future__ import annotations


def test_range_candidates_added_from_liquidity_magnets_window_ts_is_none() -> None:
    from UI_v2.viewer_state_builder import extend_range_candidates_v1

    liquidity = {
        "magnets": [
            {
                "meta": {"symbol": "xauusd"},
                "pools": [
                    {
                        "liq_type": "RANGE_EXTREME",
                        "level": 2000.0,
                        "meta": {"side": "HIGH"},
                    },
                    {
                        "liq_type": "RANGE_EXTREME",
                        "level": 1900.0,
                        "meta": {"side": "LOW"},
                    },
                ],
            }
        ]
    }

    out = []
    extend_range_candidates_v1(
        candidates=out,
        symbol="XAUUSD",
        asof_ts=123.0,
        liquidity=liquidity,
    )

    assert len(out) == 6
    labels = {str(c.get("label") or "") for c in out}
    owner_tfs = {str(c.get("owner_tf") or "") for c in out}
    sources = {str(c.get("source") or "") for c in out}
    kinds = {str(c.get("kind") or "") for c in out}
    assert labels == {"RANGE_H", "RANGE_L"}
    assert owner_tfs == {"5m", "1h", "4h"}
    assert sources == {"RANGE"}
    assert kinds == {"line"}

    windows = {c.get("window_ts") for c in out}
    assert windows == {None}


def test_range_candidates_not_added_when_missing_or_invalid() -> None:
    from UI_v2.viewer_state_builder import extend_range_candidates_v1

    out1 = []
    extend_range_candidates_v1(
        candidates=out1,
        symbol="XAUUSD",
        asof_ts=1.0,
        liquidity={"magnets": []},
    )
    assert out1 == []

    out2 = []
    extend_range_candidates_v1(
        candidates=out2,
        symbol="XAUUSD",
        asof_ts=1.0,
        liquidity={
            "magnets": [
                {
                    "meta": {"symbol": "xauusd"},
                    "pools": [
                        {"liq_type": "RANGE_EXTREME", "level": 100.0},
                    ],
                }
            ]
        },
    )
    assert out2 == []


def test_range_candidates_tick_tolerance_dedup_can_collapse_to_none() -> None:
    from UI_v2.viewer_state_builder import extend_range_candidates_v1

    # Для XAUUSD tick=0.01 -> tol=0.005. Рівні з різницею <= 0.005 злипаються.
    liquidity = {
        "magnets": [
            {
                "meta": {"symbol": "xauusd"},
                "pools": [
                    {"liq_type": "RANGE_EXTREME", "level": 100.000},
                    {"liq_type": "RANGE_EXTREME", "level": 100.004},
                ],
            }
        ]
    }

    out = []
    extend_range_candidates_v1(
        candidates=out,
        symbol="XAUUSD",
        asof_ts=1.0,
        liquidity=liquidity,
    )
    assert out == []


def test_range_candidates_tick_tolerance_keeps_distinct_extremes() -> None:
    from UI_v2.viewer_state_builder import extend_range_candidates_v1

    # 100.000 і 100.006 відрізняються на 0.006 > 0.005 => маємо 2 унікальні рівні.
    liquidity = {
        "magnets": [
            {
                "meta": {"symbol": "xauusd"},
                "pools": [
                    {"liq_type": "RANGE_EXTREME", "level": 100.000},
                    {"liq_type": "RANGE_EXTREME", "level": 100.004},
                    {"liq_type": "RANGE_EXTREME", "level": 100.006},
                ],
            }
        ]
    }

    out = []
    extend_range_candidates_v1(
        candidates=out,
        symbol="XAUUSD",
        asof_ts=1.0,
        liquidity=liquidity,
    )

    assert len(out) == 6
    prices_by_label = {}
    for c in out:
        label = str(c.get("label") or "")
        price = float(c.get("price") or 0.0)
        prices_by_label.setdefault(label, set()).add(price)
    assert prices_by_label.get("RANGE_H") == {100.006}
    assert prices_by_label.get("RANGE_L") == {100.0}
