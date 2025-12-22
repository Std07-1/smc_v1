"""Етап 1: матеріалізація старших TF у UnifiedDataStore (SSOT=1m).

Ці тести фіксують, що `get_df(symbol, 5m/1h/4h)` працює навіть якщо snapshot-ів
старших TF ще немає: store має зібрати їх з нижчих TF та зберегти як snapshot-и.

Важливо: тести не використовують реальний Redis і пишуть тільки у tmp_path.
"""

from __future__ import annotations

import time
from typing import Any, cast

import pandas as pd
import pytest
from redis.asyncio import Redis

from data.unified_store import StoreConfig, UnifiedDataStore


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


def _make_store(*, base_dir: str) -> UnifiedDataStore:
    redis_stub = cast(Redis, _InMemoryRedis())
    cfg = StoreConfig(
        validate_on_read=False,
        validate_on_write=False,
        write_behind=False,
        base_dir=base_dir,
    )
    return UnifiedDataStore(redis=redis_stub, cfg=cfg)


def _make_1m_frame(*, start_ms: int, bars: int) -> pd.DataFrame:
    rows = int(bars)
    open_time = [start_ms + i * 60_000 for i in range(rows)]
    close_time = [t + 60_000 for t in open_time]
    # Легко перевіряти агрегати: open=i, high=i+0.5, low=i-0.5, close=i+0.1
    idx = list(range(rows))
    return pd.DataFrame(
        {
            "open_time": open_time,
            "close_time": close_time,
            "open": [float(i) for i in idx],
            "high": [float(i) + 0.5 for i in idx],
            "low": [float(i) - 0.5 for i in idx],
            "close": [float(i) + 0.1 for i in idx],
            "volume": [1.0 for _ in idx],
        }
    )


@pytest.mark.asyncio
async def test_materialize_5m_from_1m_and_persist(tmp_path: Any) -> None:
    now_ms = int(time.time() * 1000)
    base_5m = (now_ms // 300_000) * 300_000
    frame_1m = _make_1m_frame(start_ms=base_5m, bars=15)  # 3x 5m

    store = _make_store(base_dir=str(tmp_path))
    await store.put_bars("xauusd", "1m", frame_1m)

    out_5m = await store.get_df("xauusd", "5m")
    assert out_5m is not None
    assert out_5m.shape[0] == 3

    # Перевіряємо перший 5m бар (i=0..4)
    first = out_5m.iloc[0]
    assert int(first["open_time"]) == base_5m
    assert float(first["open"]) == 0.0
    assert float(first["high"]) == 4.5
    assert float(first["low"]) == -0.5
    assert float(first["close"]) == 4.1
    assert float(first["volume"]) == 5.0

    # Snapshot має бути матеріалізований на диск
    assert (tmp_path / "xauusd_bars_5m_snapshot.jsonl").exists()

    # Новий store має читати 5m з диска (без залежності від RAM)
    store2 = _make_store(base_dir=str(tmp_path))
    out_5m_2 = await store2.get_df("xauusd", "5m")
    assert out_5m_2.shape[0] == 3


@pytest.mark.asyncio
async def test_materialize_chain_4h_via_5m_1h_and_persist(tmp_path: Any) -> None:
    now_ms = int(time.time() * 1000)
    base_4h = (now_ms // 14_400_000) * 14_400_000

    # 240 хвилин = 4 години => 1 повний 4h бар
    frame_1m = _make_1m_frame(start_ms=base_4h, bars=240)

    store = _make_store(base_dir=str(tmp_path))
    await store.put_bars("xauusd", "1m", frame_1m)

    out_4h = await store.get_df("xauusd", "4h")
    assert out_4h is not None
    assert out_4h.shape[0] == 1

    # Перевіряємо, що chain матеріалізував проміжні TF
    assert (tmp_path / "xauusd_bars_5m_snapshot.jsonl").exists()
    assert (tmp_path / "xauusd_bars_1h_snapshot.jsonl").exists()
    assert (tmp_path / "xauusd_bars_4h_snapshot.jsonl").exists()
