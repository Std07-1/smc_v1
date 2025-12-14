"""Тести для publish_smc_state."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from config.config import REDIS_CHANNEL_SMC_STATE
from UI.publish_smc_state import publish_smc_state


class _DummyStateManager:
    def __init__(self, assets: list[dict[str, Any]]) -> None:
        self._assets = assets

    def get_all_assets(self) -> list[dict[str, Any]]:
        return [dict(asset) for asset in self._assets]


class _DummyRedis:
    def __init__(self) -> None:
        self.snapshot: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []

    async def set(
        self, name: str, value: str
    ) -> None:  # pragma: no cover - простий сеттер
        self.snapshot[name] = value

    async def expire(self, name: str, time: int) -> None:  # pragma: no cover - заглушка
        return None

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))


class _DummyRedisPublishFails(_DummyRedis):
    async def publish(self, channel: str, message: str) -> None:
        raise ConnectionError("redis down")


def test_publish_smc_state_serializes_payload() -> None:
    manager = _DummyStateManager(
        [
            {
                "symbol": "xauusd",
                "stats": {
                    "current_price": 2000.0,
                    "smc_latency_ms": 10.5,
                },
                "smc_hint": {
                    "structure": {"swings": [], "bias": "LONG"},
                    "liquidity": {
                        "amd_phase": "EXPANSION",
                        "pools": [{"level": 1.0}],
                        "magnets": [{"center": 1.0}],
                    },
                    "zones": {"active_zones": [{"price_min": 1.0, "price_max": 2.0}]},
                },
            }
        ]
    )
    redis = _DummyRedis()

    asyncio.run(
        publish_smc_state(
            manager,
            object(),
            redis,  # type: ignore
            meta_extra={"cycle_seq": 42},
        )
    )

    assert redis.published, "має бути хоча б одне публікування"
    channel, payload_raw = redis.published[0]
    assert channel == REDIS_CHANNEL_SMC_STATE
    payload = json.loads(payload_raw)
    assert payload["meta"]["cycle_seq"] == 42
    assert payload["assets"][0]["price"] == 2000.0
    assert payload["assets"][0]["price_str"].endswith("USD")
    analytics = payload.get("analytics")
    assert analytics is not None
    assert analytics["bias_counts"]["LONG"] == 1
    assert analytics["amd_phase_counts"]["EXPANSION"] == 1


class _DummyCacheWithMetrics:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self._snapshot = snapshot

    def metrics_snapshot(self) -> dict[str, Any]:
        return self._snapshot


def test_publish_smc_state_enriches_fxcm_meta() -> None:
    manager = _DummyStateManager([])
    redis = _DummyRedis()
    fxcm_snapshot = {
        "fxcm": {
            "market_state": "open",
            "lag_seconds": 1.5,
            "last_close": 2010.0,
        }
    }

    asyncio.run(
        publish_smc_state(
            manager,
            _DummyCacheWithMetrics(fxcm_snapshot),
            redis,  # type: ignore
        )
    )

    assert redis.published
    _, payload_raw = redis.published[0]
    payload = json.loads(payload_raw)
    fxcm_meta = payload["meta"].get("fxcm")
    assert fxcm_meta == fxcm_snapshot["fxcm"]
    assert payload["fxcm"] == fxcm_snapshot["fxcm"]


def test_publish_smc_state_normalizes_breaker_zones() -> None:
    manager = _DummyStateManager(
        [
            {
                "symbol": "xauusd",
                "stats": {"current_price": 2000.0},
                "smc_hint": {
                    "structure": {},
                    "liquidity": {},
                    "zones": {
                        "breaker_zones": [
                            {"price_min": 0.2, "price_max": 0.25},
                        ]
                    },
                },
            }
        ]
    )
    redis = _DummyRedis()

    asyncio.run(
        publish_smc_state(
            manager,
            object(),
            redis,  # type: ignore
        )
    )

    _, payload_raw = redis.published[0]
    payload = json.loads(payload_raw)
    breaker = payload["assets"][0]["smc"]["zones"]["breaker_zones"][0]
    assert abs(breaker["price_min"] - 2000.0) < 1e-6
    assert abs(breaker["price_max"] - 2500.0) < 1e-6


def test_publish_smc_state_does_not_crash_when_redis_publish_fails() -> None:
    manager = _DummyStateManager(
        [
            {
                "symbol": "xauusd",
                "stats": {"current_price": 2000.0},
                "smc_hint": {"structure": {}, "liquidity": {}, "zones": {}},
            }
        ]
    )
    redis = _DummyRedisPublishFails()

    # Не має піднімати виключення, якщо Redis коротко недоступний.
    asyncio.run(
        publish_smc_state(
            manager,
            object(),
            redis,  # type: ignore
            meta_extra={"cycle_seq": 1},
        )
    )
