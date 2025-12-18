"""Перевірка meta_extra для publish_full_state."""

from __future__ import annotations

import asyncio

from core.serialization import json_loads
from UI.publish_smc_state import publish_smc_state


class DummyStateManager:
    def __init__(self) -> None:
        self._assets = [
            {
                "symbol": "xauusd",
                "stats": {"current_price": 2000.0},
                "signal": "NORMAL",
            }
        ]

    def get_all_assets(self) -> list[dict[str, object]]:
        return list(self._assets)


class DummyCache:
    def __init__(self) -> None:
        self.redis = None

    def metrics_snapshot(self) -> dict[str, object]:
        return {}


class DummyRedis:
    def __init__(self) -> None:
        self.snapshot_payload: str | None = None
        self.published_payload: str | None = None

    async def set(self, *, name: str, value: str) -> None:
        self.snapshot_payload = value

    async def expire(self, *, name: str, time: int) -> None:  # pragma: no cover - noop
        return None

    async def publish(self, channel: str, payload: str) -> None:
        self.published_payload = payload


def test_publish_full_state_respects_cycle_metadata() -> None:
    asyncio.run(_run_cycle_meta_case())


async def _run_cycle_meta_case() -> None:
    state_manager = DummyStateManager()
    cache = DummyCache()
    redis = DummyRedis()

    meta_extra = {
        "cycle_seq": 42,
        "cycle_started_ts": "2025-12-07 12:00:00",
        "cycle_ready_ts": "2025-12-07 12:00:02",
    }

    await publish_smc_state(state_manager, cache, redis, meta_extra=meta_extra)  # type: ignore[arg-type]

    assert redis.published_payload is not None
    payload = json_loads(redis.published_payload)
    meta = payload["meta"]
    assert meta["seq"] == 42
    assert meta["cycle_seq"] == 42
    assert meta["cycle_started_ts"] == meta_extra["cycle_started_ts"]
    assert meta["cycle_ready_ts"] == meta_extra["cycle_ready_ts"]
