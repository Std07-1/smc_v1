"""Тести HMAC-валідації для FXCM інжестора."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import hmac
import json
from typing import Any

import pandas as pd
import pytest

from data import fxcm_ingestor as fxcm

BARS_TEMPLATE = [
    {
        "open_time": 1,
        "close_time": 59,
        "open": 1.0,
        "high": 1.1,
        "low": 0.9,
        "close": 1.05,
        "volume": 10.0,
    }
]


class _CounterProxy:
    def __init__(
        self,
        store: dict[tuple[tuple[str, str], ...], float],
        key: tuple[tuple[str, str], ...],
    ) -> None:
        self._store = store
        self._key = key

    def inc(self, amount: float = 1.0) -> None:
        self._store[self._key] = self._store.get(self._key, 0.0) + amount


class DummyCounter:
    def __init__(self) -> None:
        self.counts: dict[tuple[tuple[str, str], ...], float] = {}

    def labels(self, **kwargs: str) -> _CounterProxy:
        key = tuple(sorted(kwargs.items()))
        return _CounterProxy(self.counts, key)

    def inc(self, amount: float = 1.0) -> None:
        key: tuple[tuple[str, str], ...] = ()
        self.counts[key] = self.counts.get(key, 0.0) + amount


class FakeStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, pd.DataFrame]] = []

    async def put_bars(self, symbol: str, interval: str, df: pd.DataFrame) -> None:
        self.calls.append((symbol, interval, df.copy()))


@pytest.fixture(autouse=True)
def _reset_unexpected_flag() -> None:
    fxcm._UNEXPECTED_SIG_LOGGED = False


@pytest.fixture()
def patched_counters(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[DummyCounter, DummyCounter]:
    invalid = DummyCounter()
    unsigned = DummyCounter()
    monkeypatch.setattr(fxcm, "PROM_FXCM_INVALID_SIG", invalid)
    monkeypatch.setattr(fxcm, "PROM_FXCM_UNSIGNED_PAYLOAD", unsigned)
    return invalid, unsigned


def _base_payload() -> dict[str, Any]:
    return {
        "symbol": "xauusd",
        "tf": "1m",
        "bars": [copy.deepcopy(bar) for bar in BARS_TEMPLATE],
    }


def _signed_payload(secret: str, algo: str = "sha256") -> dict[str, Any]:
    base = _base_payload()
    signature = _make_signature(base, secret, algo)
    base["sig"] = signature
    return base


def _make_signature(payload: dict[str, Any], secret: str, algo: str = "sha256") -> str:
    raw = json.dumps(
        payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False
    ).encode("utf-8")
    digest = getattr(hashlib, algo, hashlib.sha256)
    return hmac.new(secret.encode("utf-8"), raw, digestmod=digest).hexdigest()


def _run_process(
    payload: dict[str, Any], *, secret: str | None, algo: str = "sha256", required: bool
) -> tuple[FakeStore, int]:
    store = FakeStore()
    rows, _, _ = asyncio.run(
        fxcm._process_payload(
            store,
            payload,
            hmac_secret=secret,
            hmac_algo=algo,
            hmac_required=required,
        )
    )
    return store, rows


def test_unsigned_payload_processed_when_hmac_disabled() -> None:
    payload = _base_payload()
    store, rows = _run_process(payload, secret=None, required=False)
    assert rows == 1
    assert len(store.calls) == 1


def test_valid_signature_accepted_when_required() -> None:
    secret = "test_secret"
    payload = _signed_payload(secret)
    store, rows = _run_process(payload, secret=secret, required=True)
    assert rows == 1
    assert len(store.calls) == 1


def test_missing_signature_dropped_and_metrics_increment(
    patched_counters: tuple[DummyCounter, DummyCounter],
) -> None:
    secret = "test_secret"
    payload = _base_payload()
    store, rows = _run_process(payload, secret=secret, required=True)
    invalid_counter, unsigned_counter = patched_counters
    assert rows == 0
    assert not store.calls
    assert invalid_counter.counts.get((("reason", "missing"),), 0.0) == 1
    assert unsigned_counter.counts.get((), 0.0) == 1


def test_invalid_signature_dropped(
    patched_counters: tuple[DummyCounter, DummyCounter],
) -> None:
    secret = "test_secret"
    payload = _base_payload()
    payload["sig"] = "deadbeef"
    store, rows = _run_process(payload, secret=secret, required=True)
    invalid_counter, unsigned_counter = patched_counters
    assert rows == 0
    assert not store.calls
    assert invalid_counter.counts.get((("reason", "mismatch"),), 0.0) == 1
    assert unsigned_counter.counts.get((), 0.0) == 0


def test_optional_mode_accepts_valid_signature() -> None:
    secret = "test_secret"
    payload = _signed_payload(secret)
    store, rows = _run_process(payload, secret=secret, required=False)
    assert rows == 1
    assert len(store.calls) == 1


def test_optional_mode_accepts_missing_signature_with_warning(
    patched_counters: tuple[DummyCounter, DummyCounter],
) -> None:
    secret = "test_secret"
    payload = _base_payload()
    store, rows = _run_process(payload, secret=secret, required=False)
    invalid_counter, unsigned_counter = patched_counters
    assert rows == 1
    assert len(store.calls) == 1
    assert invalid_counter.counts.get((("reason", "missing"),), 0.0) == 1
    assert unsigned_counter.counts.get((), 0.0) == 0


def test_optional_mode_still_drops_invalid_signature(
    patched_counters: tuple[DummyCounter, DummyCounter],
) -> None:
    secret = "test_secret"
    payload = _base_payload()
    payload["sig"] = "deadbeef"
    store, rows = _run_process(payload, secret=secret, required=False)
    invalid_counter, unsigned_counter = patched_counters
    assert rows == 0
    assert not store.calls
    assert invalid_counter.counts.get((("reason", "mismatch"),), 0.0) == 1
    assert unsigned_counter.counts.get((), 0.0) == 0


def test_signed_payload_accepted_when_secret_missing() -> None:
    payload = _base_payload()
    payload["sig"] = "whatever"
    store, rows = _run_process(payload, secret=None, required=False)
    assert rows == 1
    assert len(store.calls) == 1
    assert fxcm._UNEXPECTED_SIG_LOGGED is True
