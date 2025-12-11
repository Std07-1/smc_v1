"""Тести для UnifiedStoreOhlcvProvider."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from UI_v2.ohlcv_provider import OhlcvNotFound, UnifiedStoreOhlcvProvider


class _FakeStore:
    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    async def get_df(
        self, symbol: str, interval: str, *, limit: int | None = None
    ) -> pd.DataFrame:
        return self._df


@pytest.mark.asyncio
async def test_unified_provider_returns_sorted_bars() -> None:
    df = pd.DataFrame(
        [
            {
                "close_time": datetime(2025, 1, 1, 0, 2, tzinfo=timezone.utc),
                "open": 2.0,
                "high": 3.0,
                "low": 1.5,
                "close": 2.5,
                "volume": 20,
            },
            {
                "close_time": datetime(2025, 1, 1, 0, 1, tzinfo=timezone.utc),
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 10,
            },
        ]
    )
    store = _FakeStore(df)
    provider = UnifiedStoreOhlcvProvider(store)  # type: ignore

    bars = await provider.fetch_ohlcv("xauusd", "1m", limit=2)

    assert len(bars) == 2
    assert bars[0]["time"] < bars[1]["time"]
    assert bars[0]["open"] == pytest.approx(1.0)
    assert bars[1]["close"] == pytest.approx(2.5)


@pytest.mark.asyncio
async def test_unified_provider_raises_when_empty() -> None:
    store = _FakeStore(pd.DataFrame())
    provider = UnifiedStoreOhlcvProvider(store)  # type: ignore

    with pytest.raises(OhlcvNotFound):
        await provider.fetch_ohlcv("xauusd", "1m", limit=10)
