"""QA-тести для Case H: outcome після touch (LONG vs SHORT).

Мета: зафіксувати базову коректність метрик reversal/continuation за X*ATR
для горизонтів K=1..N на синтетичних OHLCV.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from tools.smc_journal_report import _report_touch_outcome_by_direction, _Row


def _dt_from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, tz=UTC)


def test_case_h_touch_outcomes_long_vs_short_basic() -> None:
    # OHLCV: 4 бари, close_ms як маркер часу закриття
    close_ms = [1_000, 2_000, 3_000, 4_000]

    # Будуємо траєкторію так, щоб:
    # - для LONG після touch (на барі 2_000) відбулося сприятливе зростання >= 1*ATR за K=1
    # - для SHORT після touch (на барі 2_000) відбулося сприятливе падіння >= 1*ATR за K=1
    # ATR=5, поріг X=1.0 => 5
    lows = [99.0, 100.0, 95.0, 95.0]
    highs = [101.0, 100.0, 107.0, 107.0]
    closes = [100.0, 100.0, 100.0, 100.0]

    base = _Row(
        dt=_dt_from_ms(2_000),
        symbol="XAUUSD",
        tf="1m",
        entity="zone",
        event="touched",
        id="z1",
        type="OB",
        role="PRIMARY",
        direction="LONG",
        price_min=99.0,
        price_max=101.0,
        level=None,
        ctx={"atr_last": 5.0, "compute_kind": "close"},
    )

    rows = [base, replace(base, direction="SHORT", id="z2")]

    headers, data = _report_touch_outcome_by_direction(
        rows,
        close_ms=close_ms,
        lows=lows,
        highs=highs,
        closes=closes,
        x_atr=1.0,
        max_k=1,
    )

    assert "direction" in headers
    assert "k" in headers
    assert "reversal_rate" in headers

    by_dir = {(r[headers.index("direction")], r[headers.index("k")]): r for r in data}

    long_row = by_dir[("LONG", "1")]
    short_row = by_dir[("SHORT", "1")]

    # LONG: favorable = max_high - ref_close = 107 - 100 = 7 >= 5 => reversal hit
    long_rev = str(long_row[headers.index("reversal_rate")])
    assert long_rev.endswith("%")
    assert float(long_rev.rstrip("%")) == 100.0

    # SHORT: favorable = ref_close - min_low = 100 - 95 = 5 >= 5 => reversal hit
    short_rev = str(short_row[headers.index("reversal_rate")])
    assert short_rev.endswith("%")
    assert float(short_rev.rstrip("%")) == 100.0
