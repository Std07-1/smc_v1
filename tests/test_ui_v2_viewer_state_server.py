"""Тести для UI_v2.viewer_state_server.ViewerStateStore.

Фокус:
- коректний розбір snapshot JSON з Redis;
- поведінка при відсутньому/битому snapshot;
- вибір окремого символу.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.contracts.viewer_state import SmcViewerState
from UI_v2.ohlcv_provider import OhlcvNotFoundError
from UI_v2.viewer_state_server import ViewerStateHttpServer
from UI_v2.viewer_state_store import ViewerStateStore


class _FakeRedis:
    """Простий fake Redis для тестів ViewerStateStore."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    async def get(self, key: str) -> Any:
        return self._data.get(key)


class _DummyStore:
    async def get_all_states(self) -> dict[str, Any]:
        return {}

    async def get_state(self, symbol: str) -> Any:  # pragma: no cover - не використ.
        return None


class _FakeOhlcvProvider:
    def __init__(self, data: dict[tuple[str, str], list[dict[str, Any]]]) -> None:
        self._data = data

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        *,
        to_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        key = (symbol, timeframe)
        bars = self._data.get(key)
        if not bars:
            raise OhlcvNotFoundError("not found")
        return bars[-limit:]


async def test_viewer_state_store_returns_empty_dict_on_missing_snapshot() -> None:
    fake_redis = _FakeRedis({})
    store = ViewerStateStore(redis=fake_redis, snapshot_key="missing_key")

    states = await store.get_all_states()
    assert states == {}


async def test_viewer_state_store_parses_snapshot_and_get_state() -> None:
    snapshot_key = "viewer_snapshot"
    snapshot_payload: dict[str, SmcViewerState] = {
        "XAUUSD": {
            "symbol": "XAUUSD",
            "price": 2412.5,
            "session": "London",
            "payload_ts": "2025-12-08T08:05:00+00:00",
            "payload_seq": 1,
            "schema": "smc_viewer_v1",
            "structure": {},
            "liquidity": {},
            "zones": {"raw": {}},
            "fxcm": None,
            "meta": {
                "ts": "2025-12-08T08:05:00+00:00",
                "seq": 1,
                "schema_version": "smc_state_v1",
            },
        }
    }
    import json

    fake_redis = _FakeRedis({snapshot_key: json.dumps(snapshot_payload)})
    store = ViewerStateStore(redis=fake_redis, snapshot_key=snapshot_key)

    states = await store.get_all_states()
    assert "XAUUSD" in states

    state = await store.get_state("XAUUSD")
    assert state is not None
    assert state["symbol"] == "XAUUSD"  # type: ignore
    assert state["price"] == 2412.5  # type: ignore

    missing = await store.get_state("EURUSD")
    assert missing is None


async def test_viewer_state_store_case_insensitive_lookup() -> None:
    snapshot_key = "viewer_snapshot"
    snapshot_payload: dict[str, SmcViewerState] = {
        "XAUUSD": {
            "symbol": "XAUUSD",
            "price": 1900.0,
            "session": "New York",
            "payload_ts": "2025-12-08T09:00:00+00:00",
            "payload_seq": 2,
            "schema": "smc_viewer_v1",
            "structure": {},
            "liquidity": {},
            "zones": {"raw": {}},
            "fxcm": None,
            "meta": {"ts": "2025-12-08T09:00:00+00:00", "seq": 2},
        }
    }
    fake_redis = _FakeRedis({snapshot_key: json.dumps(snapshot_payload)})
    store = ViewerStateStore(redis=fake_redis, snapshot_key=snapshot_key)

    for candidate in ("xauusd", "XaUuSd", "XAUUSD"):
        state = await store.get_state(candidate)
        assert state is not None
        assert state["symbol"] == "XAUUSD"  # type: ignore


