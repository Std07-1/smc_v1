"""Тести утиліт прогріву FXCM."""

from __future__ import annotations

import pandas as pd

from tools.fxcm_warmup import _build_store_frame, _fx_symbol


def test_fx_symbol_formats_pairs_properly() -> None:
    assert _fx_symbol("eurusd") == "EUR/USD"
    assert _fx_symbol("EUR/USD") == "EUR/USD"
    assert _fx_symbol("xauusd") == "XAU/USD"


def test_build_store_frame_produces_store_columns() -> None:
    base_ts = pd.Timestamp("2025-01-01T12:00:00Z")
    raw = pd.DataFrame(
        {
            "ts": [base_ts, base_ts + pd.Timedelta(minutes=1)],
            "open": [1.1, 1.2],
            "high": [1.2, 1.3],
            "low": [1.0, 1.1],
            "close": [1.15, 1.25],
            "volume": [100, 120],
        }
    )
    frame = _build_store_frame(raw, period_ms=60_000)
    assert list(frame.columns) == [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
    ]
    assert len(frame) == 2
    # open_time має бути у мілісекундах, кратних хвилині
    expected_open_time = int(base_ts.timestamp() * 1000)
    assert frame.loc[0, "open_time"] == expected_open_time
    assert frame.loc[0, "close_time"] == expected_open_time + 59_999
    assert frame.loc[1, "open_time"] == expected_open_time + 60_000
