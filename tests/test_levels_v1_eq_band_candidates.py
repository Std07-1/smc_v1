"""Тести для 3.2.5b: EQ band candidates (EQH/EQL).

Фокус:
- EQ має єдине джерело правди: liquidity.magnets[*].pools[liq_type=EQH/EQL].source_swings[*].price.
- Anti-fake: якщо немає >=2 унікальних swing-цін (з tick-tolerance) — band не емімо.
- Формат: 0 або 6 записів (EQH/EQL × owner_tf=5m/1h/4h).
- window_ts: завжди None.
"""

from __future__ import annotations


def test_eq_band_candidates_added_from_liquidity_magnets_window_ts_is_none() -> None:
    from UI_v2.viewer_state_builder import extend_eq_band_candidates_v1

    liquidity = {
        "magnets": [
            {
                "meta": {"symbol": "xauusd"},
                "pools": [
                    {
                        "liq_type": "EQH",
                        "level": 2011.0,
                        "source_swings": [
                            {"price": 2010.0},
                            {"price": 2012.0},
                            {"price": 2011.0},
                        ],
                    },
                    {
                        "liq_type": "EQL",
                        "level": 1991.0,
                        "source_swings": [
                            {"price": 1990.0},
                            {"price": 1992.0},
                            {"price": 1991.0},
                        ],
                    },
                ],
            }
        ]
    }

    out = []
    extend_eq_band_candidates_v1(
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

    assert labels == {"EQH", "EQL"}
    assert owner_tfs == {"5m", "1h", "4h"}
    assert sources == {"POOL_DERIVED"}
    assert kinds == {"band"}

    windows = {c.get("window_ts") for c in out}
    assert windows == {None}

    # price для band має бути None.
    assert {c.get("price") for c in out} == {None}

    # Перевіряємо, що top/bot саме зі swing-цін.
    eqh = [c for c in out if str(c.get("label") or "") == "EQH"]
    eql = [c for c in out if str(c.get("label") or "") == "EQL"]

    assert {float(c.get("top") or 0.0) for c in eqh} == {2012.0}
    assert {float(c.get("bot") or 0.0) for c in eqh} == {2010.0}

    assert {float(c.get("top") or 0.0) for c in eql} == {1992.0}
    assert {float(c.get("bot") or 0.0) for c in eql} == {1990.0}


def test_eq_band_candidates_not_added_when_missing_or_invalid() -> None:
    from UI_v2.viewer_state_builder import extend_eq_band_candidates_v1

    out1 = []
    extend_eq_band_candidates_v1(
        candidates=out1,
        symbol="XAUUSD",
        asof_ts=1.0,
        liquidity={"magnets": []},
    )
    assert out1 == []

    # Немає EQL => не емімо частково (інваріант 0 або 6).
    out2 = []
    extend_eq_band_candidates_v1(
        candidates=out2,
        symbol="XAUUSD",
        asof_ts=1.0,
        liquidity={
            "magnets": [
                {
                    "meta": {"symbol": "xauusd"},
                    "pools": [
                        {
                            "liq_type": "EQH",
                            "source_swings": [{"price": 2000.0}, {"price": 2001.0}],
                        }
                    ],
                }
            ]
        },
    )
    assert out2 == []

    # Є EQH+EQL, але у EQH лише 1 унікальна swing-ціна => не емімо.
    out3 = []
    extend_eq_band_candidates_v1(
        candidates=out3,
        symbol="XAUUSD",
        asof_ts=1.0,
        liquidity={
            "magnets": [
                {
                    "meta": {"symbol": "xauusd"},
                    "pools": [
                        {"liq_type": "EQH", "source_swings": [{"price": 2000.0}]},
                        {
                            "liq_type": "EQL",
                            "source_swings": [{"price": 1999.0}, {"price": 2001.0}],
                        },
                    ],
                }
            ]
        },
    )
    assert out3 == []
