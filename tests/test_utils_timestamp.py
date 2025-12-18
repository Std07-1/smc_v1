"""Тести контрактних інваріантів `fxcm:ohlcv`.

SSOT по часу тут — `open_time`/`close_time` у UNIX ms (UTC) на I/O межі.
Ніяких best-effort "ensure"-функцій для DataFrame.
"""

from __future__ import annotations

from core.contracts.fxcm_validate import validate_fxcm_ohlcv_message


def test_validate_ohlcv_drops_bars_missing_open_time() -> None:
    raw = {
        "symbol": "EURUSD",
        "tf": "1m",
        "bars": [
            {
                "open_time": 1_700_000_000_000,
                "close_time": 1_700_000_060_000,
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.05,
                "volume": 10.0,
            },
            {
                # Некоректний бар: відсутній open_time
                "close_time": 1_700_000_120_000,
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.05,
                "volume": 10.0,
            },
        ],
    }

    out = validate_fxcm_ohlcv_message(raw)
    assert out is not None
    assert out["symbol"] == "EURUSD"
    assert out["tf"] == "1m"
    assert len(out["bars"]) == 1


def test_validate_ohlcv_returns_none_if_all_bars_invalid() -> None:
    raw = {
        "symbol": "EURUSD",
        "tf": "1m",
        "bars": [
            {
                # Некоректний бар: немає close_time
                "open_time": 1_700_000_000_000,
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.05,
                "volume": 10.0,
            }
        ],
    }

    assert validate_fxcm_ohlcv_message(raw) is None
