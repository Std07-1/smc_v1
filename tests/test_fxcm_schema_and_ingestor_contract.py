"""Тести контрактів FXCM-схем та поведінки інжестора.

Ці тести покривають мінімальний контракт повідомлень FXCM і критичні правила:
- live-бар (complete=false) не пишеться в UDS;
- некоректні бари не мають валити інжестор;
- fxcm:status використовується лише як діагностика (інжест не блокуємо).
"""

from __future__ import annotations

import pytest

from data import fxcm_status_listener as status_listener
from data.fxcm_ingestor import _process_payload
from data.fxcm_models import parse_fxcm_aggregated_status
from data.fxcm_schema import (
    validate_fxcm_ohlcv_message,
    validate_fxcm_price_tick_message,
    validate_fxcm_status_message,
)


class _FakeStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    async def put_bars(self, symbol: str, interval: str, df) -> None:  # type: ignore[no-untyped-def]
        self.calls.append((symbol, interval, len(df)))


def test_validate_fxcm_ohlcv_message_accepts_minimal() -> None:
    msg = {
        "symbol": "XAUUSD",
        "tf": "1m",
        "bars": [
            {
                "open_time": 1,
                "close_time": 2,
                "open": 10.0,
                "high": 11.0,
                "low": 9.5,
                "close": 10.5,
                "volume": 100.0,
            }
        ],
    }
    out = validate_fxcm_ohlcv_message(msg)
    assert out is not None
    assert out["symbol"] == "XAUUSD"
    assert out["tf"] == "1m"
    assert len(out["bars"]) == 1


def test_validate_fxcm_ohlcv_message_skips_invalid_bars_but_keeps_valid() -> None:
    msg = {
        "symbol": "XAUUSD",
        "tf": "1m",
        "bars": [
            {"open_time": 1},
            {
                "open_time": 2,
                "close_time": 3,
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "volume": 1.0,
            },
        ],
    }
    out = validate_fxcm_ohlcv_message(msg)
    assert out is not None
    assert out["symbol"] == "XAUUSD"
    assert out["tf"] == "1m"
    assert len(out["bars"]) == 1


def test_validate_fxcm_ohlcv_message_rejects_missing() -> None:
    assert validate_fxcm_ohlcv_message({"symbol": "XAUUSD", "tf": "1m"}) is None
    assert validate_fxcm_ohlcv_message({"symbol": "", "tf": "1m", "bars": []}) is None


def test_validate_fxcm_price_tick_message_accepts_minimal() -> None:
    msg = {
        "symbol": "XAUUSD",
        "bid": 1.0,
        "ask": 2.0,
        "mid": 1.5,
        "tick_ts": 100.0,
        "snap_ts": 101.0,
    }
    out = validate_fxcm_price_tick_message(msg)
    assert out is not None
    assert out["symbol"] == "XAUUSD"


def test_validate_fxcm_price_tick_message_rejects_invalid() -> None:
    assert validate_fxcm_price_tick_message({"symbol": "XAUUSD"}) is None
    assert validate_fxcm_price_tick_message("not json") is None


def test_validate_fxcm_status_message_allows_partial() -> None:
    msg = {"ts": 1, "market": "open"}
    out = validate_fxcm_status_message(msg)
    assert out is not None
    assert out["ts"] == 1.0
    assert out["market"] == "open"


@pytest.mark.asyncio
async def test_process_payload_skips_incomplete_bars() -> None:
    status_listener._reset_fxcm_feed_state_for_tests()

    store = _FakeStore()
    payload = {
        "symbol": "XAUUSD",
        "tf": "1m",
        "bars": [
            {
                "open_time": 1,
                "close_time": 2,
                "open": 10.0,
                "high": 11.0,
                "low": 9.5,
                "close": 10.5,
                "volume": 100.0,
                "complete": False,
            },
            {
                "open_time": 2,
                "close_time": 3,
                "open": 10.5,
                "high": 11.2,
                "low": 10.1,
                "close": 10.9,
                "volume": 200.0,
                "complete": True,
            },
        ],
    }

    rows, sym, tf = await _process_payload(
        store,  # type: ignore[arg-type]
        payload,
        hmac_secret=None,
        hmac_algo="sha256",
        hmac_required=False,
        allowed_pairs=None,
    )

    assert rows == 1
    assert (sym, tf) == ("xauusd", "1m")
    assert store.calls == [("xauusd", "1m", 1)]


@pytest.mark.asyncio
async def test_process_payload_skips_invalid_bars_and_does_not_crash() -> None:
    status_listener._reset_fxcm_feed_state_for_tests()

    store = _FakeStore()
    payload = {
        "symbol": "XAUUSD",
        "tf": "1m",
        "bars": [
            {"open_time": 1},
            {
                "open_time": 2,
                "close_time": 3,
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "volume": 1.0,
            },
        ],
    }

    rows, sym, tf = await _process_payload(
        store,  # type: ignore[arg-type]
        payload,
        hmac_secret=None,
        hmac_algo="sha256",
        hmac_required=False,
        allowed_pairs=None,
    )

    assert rows == 1
    assert (sym, tf) == ("xauusd", "1m")
    assert store.calls == [("xauusd", "1m", 1)]


@pytest.mark.asyncio
async def test_process_payload_skips_when_market_closed() -> None:
    status_listener._reset_fxcm_feed_state_for_tests()
    status = parse_fxcm_aggregated_status(
        {"ts": 1, "market": "closed", "price": "ok", "ohlcv": "ok"}
    )
    status_listener._apply_status_snapshot(status)

    store = _FakeStore()
    payload = {
        "symbol": "XAUUSD",
        "tf": "1m",
        "bars": [
            {
                "open_time": 1,
                "close_time": 2,
                "open": 10.0,
                "high": 11.0,
                "low": 9.5,
                "close": 10.5,
                "volume": 100.0,
            }
        ],
    }

    rows, sym, tf = await _process_payload(
        store,  # type: ignore[arg-type]
        payload,
        hmac_secret=None,
        hmac_algo="sha256",
        hmac_required=False,
        allowed_pairs=None,
    )

    assert rows == 0
    assert sym is None
    assert tf is None
    assert store.calls == []

    status_listener._reset_fxcm_feed_state_for_tests()
