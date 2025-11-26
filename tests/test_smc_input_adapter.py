"""Тести для адаптера UnifiedDataStore → SmcInput."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast

import pandas as pd
from redis.asyncio import Redis

from data.unified_store import StoreConfig, UnifiedDataStore
from smc_core.input_adapter import _normalize_frame, build_smc_input_from_store


class _InMemoryRedis:
    """Мінімальна in-memory реалізація Redis API для юніт-тестів."""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    async def get(self, key: str) -> bytes | None:
        return self._store.get(key)

    async def set(self, key: str, value: Any, ex: int | None = None) -> bool:
        data = value.encode() if isinstance(value, str) else value
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("Redis payload має бути bytes або str")
        self._store[key] = bytes(data)
        return True

    async def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0


def _make_store() -> UnifiedDataStore:
    redis_stub = cast(Redis, _InMemoryRedis())
    cfg = StoreConfig(
        validate_on_read=False, validate_on_write=False, write_behind=False
    )
    return UnifiedDataStore(redis=redis_stub, cfg=cfg)


def test_build_smc_input_from_store() -> None:
    frame = pd.DataFrame(
        {
            "open_time": [1, 2],
            "open": [10.0, 10.5],
            "high": [10.8, 11.2],
            "low": [9.8, 10.1],
            "close": [10.6, 11.0],
            "volume": [100, 120],
            "close_time": [2, 3],
        }
    )
    store = _make_store()
    store.ram.put("xauusd", "5m", frame)
    store.ram.put("xauusd", "15m", frame)

    smc_input = asyncio.run(
        build_smc_input_from_store(
            store,
            "xauusd",
            "5m",
            tfs_extra=["15m"],
            limit=1,
        )
    )

    assert smc_input.ohlc_by_tf["5m"].shape[0] == 1
    assert "15m" in smc_input.ohlc_by_tf
    assert smc_input.context == {}


def test_normalize_frame_converts_open_time_ms_to_utc_timestamp() -> None:
    base_ms = int(datetime(2025, 11, 25, 12, 0, tzinfo=UTC).timestamp() * 1000)
    frame = pd.DataFrame(
        {
            "open_time": [base_ms, base_ms + 60_000],
            "open": [100.0, 101.0],
            "high": [101.0, 102.0],
            "low": [99.5, 100.5],
            "close": [100.5, 101.5],
            "volume": [10.0, 12.0],
        }
    )

    normalized = _normalize_frame(frame)

    assert "timestamp" in normalized.columns
    assert normalized["timestamp"].dt.tz is not None
    assert normalized["timestamp"].iloc[0].year == 2025
    assert normalized["timestamp"].iloc[1] > normalized["timestamp"].iloc[0]
