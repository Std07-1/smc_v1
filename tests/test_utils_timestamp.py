"""Тести для ensure_timestamp_column та супутніх сценаріїв часу."""

from __future__ import annotations

import pandas as pd

from utils.utils import ensure_timestamp_column


def test_float_timestamp_in_milliseconds_converts_to_datetime() -> None:
    df = pd.DataFrame(
        {
            "timestamp": [1762732800000.0, 1762732860000.25],
            "open": [1.0, 2.0],
            "close": [1.1, 2.1],
        }
    )

    result = ensure_timestamp_column(df, drop_duplicates=False, sort=False)

    assert not result.empty
    assert pd.api.types.is_datetime64_any_dtype(result["timestamp"])
    assert result["timestamp"].dt.year.min() >= 2024


def test_seconds_timestamp_series_preserves_order() -> None:
    df = pd.DataFrame(
        {
            "timestamp": [1_762_732_800, 1_762_732_860],
            "open": [3.0, 4.0],
            "close": [3.3, 4.4],
        }
    )

    result = ensure_timestamp_column(df, drop_duplicates=False, sort=False)

    assert not result.empty
    assert result["timestamp"].iloc[0] < result["timestamp"].iloc[1]
    assert result["timestamp"].dt.tz is not None
