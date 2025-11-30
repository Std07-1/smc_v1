"""Утиліта прогріву Stage1 за рахунок історії FXCM/Dukascopy."""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pandas as pd
from redis.asyncio import Redis

from config.config import (
    FXCM_FAST_SYMBOLS,
    FXCM_STREAM_LIMIT,
    FXCM_WARMUP_BARS,
)
from tools.fxcm_connector import (
    DukascopyConnector,
    FXConnector,
    _normalize_history_to_ohlcv,
    publish_ohlcv_to_redis,
    truncate_bars,
)

if TYPE_CHECKING:  # pragma: no cover
    from data.unified_store import UnifiedDataStore

logger = logging.getLogger("tools.fxcm_warmup")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

PERIOD_TO_MS = {"m1": 60_000, "m5": 300_000, "h1": 3_600_000}
PERIOD_TO_STORE_TF = {"m1": "1m", "m5": "5m", "h1": "1h"}


def _default_symbols() -> list[str]:
    return [sym.lower() for sym in FXCM_FAST_SYMBOLS if sym]


def _sanitize_symbols(symbols: Iterable[str]) -> list[str]:
    return [sym.lower().strip() for sym in symbols if sym and sym.strip()]


def _history_window(
    last_open_time_ms: int | None, number: int, interval_ms: int
) -> tuple[datetime, datetime]:
    end_dt = datetime.now(tz=UTC)
    base = end_dt - timedelta(milliseconds=max(1, number) * interval_ms)
    if last_open_time_ms is None:
        return base, end_dt
    start_candidate = datetime.fromtimestamp(
        (last_open_time_ms + interval_ms) / 1000.0, tz=UTC
    )
    start_dt = max(base, min(start_candidate, end_dt))
    if start_dt >= end_dt:
        start_dt = end_dt - timedelta(milliseconds=interval_ms)
    return start_dt, end_dt


async def _read_last_open(
    store: UnifiedDataStore,
    symbol: str,
    interval: str,
    cache: dict[tuple[str, str], int],
) -> int | None:
    key = (symbol, interval)
    if key in cache:
        return cache[key]
    last_bar = await store.get_last(symbol, interval)
    if last_bar and "open_time" in last_bar:
        try:
            value = int(last_bar["open_time"])
        except (TypeError, ValueError):
            return None
        cache[key] = value
        return value
    return None


async def _store_chunk(
    store: UnifiedDataStore,
    symbol: str,
    interval: str,
    chunk: pd.DataFrame,
    limit: int,
) -> None:
    if chunk.empty:
        return
    await store.put_bars(symbol, interval, chunk)
    await store.enforce_tail_limit(symbol, interval, limit)


async def _load_symbol_history(
    *,
    connector: FXConnector,
    store: UnifiedDataStore,
    redis_client: Redis,
    symbol: str,
    period: str,
    store_interval: str,
    interval_ms: int,
    number: int,
    limit: int,
    last_open_cache: dict[tuple[str, str], int],
) -> int:
    last_open = await _read_last_open(store, symbol, store_interval, last_open_cache)
    start_dt, end_dt = _history_window(last_open, number, interval_ms)
    if start_dt >= end_dt:
        return 0
    try:
        raw_df = await connector.get_history(symbol, period, start_dt, end_dt)
    except Exception as exc:
        logger.warning("[Warmup] Не вдалося отримати %s (%s): %s", symbol, period, exc)
        return 0
    normalized = _normalize_history_to_ohlcv(raw_df, interval_ms)
    if last_open is not None:
        normalized = normalized[normalized["open_time"] > last_open]
    normalized = truncate_bars(normalized, limit)
    if normalized.empty:
        return 0
    await store.put_bars(symbol, store_interval, normalized)
    await store.enforce_tail_limit(symbol, store_interval, limit)
    await publish_ohlcv_to_redis(
        redis_client,
        symbol,
        store_interval,
        normalized,
        limit=limit,
    )
    last_open_cache[(symbol, store_interval)] = int(normalized["open_time"].iloc[-1])
    return len(normalized)


async def warmup(
    symbols: Iterable[str],
    period: str,
    number: int,
    *,
    limit: int = FXCM_STREAM_LIMIT,
    store: UnifiedDataStore | None = None,
) -> int:
    """Прогріває UnifiedDataStore та публікує історію без локального стріму."""

    period_key = period.lower()
    if period_key not in PERIOD_TO_MS:
        raise ValueError(f"Непідтримуваний period '{period}'")
    store_interval = PERIOD_TO_STORE_TF[period_key]
    interval_ms = PERIOD_TO_MS[period_key]
    target_store = store
    owns_store = False
    if target_store is None:
        from app.main import bootstrap

        target_store = await bootstrap()
        owns_store = True
    assert target_store is not None
    redis_client: Redis = target_store.redis.r  # type: ignore[attr-defined]
    history_connector = DukascopyConnector(redis_client)
    cleaned_symbols = _sanitize_symbols(symbols)
    last_open_cache: dict[tuple[str, str], int] = {}
    success = 0
    try:
        for symbol in cleaned_symbols:
            added = await _load_symbol_history(
                connector=history_connector,
                store=target_store,
                redis_client=redis_client,
                symbol=symbol,
                period=period_key,
                store_interval=store_interval,
                interval_ms=interval_ms,
                number=number,
                limit=limit,
                last_open_cache=last_open_cache,
            )
            if added:
                logger.info(
                    "[Warmup] %s: записано %d барів (%s)", symbol, added, store_interval
                )
                success += 1
    finally:
        history_connector.close()
        if owns_store:
            try:
                await target_store.stop_maintenance()
            except Exception:  # pragma: no cover - best effort
                pass
    return success


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FXCM cold-start warmup")
    parser.add_argument(
        "--symbols", help="Символи через кому (default=FXCM_FAST_SYMBOLS)"
    )
    parser.add_argument("--period", default="m1", help="Таймфрейм для warmup")
    parser.add_argument(
        "--number", type=int, default=FXCM_WARMUP_BARS, help="Кількість барів"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=FXCM_STREAM_LIMIT,
        help="Максимум барів, які зберігаємо/публікуємо",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    symbols = (
        [s.strip() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else _default_symbols()
    )
    success = asyncio.run(
        warmup(
            symbols=symbols,
            period=args.period,
            number=args.number,
            limit=args.limit,
        )
    )
    if success <= 0:
        raise SystemExit("Жодного символу не вдалося прогріти")


if __name__ == "__main__":
    main()
