"""Тести cold-start репорту для UnifiedDataStore."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from app.cold_start import build_cold_start_report_payload
from data.unified_store import ColdStartCacheEntry, StoreConfig, UnifiedDataStore


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, Any] = {}
        self._ttl: dict[str, int] = {}

    async def get(self, key: str) -> Any:
        return self._store.get(key)

    async def set(self, key: str, value: Any, ex: int | None = None) -> None:
        self._store[key] = value
        if ex is None:
            self._ttl.pop(key, None)
        else:
            self._ttl[key] = ex

    async def ttl(self, key: str) -> int:
        return self._ttl.get(key, -1)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)
        self._ttl.pop(key, None)


def _make_df(start_ts: int, count: int) -> pd.DataFrame:
    rows = []
    for idx in range(count):
        base = start_ts + idx * 60_000
        rows.append(
            {
                "open_time": base,
                "close_time": base + 59_000,
                "open": 1.0 + idx,
                "high": 1.1 + idx,
                "low": 0.9 + idx,
                "close": 1.05 + idx,
                "volume": 10.0 + idx,
            }
        )
    return pd.DataFrame(rows)


def test_build_cold_start_report_includes_all_layers(tmp_path: Path) -> None:
    async def _run() -> None:
        cfg = StoreConfig(base_dir=str(tmp_path), write_behind=False)
        store = UnifiedDataStore(redis=_FakeRedis(), cfg=cfg)  # type: ignore

        now_ms = int(time.time() * 1000)
        df = _make_df(now_ms - 180_000, 3)
        await store.put_bars("xauusd", "1m", df)

        report = await store.build_cold_start_report(["xauusd"], "1m")
        assert len(report) == 1
        entry: ColdStartCacheEntry = report[0]
        assert entry.symbol == "xauusd"
        assert entry.rows_in_ram == 3
        assert entry.rows_on_disk == 3
        assert entry.redis_ttl == cfg.intervals_ttl["1m"]
        assert entry.last_open_time == pytest.approx(df.iloc[-1]["open_time"] / 1000.0)
        assert entry.age_seconds is not None and entry.age_seconds >= 0.0
        assert entry.ram_last_open_time == pytest.approx(entry.last_open_time)

    asyncio.run(_run())


def test_inspect_snapshot_handles_jsonl(tmp_path: Path) -> None:
    async def _run() -> None:
        cfg = StoreConfig(base_dir=str(tmp_path), write_behind=False)
        store = UnifiedDataStore(redis=_FakeRedis(), cfg=cfg)  # type: ignore

        path = store.disk.snapshot_path("xauusd", "1m", ext="jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                "open_time": 1_700_000_000_000,
                "close_time": 1_700_000_059_000,
                "open": 1,
                "high": 2,
                "low": 0.5,
                "close": 1.5,
                "volume": 10,
            },
            {
                "open_time": 1_700_000_120_000,
                "close_time": 1_700_000_179_000,
                "open": 2,
                "high": 3,
                "low": 1,
                "close": 2.5,
                "volume": 20,
            },
        ]
        with path.open("w", encoding="utf-8") as handle:
            for row in payload:
                handle.write(json.dumps(row))
                handle.write("\n")

        stats = await store.disk.inspect_snapshot("xauusd", "1m")
        assert stats is not None
        assert stats.rows == 2
        assert stats.last_open_time == pytest.approx(payload[-1]["open_time"] / 1000.0)
        assert stats.modified_ts is not None and stats.modified_ts > 0

    asyncio.run(_run())


def test_payload_builder_returns_summary_and_entries(tmp_path: Path) -> None:
    async def _run() -> None:
        cfg = StoreConfig(base_dir=str(tmp_path), write_behind=False)
        store = UnifiedDataStore(redis=_FakeRedis(), cfg=cfg)  # type: ignore
        now_ms = int(time.time() * 1000)
        df = _make_df(now_ms - 600_000, 10)
        await store.put_bars("xauusd", "1m", df)

        payload, entries = await build_cold_start_report_payload(
            store,
            symbols=["xauusd"],
            interval="1m",
            min_rows=5,
            stale_threshold=3_600,
        )

        assert payload["summary"]["stale_symbols"] == []  # type: ignore
        assert payload["summary"]["insufficient_symbols"] == []  # type: ignore
        assert len(entries) == 1
        assert payload["entries"][0]["symbol"] == "xauusd"  # type: ignore

    asyncio.run(_run())
