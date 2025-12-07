import asyncio
from collections import defaultdict

from app.cold_start import ColdstartHistoryReport, ensure_min_history


class FakeDf:
    def __init__(self, length: int):
        self._length = length

    def __len__(self) -> int:
        return self._length


class FakeStore:
    def __init__(self, lengths_by_symbol: dict[str, list[int]]):
        self.lengths_by_symbol = lengths_by_symbol
        self.calls = defaultdict(int)

    async def get_df(self, symbol: str, interval: str, limit: int):  # noqa: D401
        values = self.lengths_by_symbol.get(symbol, [])
        idx = min(self.calls[symbol], len(values) - 1) if values else -1
        self.calls[symbol] += 1
        if idx < 0:
            return None
        return FakeDf(values[idx])


def test_ensure_min_history_success_before_timeout():
    store = FakeStore(
        {
            "xauusd": [100, 250, 310],
            "eurusd": [200, 305],
        }
    )
    report = asyncio.run(
        ensure_min_history(
            store,  # type: ignore
            symbols=["xauusd", "eurusd"],
            interval="1m",
            required_bars=300,
            timeout_sec=2,
            sleep_sec=0.01,
        )
    )
    assert isinstance(report, ColdstartHistoryReport)
    assert report.status == "success"
    assert report.symbols_ready == 2
    assert report.symbols_pending == []


def test_ensure_min_history_timeout_all_pending():
    store = FakeStore(
        {
            "xauusd": [100, 150],
            "eurusd": [200, 250],
        }
    )
    report = asyncio.run(
        ensure_min_history(
            store,  # type: ignore
            symbols=["xauusd", "eurusd"],
            interval="1m",
            required_bars=300,
            timeout_sec=0.5,  # type: ignore
            sleep_sec=0.01,
        )
    )
    assert report.status == "timeout"
    assert sorted(report.symbols_pending) == ["eurusd", "xauusd"]
    assert report.symbols_ready == 0


def test_ensure_min_history_degraded_partial_ready():
    store = FakeStore(
        {
            "xauusd": [100, 320],
            "eurusd": [200],
        }
    )
    report = asyncio.run(
        ensure_min_history(
            store,  # type: ignore
            symbols=["xauusd", "eurusd"],
            interval="1m",
            required_bars=300,
            timeout_sec=0.5,  # type: ignore
            sleep_sec=0.01,
        )
    )
    assert report.status == "degraded"
    assert report.symbols_ready == 1
    assert report.symbols_pending == ["eurusd"]
