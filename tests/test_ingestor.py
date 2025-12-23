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


class _FakeFeedState:
    def __init__(self, market_state: str = "open") -> None:
        self.market_state = market_state
        self.price_state = "ok"
        self.ohlcv_state = "ok"


class FakeRedis:
    def __init__(self) -> None:
        self.publishes: list[tuple[str, str]] = []

    async def publish(self, channel: str, payload: str) -> None:
        self.publishes.append((str(channel), str(payload)))


@pytest.fixture(autouse=True)
def _reset_unexpected_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    fxcm._UNEXPECTED_SIG_LOGGED = False
    # Тести в інших модулях можуть "прогріти" live-cache інжестора і зробити
    # цей файл order-dependent. Скидаємо кеш для ізоляції.
    if hasattr(fxcm, "_reset_live_cache_for_tests"):
        fxcm._reset_live_cache_for_tests()

    # Інжестор має status-based gate (market/price). Щоб тест HMAC не залежав
    # від глобального стану `fxcm:status`, фіксуємо дозволяючий стан локально.
    monkeypatch.setattr(fxcm, "get_fxcm_feed_state", lambda: _FakeFeedState("open"))


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
    _rows_reported, _, _ = asyncio.run(
        fxcm._process_payload(
            store,  # type: ignore
            payload,
            redis=None,
            hmac_secret=secret,
            hmac_algo=algo,
            hmac_required=required,
        )
    )
    # T3: rows з процесора більше не гарантує "скільки реально записано".
    # Для тестів визначаємо rows як кількість рядків, переданих у store.put_bars().
    rows_written = sum(len(df) for _symbol, _tf, df in store.calls)
    return store, rows_written


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


def test_hmac_still_valid_with_extra_fields_in_bars_when_required() -> None:
    secret = "test_secret"
    payload = _base_payload()

    # Додаємо forward-compatible поля всередину bar (microstructure/meta).
    payload["bars"][0]["complete"] = True
    payload["bars"][0]["synthetic"] = False
    payload["bars"][0]["source"] = "fxcm"
    payload["bars"][0]["spread"] = 0.12
    payload["bars"][0]["tick_count"] = 42
    payload["bars"][0]["unknown_nested"] = {"a": 1, "b": [1, 2, 3]}

    base_for_sig = {
        "symbol": payload["symbol"],
        "tf": payload["tf"],
        "bars": payload["bars"],
    }
    payload["sig"] = _make_signature(base_for_sig, secret)

    store, rows = _run_process(payload, secret=secret, required=True)
    assert rows == 1
    assert len(store.calls) == 1


def _bar(*, open_time: int, close_time: int) -> dict[str, Any]:
    return {
        "open_time": int(open_time),
        "close_time": int(close_time),
        "open": 1.0,
        "high": 1.1,
        "low": 0.9,
        "close": 1.05,
        "volume": 10.0,
        "complete": True,
    }


def test_live_gap_publishes_backfill_command(monkeypatch: pytest.MonkeyPatch) -> None:
    # Увімкнемо фічу (kill-switch).
    monkeypatch.setattr(fxcm.cfg, "SMC_LIVE_GAP_BACKFILL_ENABLED", True)
    monkeypatch.setattr(fxcm.cfg, "SMC_LIVE_GAP_BACKFILL_COOLDOWN_SEC", 9999)
    monkeypatch.setattr(fxcm.cfg, "SMC_LIVE_GAP_BACKFILL_MAX_GAP_MINUTES", 180)
    monkeypatch.setattr(fxcm.cfg, "SMC_LIVE_GAP_BACKFILL_LOOKBACK_BARS", 50)
    monkeypatch.setattr(fxcm.cfg, "FXCM_COMMANDS_CHANNEL", "fxcm:commands")

    store = FakeStore()
    redis = FakeRedis()

    # Перший бар (last_ingested).
    payload1 = {
        "symbol": "xauusd",
        "tf": "1m",
        "bars": [_bar(open_time=60_000, close_time=119_999)],
    }
    asyncio.run(
        fxcm._process_payload(
            store,  # type: ignore
            payload1,
            redis=redis,  # type: ignore[arg-type]
            hmac_secret=None,
            hmac_algo="sha256",
            hmac_required=False,
        )
    )

    # Другий бар зі стрибком (пропущено 120_000).
    payload2 = {
        "symbol": "xauusd",
        "tf": "1m",
        "bars": [_bar(open_time=180_000, close_time=239_999)],
    }
    asyncio.run(
        fxcm._process_payload(
            store,  # type: ignore
            payload2,
            redis=redis,  # type: ignore[arg-type]
            hmac_secret=None,
            hmac_algo="sha256",
            hmac_required=False,
        )
    )

    assert len(redis.publishes) == 1
    channel, payload = redis.publishes[0]
    assert channel == "fxcm:commands"
    assert '"type":"fxcm_warmup"' in payload
    assert '"reason":"live_gap_detected"' in payload


def test_live_gap_respects_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fxcm.cfg, "SMC_LIVE_GAP_BACKFILL_ENABLED", True)
    monkeypatch.setattr(fxcm.cfg, "SMC_LIVE_GAP_BACKFILL_COOLDOWN_SEC", 10_000)
    monkeypatch.setattr(fxcm.cfg, "SMC_LIVE_GAP_BACKFILL_MAX_GAP_MINUTES", 180)
    monkeypatch.setattr(fxcm.cfg, "SMC_LIVE_GAP_BACKFILL_LOOKBACK_BARS", 50)
    monkeypatch.setattr(fxcm.cfg, "FXCM_COMMANDS_CHANNEL", "fxcm:commands")

    store = FakeStore()
    redis = FakeRedis()

    payload1 = {
        "symbol": "xauusd",
        "tf": "1m",
        "bars": [_bar(open_time=60_000, close_time=119_999)],
    }
    payload2 = {
        "symbol": "xauusd",
        "tf": "1m",
        "bars": [_bar(open_time=180_000, close_time=239_999)],
    }
    payload3 = {
        "symbol": "xauusd",
        "tf": "1m",
        "bars": [_bar(open_time=300_000, close_time=359_999)],
    }

    asyncio.run(
        fxcm._process_payload(
            store,  # type: ignore
            payload1,
            redis=redis,  # type: ignore[arg-type]
            hmac_secret=None,
            hmac_algo="sha256",
            hmac_required=False,
        )
    )
    asyncio.run(
        fxcm._process_payload(
            store,  # type: ignore
            payload2,
            redis=redis,  # type: ignore[arg-type]
            hmac_secret=None,
            hmac_algo="sha256",
            hmac_required=False,
        )
    )
    asyncio.run(
        fxcm._process_payload(
            store,  # type: ignore
            payload3,
            redis=redis,  # type: ignore[arg-type]
            hmac_secret=None,
            hmac_algo="sha256",
            hmac_required=False,
        )
    )

    # Дві події gap, але publish має бути лише один раз через cooldown.
    assert len(redis.publishes) == 1
