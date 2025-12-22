"""Тести S2-логіки: history_state (insufficient/stale_tail)."""

from __future__ import annotations

import time

import pandas as pd

from app.fxcm_history_state import (
    FxcmHistoryState,
    HistoryStatus,
    classify_history,
    compute_history_status,
    timeframe_to_ms,
)


class _FakeStore:
    def __init__(self, df: pd.DataFrame | None) -> None:
        self._df = df

    async def get_df(self, symbol: str, timeframe: str, limit: int):  # noqa: ANN001
        return self._df


def test_timeframe_to_ms_parses_known_units() -> None:
    assert timeframe_to_ms("1m") == 60_000
    assert timeframe_to_ms("5m") == 300_000
    assert timeframe_to_ms("15m") == 900_000
    assert timeframe_to_ms("1h") == 3_600_000
    assert timeframe_to_ms("4h") == 14_400_000
    assert timeframe_to_ms("1d") == 86_400_000


def test_classify_insufficient_history_sets_needs_warmup() -> None:
    now_ms = 1_700_000_000_000
    out = classify_history(
        now_ms=now_ms,
        bars_count=10,
        last_open_time_ms=now_ms,
        min_history_bars=2000,
        tf_ms=60_000,
        stale_k=3.0,
    )
    assert isinstance(out, FxcmHistoryState)
    assert out.state == "insufficient"
    assert out.needs_warmup is True
    assert out.needs_backfill is False
    assert out.age_ms is None


def test_classify_stale_tail_sets_needs_backfill() -> None:
    now_ms = 1_700_000_000_000
    last_open_time_ms = now_ms - (10 * 60_000)
    out = classify_history(
        now_ms=now_ms,
        bars_count=2500,
        last_open_time_ms=last_open_time_ms,
        min_history_bars=2000,
        tf_ms=60_000,
        stale_k=3.0,
    )
    assert out.state == "stale_tail"
    assert out.needs_warmup is False
    assert out.needs_backfill is True
    assert isinstance(out.age_ms, int)
    assert out.age_ms >= 10 * 60_000


def test_classify_ok_when_tail_fresh_enough() -> None:
    now_ms = 1_700_000_000_000
    last_open_time_ms = now_ms - (2 * 60_000)
    out = classify_history(
        now_ms=now_ms,
        bars_count=2500,
        last_open_time_ms=last_open_time_ms,
        min_history_bars=2000,
        tf_ms=60_000,
        stale_k=3.0,
    )
    assert out.state == "ok"
    assert out.needs_warmup is False
    assert out.needs_backfill is False
    assert out.age_ms == 2 * 60_000


async def test_compute_history_status_reads_tail_and_counts() -> None:
    now_ms = int(time.time() * 1000.0)
    df = pd.DataFrame(
        [
            {
                "open_time": (now_ms - 60_000) / 1000.0,  # секунди
                "close_time": (now_ms - 1) / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            }
        ]
    )
    store = _FakeStore(df)
    status = await compute_history_status(
        store=store, symbol="xauusd", timeframe="1m", min_history_bars=1, now_ms=now_ms  # type: ignore
    )
    assert isinstance(status, HistoryStatus)
    assert status.symbol == "xauusd"
    assert status.timeframe == "1m"
    assert status.bars_count == 1
    assert status.last_open_time_ms is not None
    assert status.state in {"ok", "unknown"}
    assert status.gaps_count == 0
    assert status.max_gap_ms is None
    assert status.non_monotonic_count == 0


async def test_compute_history_status_marks_gappy_tail_when_internal_gaps_exist() -> (
    None
):
    now_ms = 1_700_000_000_000
    # Симулюємо пропуск 1 бара: крок 2*60_000.
    base = now_ms - (5 * 60_000)
    df = pd.DataFrame(
        [
            {
                "open_time": (base + 0 * 120_000) / 1000.0,
                "close_time": (base + 0 * 120_000) / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            },
            {
                "open_time": (base + 1 * 120_000) / 1000.0,
                "close_time": (base + 1 * 120_000) / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            },
            {
                "open_time": (base + 2 * 120_000) / 1000.0,
                "close_time": (base + 2 * 120_000) / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            },
        ]
    )
    store = _FakeStore(df)
    status = await compute_history_status(
        store=store,
        symbol="xauusd",
        timeframe="1m",
        min_history_bars=3,
        stale_k=3.0,
        now_ms=now_ms,
    )
    assert status.state == "gappy_tail"
    assert status.needs_backfill is True
    assert status.gaps_count >= 1
    assert isinstance(status.max_gap_ms, int)
    assert status.non_monotonic_count == 0


async def test_compute_history_status_marks_non_monotonic_tail_when_bars_go_backwards() -> (
    None
):
    now_ms = 1_700_000_000_000
    # Важливо: тримаємо last_open_time в межах stale_k*tf (3х1m),
    # щоб кейс "бар позаду" не маскувався станом stale_tail.
    base = now_ms - (2 * 60_000)
    # Третій бар "позаду" (open_time менше попереднього) -> non_monotonic_tail.
    df = pd.DataFrame(
        [
            {
                "open_time": (base + 0 * 60_000) / 1000.0,
                "close_time": (base + 0 * 60_000) / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            },
            {
                "open_time": (base + 2 * 60_000) / 1000.0,
                "close_time": (base + 2 * 60_000) / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            },
            {
                "open_time": (base + 1 * 60_000) / 1000.0,
                "close_time": (base + 1 * 60_000) / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            },
        ]
    )
    store = _FakeStore(df)
    status = await compute_history_status(
        store=store,
        symbol="xauusd",
        timeframe="1m",
        min_history_bars=3,
        stale_k=3.0,
        now_ms=now_ms,
    )
    assert status.state == "non_monotonic_tail"
    assert status.needs_backfill is True
    assert status.non_monotonic_count >= 1


async def test_compute_history_status_does_not_mark_non_monotonic_when_open_time_duplicates_exist() -> (
    None
):
    now_ms = 1_700_000_000_000
    base = now_ms - (1 * 60_000)
    # Дублікати open_time не вважаємо "баром позаду".
    df = pd.DataFrame(
        [
            {
                "open_time": (base + 0 * 60_000) / 1000.0,
                "close_time": (base + 0 * 60_000) / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            },
            {
                "open_time": (base + 0 * 60_000) / 1000.0,
                "close_time": (base + 0 * 60_000) / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            },
            {
                "open_time": (base + 1 * 60_000) / 1000.0,
                "close_time": (base + 1 * 60_000) / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            },
        ]
    )
    store = _FakeStore(df)
    status = await compute_history_status(
        store=store,
        symbol="xauusd",
        timeframe="1m",
        min_history_bars=3,
        stale_k=3.0,
        now_ms=now_ms,
    )
    assert status.state == "ok"
    assert status.non_monotonic_count == 0
    assert status.gaps_count == 0
