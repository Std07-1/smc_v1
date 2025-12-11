"""Е2Е-smoke для ViewerStateWsServer."""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from typing import Any

import pytest
import websockets

from UI_v2.viewer_state_ws_server import ViewerStateWsServer


def _get_free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return port


class _FakeStore:
    def __init__(self, states: dict[str, Any]) -> None:
        self._states = states

    async def get_state(self, symbol: str) -> Any:
        return self._states.get(symbol)


class _FakePubSub:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def subscribe(self, *_channels: str) -> None:  # noqa: D401
        return None

    async def unsubscribe(self, *_channels: str) -> None:
        return None

    async def get_message(
        self,
        ignore_subscribe_messages: bool = True,
        timeout: float | int | None = None,
    ) -> dict[str, Any] | None:
        try:
            if timeout is None:
                return await self._queue.get()
            return await asyncio.wait_for(self._queue.get(), timeout)
        except asyncio.TimeoutError:
            return None

    async def close(self) -> None:
        return None

    async def push(self, payload: dict[str, Any]) -> None:
        await self._queue.put(payload)


class _FakeRedis:
    def __init__(self) -> None:
        self.last_pubsub: _FakePubSub | None = None

    def pubsub(self) -> _FakePubSub:
        self.last_pubsub = _FakePubSub()
        return self.last_pubsub


@pytest.mark.asyncio
async def test_ws_server_sends_snapshot_and_updates() -> None:
    port = _get_free_port()
    store = _FakeStore(
        {
            "XAUUSD": {
                "symbol": "XAUUSD",
                "price": 2500,
                "structure": {},
                "liquidity": {},
                "zones": {"raw": {}},
                "meta": {"seq": 1},
            }
        }
    )
    redis = _FakeRedis()
    server = ViewerStateWsServer(
        store=store,  # type: ignore[arg-type]
        redis=redis,
        channel_name="viewer_updates",
        host="127.0.0.1",
        port=port,
    )

    server_task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)

    uri = f"ws://127.0.0.1:{port}/smc-viewer/stream?symbol=XAUUSD"
    async with websockets.connect(uri) as ws:
        snapshot = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
        assert snapshot["type"] == "snapshot"
        assert snapshot["viewer_state"]["price"] == 2500

        pubsub = redis.last_pubsub
        assert pubsub is not None
        await pubsub.push(
            {
                "type": "message",
                "data": json.dumps(
                    {
                        "symbol": "XAUUSD",
                        "viewer_state": {
                            "symbol": "XAUUSD",
                            "price": 2501,
                            "structure": {},
                        },
                    }
                ),
            }
        )

        update = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
        assert update["type"] == "update"
        assert update["viewer_state"]["price"] == 2501

    server_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await server_task


@pytest.mark.asyncio
async def test_ws_server_rejects_missing_symbol() -> None:
    port = _get_free_port()
    store = _FakeStore({})
    redis = _FakeRedis()
    server = ViewerStateWsServer(
        store=store,  # type: ignore[arg-type]
        redis=redis,
        channel_name="viewer_updates",
        host="127.0.0.1",
        port=port,
    )

    server_task = asyncio.create_task(server.run())
    await asyncio.sleep(0.05)

    ws = await websockets.connect(f"ws://127.0.0.1:{port}/smc-viewer/stream")
    await ws.wait_closed()
    assert ws.close_code == 4400

    server_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await server_task
