"""Тести кешу живих тикових цін у UnifiedDataStore."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from data.unified_store import StoreConfig, UnifiedDataStore


class DummyRedis:
    """Мінімальна заглушка redis.asyncio.Redis для юніт-тестів."""

    def __init__(self) -> None:
        self._storage: dict[str, str] = {}

    async def get(self, key: str) -> str | None:  # pragma: no cover - простий геттер
        return self._storage.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._storage[key] = value

    async def ttl(self, key: str) -> int:
        return 60


@pytest.fixture()
def store(tmp_path: Path) -> UnifiedDataStore:
    cfg = StoreConfig(base_dir=str(tmp_path))
    dummy = DummyRedis()
    return UnifiedDataStore(redis=dummy, cfg=cfg)  # type: ignore


def test_price_tick_cache_returns_live_mid(store: UnifiedDataStore) -> None:
    """Кеш має повертати нормалізований mid/bid/ask зі свіжого тика."""

    payload = {
        "symbol": "XAUUSD",
        "bid": 2375.1,
        "ask": 2375.4,
        "mid": 2375.25,
        "tick_ts": 1_765_000_000.0,
        "snap_ts": 1_765_000_001.0,
    }
    stored = store.update_price_tick(payload)
    assert stored is not None
    snap = store.get_price_tick("xauusd")
    assert snap is not None
    assert snap["spread"] == pytest.approx(0.3)
    assert snap["mid"] == pytest.approx(2375.25)
    assert snap["bid"] == pytest.approx(2375.1)
    assert snap["ask"] == pytest.approx(2375.4)
    assert snap["is_stale"] is False


def test_price_tick_cache_drops_stale_entries(store: UnifiedDataStore) -> None:
    """Протухлі записи мають видалятися після перевищення drop_after."""

    store._price_tick_drop_after = 0.5  # пришвидшуємо тест і зберігаємо контроль
    now = time.time()
    payload = {
        "symbol": "EURUSD",
        "bid": 1.0,
        "ask": 1.1,
        "tick_ts": now,
        "snap_ts": now,
    }
    store.update_price_tick(payload)
    entry = store._price_ticks["eurusd"]
    entry["tick_ts"] = now - 10
    snap = store.get_price_tick("EURUSD")
    assert snap is None
