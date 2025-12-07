"""Тести телеметрійних утиліт."""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict

from app.telemetry import publish_ui_metrics


class DummyRedis:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.messages.append((channel, payload))


class DummyStore:
    def __init__(self, lru_keys: list[tuple[str, str]] | None) -> None:
        if lru_keys is not None:
            self.ram = type(
                "RamLayer",
                (),
                {"_lru": OrderedDict((key, None) for key in lru_keys)},
            )()
        else:
            self.ram = None

    def metrics_snapshot(self) -> dict[str, int]:
        return {"tick": 1}


def test_publish_ui_metrics_enriches_payload_with_hot_symbols() -> None:
    asyncio.run(_run_hot_symbols_case())


async def _run_hot_symbols_case() -> None:
    store = DummyStore(
        [
            ("xauusd", "1m"),
            ("xauusd", "5m"),  # дубль символа не повинен шкодити
            ("xagusd", "1m"),
        ]
    )
    redis = DummyRedis()

    await publish_ui_metrics(
        store,  # type: ignore
        redis,
        channel="test.metrics",
        interval=0.0,
        iteration_limit=1,
    )

    assert redis.messages, "Очікується хоча б одне повідомлення"
    channel, payload = redis.messages[0]
    assert channel == "test.metrics"
    data = json.loads(payload)
    assert data["hot_symbols"] == 2


def test_publish_ui_metrics_handles_absent_redis() -> None:
    asyncio.run(_run_absent_redis_case())


async def _run_absent_redis_case() -> None:
    store = DummyStore(None)
    # Перевіряємо, що цикл завершується навіть без Redis публішера
    await publish_ui_metrics(store, None, iteration_limit=1, interval=0.0)  # type: ignore
