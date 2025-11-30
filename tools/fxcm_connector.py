"""Конектор для Dukascopy та утиліти публікації OHLCV у Redis.

Реалізує клієнт Dukascopy, нормалізацію барів, публікацію у канал
`fxcm:ohlcv` та базовий стрімінговий цикл poll → publish з контролем
`open_time` і обрізанням історії до заданого ліміту.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

import pandas as pd
from redis.asyncio import Redis

from data.duka import fetch_duka
from data.fxcm_ingestor import FXCM_OHLCV_CHANNEL

logger = logging.getLogger("tools.fxcm_connector")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

STORE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time"]
BarSignature = tuple[int, float, float, float, float, float]
BarsCallback = Callable[[str, pd.DataFrame], Awaitable[None]]
SymbolKey = tuple[str, str]


class FXConnector(Protocol):
    """Протокол для історичних конекторів Stage1."""

    async def get_history(
        self,
        symbol: str,
        period: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> pd.DataFrame: ...

    async def stream(
        self,
        symbols: Sequence[str],
        period: str,
        store_interval: str,
        interval_ms: int,
        *,
        poll_seconds: int,
        lookback_minutes: int,
        limit: int,
        last_open_times: dict[SymbolKey, int],
        last_partial: dict[SymbolKey, BarSignature] | None = None,
        on_chunk: BarsCallback | None = None,
    ) -> None: ...

    def close(self) -> None: ...


def _fx_symbol(symbol: str) -> str:
    sym = symbol.upper()
    if "/" in sym:
        return sym
    if len(sym) == 6:
        return f"{sym[:3]}/{sym[3:]}"
    return sym


def _normalize_history_to_ohlcv(raw: pd.DataFrame, interval_ms: int) -> pd.DataFrame:
    """Приводить сирі дані до колонок UnifiedDataStore з open/close time."""

    if raw is None or raw.empty:
        return pd.DataFrame(columns=STORE_COLS)
    work = raw.copy()
    if "ts" not in work.columns:
        raise ValueError("Очікуємо колонку 'ts' у сирому фреймі")
    ts = pd.to_datetime(work["ts"], utc=True, errors="coerce")
    work["open_time"] = (ts.astype("int64", copy=False) // 1_000_000).astype("int64")
    work["close_time"] = work["open_time"] + (interval_ms - 1)
    for col in ("open", "high", "low", "close"):
        if col not in work.columns:
            raise ValueError(f"У сирому фреймі відсутня колонка {col}")
        work[col] = pd.to_numeric(work[col], errors="coerce")
    if "volume" not in work.columns:
        work["volume"] = pd.NA
    work["volume"] = pd.to_numeric(work["volume"], errors="coerce")
    work = work.dropna(subset=["open_time", "open", "high", "low", "close"])
    work = work.sort_values("open_time").reset_index(drop=True)
    return work[STORE_COLS]


def truncate_bars(frame: pd.DataFrame, limit: int) -> pd.DataFrame:
    """Обрізає DataFrame до останніх ``limit`` барів."""

    if limit is None or limit <= 0 or frame.empty or len(frame) <= limit:
        return frame.reset_index(drop=True)
    return frame.tail(limit).reset_index(drop=True)


def _bar_signature(row: pd.Series) -> BarSignature:
    return (
        int(row.get("open_time", 0)),
        float(row.get("open", 0.0) or 0.0),
        float(row.get("high", 0.0) or 0.0),
        float(row.get("low", 0.0) or 0.0),
        float(row.get("close", 0.0) or 0.0),
        float(row.get("volume", 0.0) or 0.0),
    )


def _pick_incremental_rows(
    frame: pd.DataFrame,
    key: SymbolKey,
    last_open_times: dict[SymbolKey, int],
    partial_cache: dict[SymbolKey, BarSignature],
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=STORE_COLS)
    updates: list[pd.DataFrame] = []
    last_closed = last_open_times.get(key)
    newer = frame if last_closed is None else frame[frame["open_time"] > last_closed]
    if not newer.empty:
        updates.append(newer)
        last_open_times[key] = int(newer["open_time"].iloc[-1])
        partial_cache.pop(key, None)
        latest = frame[frame["open_time"] == last_open_times[key]]
        if not latest.empty:
            partial_cache[key] = _bar_signature(latest.iloc[-1])
        return pd.concat(updates, ignore_index=True)

    reference = last_open_times.get(key)
    if reference is None:
        return pd.DataFrame(columns=STORE_COLS)
    same = frame[frame["open_time"] == reference]
    if same.empty:
        return pd.DataFrame(columns=STORE_COLS)
    candidate = same.tail(1)
    signature = _bar_signature(candidate.iloc[-1])
    if partial_cache.get(key) == signature:
        return pd.DataFrame(columns=STORE_COLS)
    partial_cache[key] = signature
    return candidate.reset_index(drop=True)


async def publish_ohlcv_to_redis(
    redis: Redis,
    symbol: str,
    interval: str,
    bars: pd.DataFrame,
    *,
    limit: int,
    channel: str = FXCM_OHLCV_CHANNEL,
) -> int:
    """Публікує бари у Redis Pub/Sub для інжестора FXCM."""

    trimmed = truncate_bars(bars, limit)
    if trimmed.empty:
        return 0
    records = cast(list[dict[str, Any]], trimmed.to_dict(orient="records"))
    payload = {
        "symbol": symbol.lower(),
        "tf": interval.lower(),
        "bars": [
            {
                "open_time": int(record["open_time"]),
                "close_time": int(record["close_time"]),
                "open": float(record["open"]),
                "high": float(record["high"]),
                "low": float(record["low"]),
                "close": float(record["close"]),
                "volume": float(record.get("volume") or 0.0),
            }
            for record in records
        ],
    }
    message = json.dumps(payload, ensure_ascii=False)
    await redis.publish(channel, message)
    return len(trimmed)


class FXBaseConnector:
    """Базовий конектор із готовим стрімінговим циклом."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def get_history(
        self,
        symbol: str,
        period: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> pd.DataFrame:
        raise NotImplementedError

    async def stream(
        self,
        symbols: Sequence[str],
        period: str,
        store_interval: str,
        interval_ms: int,
        *,
        poll_seconds: int,
        lookback_minutes: int,
        limit: int,
        last_open_times: dict[SymbolKey, int],
        last_partial: dict[SymbolKey, BarSignature] | None = None,
        on_chunk: BarsCallback | None = None,
    ) -> None:
        poll_seconds = max(1, int(poll_seconds))
        lookback_minutes = max(1, int(lookback_minutes))
        logger.info(
            "[Stream] Старт конектора %s symbols=%d poll=%ds lookback=%dmin",
            self.__class__.__name__,
            len(symbols),
            poll_seconds,
            lookback_minutes,
        )
        partial_cache = last_partial if last_partial is not None else {}
        try:
            while True:
                end_dt = datetime.now(tz=UTC)
                start_dt = end_dt - timedelta(minutes=lookback_minutes)
                for symbol in symbols:
                    try:
                        df = await self.get_history(symbol, period, start_dt, end_dt)
                    except Exception as exc:
                        logger.warning(
                            "[Stream] Помилка get_history(%s): %s", symbol, exc
                        )
                        continue
                    normalized = _normalize_history_to_ohlcv(df, interval_ms)
                    key = (symbol.lower(), store_interval)
                    incremental = _pick_incremental_rows(
                        normalized,
                        key,
                        last_open_times,
                        partial_cache,
                    )
                    if incremental.empty:
                        continue
                    incremental = truncate_bars(incremental, limit)
                    if incremental.empty:
                        continue
                    if on_chunk is not None:
                        try:
                            await on_chunk(symbol, incremental)
                        except Exception as exc:  # pragma: no cover - defensive
                            logger.warning(
                                "[Stream] on_chunk помилка для %s: %s", symbol, exc
                            )
                    await publish_ohlcv_to_redis(
                        self.redis,
                        symbol,
                        store_interval,
                        incremental,
                        limit=limit,
                    )
                await asyncio.sleep(poll_seconds)
        except asyncio.CancelledError:
            logger.info("[Stream] Зупинка %s (Cancelled)", self.__class__.__name__)
            raise

    def close(self) -> None:  # pragma: no cover - реалізується в нащадках
        return None


class DukascopyConnector(FXBaseConnector):
    """Резервний конектор через публічний датафід Dukascopy (M1)."""

    def __init__(self, redis: Redis) -> None:
        super().__init__(redis)

    async def get_history(
        self,
        symbol: str,
        period: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> pd.DataFrame:
        period_key = period.lower()
        if period_key != "m1":
            raise ValueError("Dukascopy конектор наразі підтримує лише M1")

        def _fetch() -> pd.DataFrame:
            rows = list(
                fetch_duka(
                    symbol=symbol.upper(),
                    tf="M1",
                    start_dt=start_dt,
                    end_dt=end_dt,
                    url_template=None,
                )
            )
            if not rows:
                return pd.DataFrame(
                    columns=["ts", "open", "high", "low", "close", "volume"]
                )
            df = pd.DataFrame(rows)
            if "ts" not in df.columns and "open_time" in df.columns:
                df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            return df[["ts", "open", "high", "low", "close", "volume"]]

        return await asyncio.to_thread(_fetch)


__all__ = [
    "FXConnector",
    "DukascopyConnector",
    "publish_ohlcv_to_redis",
    "truncate_bars",
    "_normalize_history_to_ohlcv",
]