@pytest.mark.asyncio
async def test_http_server_adds_cors_headers() -> None:
    server = ViewerStateHttpServer(store=_DummyStore())  # type: ignore
    request = b"OPTIONS /smc-viewer/snapshot HTTP/1.1\r\nHost: test\r\n\r\n"
    response, status, _ = await server._process_http_request(request)
    assert status == 200
    text = response.decode("utf-8", errors="replace")
    assert "HTTP/1.1 200 OK" in text
    assert "Access-Control-Allow-Origin: *" in text
    assert "Access-Control-Allow-Headers: Content-Type" in text
    assert "Access-Control-Allow-Methods: GET, OPTIONS" in text


@pytest.mark.asyncio
async def test_http_server_ohlcv_ok_response() -> None:
    provider = _FakeOhlcvProvider(
        {
            (
                "xauusd",
                "1m",
            ): [
                {
                    "time": 1,
                    "open": 1.0,
                    "high": 2.0,
                    "low": 0.5,
                    "close": 1.5,
                    "volume": 10.0,
                },
                {
                    "time": 2,
                    "open": 1.5,
                    "high": 2.5,
                    "low": 1.2,
                    "close": 2.0,
                    "volume": 15.0,
                },
            ]
        }
    )
    server = ViewerStateHttpServer(store=_DummyStore(), ohlcv_provider=provider)  # type: ignore
    request = (
        b"GET /smc-viewer/ohlcv?symbol=xauusd&tf=1m&limit=1 HTTP/1.1\r\n"
        b"Host: test\r\n\r\n"
    )
    response, status, _ = await server._process_http_request(request)
    assert status == 200
    text = response.decode("utf-8", errors="replace")
    assert "HTTP/1.1 200 OK" in text
    body = json.loads(text.split("\r\n\r\n", 1)[1])
    assert body["symbol"] == "xauusd"
    assert body["timeframe"] == "1m"
    assert len(body["bars"]) == 1
    assert body["bars"][0]["time"] == 2
    assert "Access-Control-Allow-Origin: *" in text


@pytest.mark.asyncio
async def test_http_server_ohlcv_missing_params() -> None:
    server = ViewerStateHttpServer(
        store=_DummyStore(), ohlcv_provider=_FakeOhlcvProvider({})  # type: ignore
    )
    request = b"GET /smc-viewer/ohlcv?symbol=&tf= HTTP/1.1\r\nHost: test\r\n\r\n"
    response, status, _ = await server._process_http_request(request)
    assert status == 400
    text = response.decode("utf-8", errors="replace")
    assert "HTTP/1.1 400 Bad Request" in text


@pytest.mark.asyncio
async def test_http_server_ohlcv_invalid_limit() -> None:
    provider = _FakeOhlcvProvider({})
    server = ViewerStateHttpServer(store=_DummyStore(), ohlcv_provider=provider)  # type: ignore
    request = b"GET /smc-viewer/ohlcv?symbol=xauusd&tf=1m&limit=foo HTTP/1.1\r\nHost: test\r\n\r\n"
    response, status, _ = await server._process_http_request(request)
    assert status == 400
    text = response.decode("utf-8", errors="replace")
    assert "HTTP/1.1 400 Bad Request" in text


@pytest.mark.asyncio
async def test_http_server_ohlcv_not_found() -> None:
    provider = _FakeOhlcvProvider({})
    server = ViewerStateHttpServer(store=_DummyStore(), ohlcv_provider=provider)  # type: ignore
    request = b"GET /smc-viewer/ohlcv?symbol=xauusd&tf=1m&limit=10 HTTP/1.1\r\nHost: test\r\n\r\n"
    response, status, _ = await server._process_http_request(request)
    assert status == 404
    text = response.decode("utf-8", errors="replace")
    assert "HTTP/1.1 404 Not Found" in text


@pytest.mark.asyncio
async def test_http_server_ohlcv_disabled_returns_501() -> None:
    server = ViewerStateHttpServer(store=_DummyStore(), ohlcv_provider=None)  # type: ignore
    request = (
        b"GET /smc-viewer/ohlcv?symbol=xauusd&tf=1m HTTP/1.1\r\nHost: test\r\n\r\n"
    )
    response, status, _ = await server._process_http_request(request)
    assert status == 501
    text = response.decode("utf-8", errors="replace")
    assert "HTTP/1.1 501 Not Implemented" in text
