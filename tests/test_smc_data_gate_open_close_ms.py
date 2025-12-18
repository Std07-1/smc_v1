"""Гейт DATA: інваріанти OHLCV (open/close time ms) та детермінізм сортування/дедупа.

Це tests-only хвиля (F1): фіксуємо поточну поведінку SSOT без зміни runtime.
"""

from __future__ import annotations

import pandas as pd

from core.contracts.fxcm_validate import validate_fxcm_ohlcv_message
from data.unified_store import UnifiedDataStore


def test_data_gate_rejects_missing_open_close_time() -> None:
    msg = {
        "symbol": "xauusd",
        "tf": "5m",
        "bars": [
            {
                # open_time/close_time відсутні
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.0,
                "volume": 100.0,
            }
        ],
    }
    assert validate_fxcm_ohlcv_message(msg) is None


def test_data_gate_rejects_close_time_before_open_time() -> None:
    msg = {
        "symbol": "xauusd",
        "tf": "5m",
        "bars": [
            {
                "open_time": 2_000,
                "close_time": 1_000,
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.0,
                "volume": 100.0,
            }
        ],
    }
    assert validate_fxcm_ohlcv_message(msg) is None


def test_data_gate_open_time_not_sorted_is_normalized_in_store_dedup_sort() -> None:
    df = pd.DataFrame(
        {
            "open_time": [3_000, 1_000, 2_000],
            "close_time": [3_500, 1_500, 2_500],
            "open": [1.0, 1.0, 1.0],
            "high": [1.1, 1.1, 1.1],
            "low": [0.9, 0.9, 0.9],
            "close": [1.0, 1.0, 1.0],
            "volume": [100.0, 100.0, 100.0],
        }
    )
    out = UnifiedDataStore._dedup_sort(df)
    assert list(out["open_time"].astype(int)) == [1_000, 2_000, 3_000]


def test_data_gate_duplicate_open_time_is_deterministic_keep_first_without_is_closed() -> (
    None
):
    df = pd.DataFrame(
        {
            "open_time": [1_000, 1_000, 2_000],
            "close_time": [1_500, 1_500, 2_500],
            "open": [10.0, 99.0, 20.0],
            "high": [10.1, 99.1, 20.1],
            "low": [9.9, 98.9, 19.9],
            "close": [10.0, 99.0, 20.0],
            "volume": [1.0, 2.0, 3.0],
        }
    )
    out = UnifiedDataStore._dedup_sort(df)
    # Поточна політика: keep="first" (перший рядок з open_time=1000)
    assert out.shape[0] == 2
    assert float(out.iloc[0]["open"]) == 10.0
    assert list(out["open_time"].astype(int)) == [1_000, 2_000]
