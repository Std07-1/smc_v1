"""Тести S3 воркера: rate-limit і формування команд для конектора."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from app.fxcm_warmup_requester import FxcmWarmupRequester


class _FakeRedis:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> int:  # noqa: D401
        self.published.append((channel, message))
        return 1


class _FakeStore:
    def __init__(self, df: pd.DataFrame | None) -> None:
        self._df = df

    async def get_df(self, symbol: str, timeframe: str, limit: int):  # noqa: ANN001
        return self._df


class _SequencedStore:
    """Повертає різні DF по черзі, щоб симулювати зміну history_state."""

    def __init__(self, sequence: list[pd.DataFrame | None]) -> None:
        self._seq = list(sequence)
        self._idx = 0

    async def get_df(self, symbol: str, timeframe: str, limit: int):  # noqa: ANN001
        if not self._seq:
            return None
        value = self._seq[min(self._idx, len(self._seq) - 1)]
        self._idx += 1
        return value


class _FakeFeed:
    def __init__(self, market_state: str = "closed") -> None:
        self.market_state = market_state
        self.price_state = "ok"
        self.ohlcv_state = "ok"


@pytest.mark.asyncio
async def test_requester_publishes_warmup_once_then_rate_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = _FakeRedis()
    fake_store = _FakeStore(df=None)  # 0 барів -> insufficient -> warmup

    # Фіксуємо desired-limit, щоб тест не залежав від глобального config.
    monkeypatch.setattr("app.fxcm_warmup_requester.SMC_RUNTIME_PARAMS", {"limit": 300})

    # фіксуємо час
    t0 = 1_700_000_000.0
    monkeypatch.setattr("app.fxcm_warmup_requester.utc_now_ms", lambda: int(t0 * 1000))
    monkeypatch.setattr(
        "app.fxcm_warmup_requester.get_fxcm_feed_state", lambda: _FakeFeed("closed")
    )

    requester = FxcmWarmupRequester(
        redis=fake_redis,  # type: ignore[arg-type]
        store=fake_store,  # type: ignore[arg-type]
        allowed_pairs={("xauusd", "1m")},
        min_history_bars_by_symbol={"xauusd": 2000},
        commands_channel="fxcm:commands",
        poll_sec=60,
        cooldown_sec=900,
        stale_k=3.0,
    )

    await requester._run_once()
    assert len(fake_redis.published) == 1

    channel, msg = fake_redis.published[0]
    assert channel == "fxcm:commands"
    payload = json.loads(msg)
    assert payload["type"] == "fxcm_warmup"
    assert payload["symbol"] == "XAUUSD"
    assert payload["tf"] == "1m"
    # На insufficient_history просимо мінімум для старту (desired-limit),
    # а контрактну глибину дорощуємо окремо у prefetch режимі.
    assert payload["min_history_bars"] == 300
    assert payload["lookback_bars"] == 300
    assert isinstance(payload["lookback_minutes"], int)
    assert payload["lookback_minutes"] >= 1
    assert payload["reason"] == "insufficient_history"
    assert payload["s2"]["history_state"] == "insufficient"
    assert payload["s2"]["bars_count"] == 0
    assert payload["s2"]["last_open_time_ms"] is None
    assert payload["fxcm_status"] == {"market": "closed", "price": "ok", "ohlcv": "ok"}

    # другий прогін у той же час -> має бути rate-limited
    await requester._run_once()
    assert len(fake_redis.published) == 1

    # пересуваємось за cooldown -> знову publish
    monkeypatch.setattr(
        "app.fxcm_warmup_requester.utc_now_ms", lambda: int((t0 + 901) * 1000)
    )
    await requester._run_once()
    assert len(fake_redis.published) == 2


@pytest.mark.asyncio
async def test_requester_publishes_backfill_when_tail_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = _FakeRedis()

    monkeypatch.setattr("app.fxcm_warmup_requester.SMC_RUNTIME_PARAMS", {"limit": 300})

    # Багато барів є, але хвіст старий
    now_ms = 1_700_000_000_000
    last_open_ms = now_ms - (10 * 60_000)
    df = pd.DataFrame(
        [
            {
                "open_time": last_open_ms / 1000.0,
                "close_time": last_open_ms / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            }
        ]
        * 300
    )
    fake_store = _FakeStore(df=df)

    monkeypatch.setattr("app.fxcm_warmup_requester.utc_now_ms", lambda: int(now_ms))
    monkeypatch.setattr(
        "app.fxcm_warmup_requester.get_fxcm_feed_state", lambda: _FakeFeed("open")
    )

    requester = FxcmWarmupRequester(
        redis=fake_redis,  # type: ignore[arg-type]
        store=fake_store,  # type: ignore[arg-type]
        allowed_pairs={("xauusd", "1m")},
        # Для stale_tail тесту тримаємо дефолтний desired-limit (300 барів).
        min_history_bars_by_symbol={"xauusd": 0},
        cooldown_sec=1,
        stale_k=3.0,
    )

    await requester._run_once()
    assert len(fake_redis.published) == 1
    assert fake_redis.published[0][0] == "fxcm:commands"
    payload = json.loads(fake_redis.published[0][1])
    # Для TF=1m використовуємо fallback на warmup, бо backfill може бути
    # не підтриманий у конекторі (tick TF).
    assert payload["type"] == "fxcm_warmup"
    assert payload["reason"] == "stale_tail"
    assert payload["s2"]["history_state"] == "stale_tail"
    assert payload["s2"]["last_open_time_ms"] == last_open_ms
    assert payload["fxcm_status"] == {"market": "open", "price": "ok", "ohlcv": "ok"}


@pytest.mark.asyncio
async def test_requester_publishes_warmup_when_tail_has_internal_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = _FakeRedis()

    # Зменшуємо desired-limit, щоб S2 не класифікував це як insufficient.
    monkeypatch.setattr("app.fxcm_warmup_requester.SMC_RUNTIME_PARAMS", {"limit": 3})

    now_ms = 1_700_000_000_000
    # Tail свіжий, але open_time має пропуски (2 хв крок замість 1 хв).
    base = now_ms - (5 * 60_000)
    df = pd.DataFrame(
        [
            {
                "open_time": (base + 0 * 120_000) / 1000.0,
                "close_time": (base + 0 * 120_000) / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            },
            {
                "open_time": (base + 1 * 120_000) / 1000.0,
                "close_time": (base + 1 * 120_000) / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            },
            {
                "open_time": (base + 2 * 120_000) / 1000.0,
                "close_time": (base + 2 * 120_000) / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            },
        ]
    )
    fake_store = _FakeStore(df=df)

    monkeypatch.setattr("app.fxcm_warmup_requester.utc_now_ms", lambda: int(now_ms))
    monkeypatch.setattr(
        "app.fxcm_warmup_requester.get_fxcm_feed_state", lambda: _FakeFeed("open")
    )

    requester = FxcmWarmupRequester(
        redis=fake_redis,  # type: ignore[arg-type]
        store=fake_store,  # type: ignore[arg-type]
        allowed_pairs={("xauusd", "1m")},
        min_history_bars_by_symbol={"xauusd": 0},
        cooldown_sec=1,
        stale_k=3.0,
    )

    await requester._run_once()
    assert len(fake_redis.published) == 1
    payload = json.loads(fake_redis.published[0][1])
    assert payload["type"] == "fxcm_warmup"
    assert payload["reason"] == "gappy_tail"
    assert payload["s2"]["history_state"] == "gappy_tail"
    assert int(payload["s2"]["gaps_count"]) >= 1


@pytest.mark.asyncio
async def test_requester_still_repairs_gappy_tail_when_market_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = _FakeRedis()

    # Зменшуємо desired-limit, щоб тест був маленьким.
    monkeypatch.setattr("app.fxcm_warmup_requester.SMC_RUNTIME_PARAMS", {"limit": 3})

    now_ms = 1_700_000_000_000
    base = now_ms - (5 * 60_000)
    # Пропуски в open_time (2 хв крок замість 1 хв).
    df = pd.DataFrame(
        [
            {
                "open_time": (base + 0 * 120_000) / 1000.0,
                "close_time": (base + 0 * 120_000) / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            },
            {
                "open_time": (base + 1 * 120_000) / 1000.0,
                "close_time": (base + 1 * 120_000) / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            },
            {
                "open_time": (base + 2 * 120_000) / 1000.0,
                "close_time": (base + 2 * 120_000) / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            },
        ]
    )
    fake_store = _FakeStore(df=df)

    monkeypatch.setattr("app.fxcm_warmup_requester.utc_now_ms", lambda: int(now_ms))
    monkeypatch.setattr(
        "app.fxcm_warmup_requester.get_fxcm_feed_state", lambda: _FakeFeed("closed")
    )

    requester = FxcmWarmupRequester(
        redis=fake_redis,  # type: ignore[arg-type]
        store=fake_store,  # type: ignore[arg-type]
        allowed_pairs={("xauusd", "1m")},
        min_history_bars_by_symbol={"xauusd": 0},
        cooldown_sec=1,
        stale_k=3.0,
    )

    await requester._run_once()
    assert len(fake_redis.published) == 1
    payload = json.loads(fake_redis.published[0][1])
    assert payload["type"] == "fxcm_warmup"
    assert payload["reason"] == "gappy_tail"
    assert payload["s2"]["history_state"] == "gappy_tail"


@pytest.mark.asyncio
async def test_requester_publishes_warmup_when_tail_is_non_monotonic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = _FakeRedis()

    # Зменшуємо desired-limit, щоб не потрапити в insufficient.
    monkeypatch.setattr("app.fxcm_warmup_requester.SMC_RUNTIME_PARAMS", {"limit": 3})

    now_ms = 1_700_000_000_000
    # Важливо: тримаємо last_open_time в межах stale_k*tf (3х1m),
    # щоб кейс "бар позаду" не маскувався станом stale_tail.
    base = now_ms - (2 * 60_000)
    # Open_time "назад" на третьому елементі.
    df = pd.DataFrame(
        [
            {
                "open_time": (base + 0 * 60_000) / 1000.0,
                "close_time": (base + 0 * 60_000) / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            },
            {
                "open_time": (base + 2 * 60_000) / 1000.0,
                "close_time": (base + 2 * 60_000) / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            },
            {
                "open_time": (base + 1 * 60_000) / 1000.0,
                "close_time": (base + 1 * 60_000) / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            },
        ]
    )
    fake_store = _FakeStore(df=df)

    monkeypatch.setattr("app.fxcm_warmup_requester.utc_now_ms", lambda: int(now_ms))
    monkeypatch.setattr(
        "app.fxcm_warmup_requester.get_fxcm_feed_state", lambda: _FakeFeed("open")
    )

    requester = FxcmWarmupRequester(
        redis=fake_redis,  # type: ignore[arg-type]
        store=fake_store,  # type: ignore[arg-type]
        allowed_pairs={("xauusd", "1m")},
        min_history_bars_by_symbol={"xauusd": 0},
        cooldown_sec=1,
        stale_k=3.0,
    )

    await requester._run_once()
    assert len(fake_redis.published) == 1
    payload = json.loads(fake_redis.published[0][1])
    assert payload["type"] == "fxcm_warmup"
    assert payload["reason"] == "non_monotonic_tail"
    assert payload["s2"]["history_state"] == "non_monotonic_tail"
    assert int(payload["s2"]["non_monotonic_count"]) >= 1


@pytest.mark.asyncio
async def test_requester_resets_active_issue_when_state_becomes_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = _FakeRedis()

    monkeypatch.setattr("app.fxcm_warmup_requester.SMC_RUNTIME_PARAMS", {"limit": 300})

    # 1) insufficient -> warmup (publish)
    # 2) ok -> clear active issue (no publish)
    # 3) insufficient -> warmup знову (publish без очікування cooldown)
    df_ok = pd.DataFrame(
        [
            {
                "open_time": 1_700_000_000.0,
                "close_time": 1_700_000_000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            }
        ]
        * 300
    )

    fake_store = _SequencedStore([None, df_ok, None])

    t0 = 1_700_000_000.0
    monkeypatch.setattr("app.fxcm_warmup_requester.utc_now_ms", lambda: int(t0 * 1000))
    monkeypatch.setattr(
        "app.fxcm_warmup_requester.get_fxcm_feed_state", lambda: _FakeFeed("open")
    )

    requester = FxcmWarmupRequester(
        redis=fake_redis,  # type: ignore[arg-type]
        store=fake_store,  # type: ignore[arg-type]
        allowed_pairs={("xauusd", "1m")},
        # Для тесту reset достатньо desired-limit (300 барів).
        min_history_bars_by_symbol={"xauusd": 0},
        commands_channel="fxcm:commands",
        poll_sec=60,
        cooldown_sec=900,
        stale_k=3.0,
    )

    await requester._run_once()
    assert len(fake_redis.published) == 1
    assert json.loads(fake_redis.published[0][1])["type"] == "fxcm_warmup"

    await requester._run_once()
    assert len(fake_redis.published) == 1

    await requester._run_once()
    assert len(fake_redis.published) == 2
    assert json.loads(fake_redis.published[1][1])["type"] == "fxcm_warmup"


@pytest.mark.asyncio
async def test_requester_prefetches_when_ok_but_contract_wants_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = _FakeRedis()

    monkeypatch.setattr("app.fxcm_warmup_requester.SMC_RUNTIME_PARAMS", {"limit": 300})

    # Є мінімум (300 барів) і хвіст свіжий -> state=ok,
    # але контракт вимагає більше -> має бути prefetch_history.
    now_ms = 1_700_000_000_000
    last_open_ms = now_ms - (1 * 60_000)
    df_ok = pd.DataFrame(
        [
            {
                "open_time": last_open_ms / 1000.0,
                "close_time": last_open_ms / 1000.0,
                "open": 1,
                "high": 1,
                "low": 1,
                "close": 1,
                "volume": 1,
            }
        ]
        * 300
    )
    fake_store = _FakeStore(df=df_ok)

    monkeypatch.setattr("app.fxcm_warmup_requester.utc_now_ms", lambda: int(now_ms))
    monkeypatch.setattr(
        "app.fxcm_warmup_requester.get_fxcm_feed_state", lambda: _FakeFeed("open")
    )

    requester = FxcmWarmupRequester(
        redis=fake_redis,  # type: ignore[arg-type]
        store=fake_store,  # type: ignore[arg-type]
        allowed_pairs={("xauusd", "1m")},
        min_history_bars_by_symbol={"xauusd": 2000},
        cooldown_sec=1,
        stale_k=3.0,
    )

    await requester._run_once()
    assert len(fake_redis.published) == 1
    payload = json.loads(fake_redis.published[0][1])
    assert payload["type"] == "fxcm_warmup"
    assert payload["reason"] == "prefetch_history"
    assert payload["s2"]["history_state"] == "ok"
    # Режим prefetch: нарощуємо поступово від поточного bars_count (300) кроком desired-limit (300).
    assert payload["lookback_bars"] == 600
