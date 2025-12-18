"""Тести стійкості до тимчасового падіння Redis.

Мета: якщо Redis/мережа коротко "пропадає", лістенери не мають валити процес,
а повинні робити перепідключення з backoff.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest


class _FakePubSub:
    def __init__(self, plan: list[str], calls: _Calls) -> None:
        self._plan = plan
        self._calls = calls

    async def subscribe(self, *_channels: str) -> None:
        self._calls.subscribes += 1

    async def unsubscribe(self, *_channels: str) -> None:
        self._calls.unsubscribes += 1

    async def close(self) -> None:
        self._calls.pubsub_closes += 1

    async def listen(self):  # type: ignore[override]
        action = self._plan.pop(0) if self._plan else "cancel"
        if action == "conn_error":
            raise ConnectionError("redis down")
        if action == "cancel":
            raise asyncio.CancelledError()
        if False:  # pragma: no cover
            yield None


class _FakeRedis:
    def __init__(self, plan: list[str], calls: _Calls, **_kwargs: object) -> None:
        self._plan = plan
        self._calls = calls

    def pubsub(self) -> _FakePubSub:
        self._calls.pubsubs += 1
        return _FakePubSub(self._plan, self._calls)

    async def close(self) -> None:
        self._calls.redis_closes += 1


@dataclass
class _Calls:
    pubsubs: int = 0
    subscribes: int = 0
    unsubscribes: int = 0
    pubsub_closes: int = 0
    redis_closes: int = 0


async def _no_sleep(_sec: float) -> None:
    return


@pytest.mark.asyncio
async def test_fxcm_status_listener_reconnects_on_redis_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from data import fxcm_status_listener as mod

    calls = _Calls()
    plan = ["conn_error", "cancel"]

    monkeypatch.setattr(
        mod, "Redis", lambda **kwargs: _FakeRedis(plan, calls, **kwargs)
    )
    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

    with pytest.raises(asyncio.CancelledError):
        await mod.run_fxcm_status_listener(
            redis_host="127.0.0.1",
            redis_port=6379,
            status_channel="fxcm:status",
        )

    assert calls.subscribes >= 2, "Очікували повторну підписку після дисконекту"


@pytest.mark.asyncio
async def test_fxcm_ingestor_reconnects_on_redis_drop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from data import fxcm_ingestor as mod

    class _DummyStore:
        pass

    calls = _Calls()
    plan = ["conn_error", "cancel"]

    monkeypatch.setattr(
        mod, "Redis", lambda **kwargs: _FakeRedis(plan, calls, **kwargs)
    )
    monkeypatch.setattr(mod.asyncio, "sleep", _no_sleep)

    with pytest.raises(asyncio.CancelledError):
        await mod.run_fxcm_ingestor(
            _DummyStore(),  # type: ignore
            redis_host="127.0.0.1",
            redis_port=6379,
            channel="fxcm:ohlcv",
            log_every_n=10,
            hmac_secret=None,
            hmac_required=False,
        )

    assert calls.subscribes >= 2, "Очікували повторну підписку після дисконекту"
