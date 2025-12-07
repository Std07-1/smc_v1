"""Юніт-тести допоміжних функцій cold_start_runner."""

from __future__ import annotations

from app.cold_start import format_cold_report_table, summarize_cold_report
from data.unified_store import ColdStartCacheEntry


def _entry(**kwargs: object) -> ColdStartCacheEntry:
    base = {
        "symbol": "xauusd",
        "interval": "1m",
        "rows_in_ram": 0,
        "rows_on_disk": 0,
        "redis_ttl": None,
        "last_open_time": None,
        "age_seconds": None,
        "ram_last_open_time": None,
        "disk_last_open_time": None,
        "redis_last_open_time": None,
        "disk_modified_ts": None,
    }
    base.update(kwargs)
    return ColdStartCacheEntry(**base)  # type: ignore[arg-type]


def test_summarize_report_flags_stale_and_insufficient() -> None:
    entries = [
        _entry(symbol="xauusd", rows_in_ram=10, rows_on_disk=50, age_seconds=50.0),
        _entry(symbol="eurusd", rows_in_ram=2, rows_on_disk=2, age_seconds=200.0),
        _entry(symbol="btcusdt", rows_in_ram=0, rows_on_disk=0, age_seconds=None),
    ]
    summary = summarize_cold_report(entries, stale_threshold=100, min_rows=5)
    assert summary["total"] == 3
    assert summary["stale_symbols"] == ["eurusd"]
    assert summary["insufficient_symbols"] == ["eurusd", "btcusdt"]
    assert summary["max_age_seconds"] == 200.0


def test_format_table_contains_headers_and_rows() -> None:
    entries = [
        _entry(
            symbol="xauusd",
            rows_in_ram=12,
            rows_on_disk=24,
            redis_ttl=90,
            last_open_time=1_700_000_000,
            age_seconds=12.5,
        )
    ]
    table = format_cold_report_table(entries)
    assert "symbol" in table
    assert "xauusd" in table
    assert "12.5" in table
    assert "2023" in table  # iso8601 timestamp фрагмент
