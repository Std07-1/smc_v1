"""Тести фільтрації FXCM інжестора за contract-of-needs."""

from __future__ import annotations

import pytest

from data.fxcm_ingestor import _process_payload


class _FakeStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    async def put_bars(self, symbol: str, interval: str, df) -> None:  # type: ignore[no-untyped-def]
        self.calls.append((symbol, interval, len(df)))


_PAYLOAD_OK = {
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

_PAYLOAD_OTHER = {
    "symbol": "EURUSD",
    "tf": "1m",
    "bars": [
        {
            "open_time": 1,
            "close_time": 2,
            "open": 1.0,
            "high": 1.1,
            "low": 0.9,
            "close": 1.05,
            "volume": 50.0,
        }
    ],
}


@pytest.mark.asyncio
async def test_process_payload_writes_only_allowed_pairs() -> None:
    store = _FakeStore()
    allowed = {("xauusd", "1m")}

    rows, sym, tf = await _process_payload(
        store,  # type: ignore
        _PAYLOAD_OK,
        hmac_secret=None,
        hmac_algo="sha256",
        hmac_required=False,
        allowed_pairs=allowed,
    )

    assert rows == 1
    assert (sym, tf) == ("xauusd", "1m")
    assert store.calls == [("xauusd", "1m", 1)]

    rows, sym, tf = await _process_payload(
        store,  # type: ignore
        _PAYLOAD_OTHER,
        hmac_secret=None,
        hmac_algo="sha256",
        hmac_required=False,
        allowed_pairs=allowed,
    )

    assert rows == 0
    assert store.calls == [("xauusd", "1m", 1)]


@pytest.mark.asyncio
async def test_process_payload_legacy_mode_accepts_all() -> None:
    store = _FakeStore()

    for payload in (_PAYLOAD_OK, _PAYLOAD_OTHER):
        rows, _, _ = await _process_payload(
            store,  # type: ignore
            payload,
            hmac_secret=None,
            hmac_algo="sha256",
            hmac_required=False,
            allowed_pairs=None,
        )
        assert rows == 1

    assert store.calls == [("xauusd", "1m", 1), ("eurusd", "1m", 1)]
