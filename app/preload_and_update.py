"""Планувальник періодичного prefilter та історичного preload.

Шлях: ``app/preload_and_update.py``

Фонові задачі:
    • periodic_prefilter_and_update — періодично виконує Stage1 prefilter і оновлює fast_symbols.
    • preload_1m_history — масове завантаження останніх 1m барів для холодного старту RAM шару.
    • preload_daily_levels — вибірка денних барів для глобальних рівнів / статистик.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import Iterable
from typing import Any, cast

import aiohttp
import pandas as pd
from rich.console import Console
from rich.logging import RichHandler

from config.config import (
    MANUAL_FAST_SYMBOLS_SEED,
    PREFILTER_INTERVAL_SEC,
    PRELOAD_1M_LOOKBACK_INIT,
    SCREENING_LOOKBACK,
)
from data.unified_store import UnifiedDataStore
from stage1.optimized_asset_filter import get_filtered_assets

# Ніякої нормалізації часу: працюємо із сирими timestamp з Binance як є

# ───────────────────────────── Логування ─────────────────────────────
logger = logging.getLogger("app.preload")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    # show_path=True для чіткої вказівки файлу/рядка у WARNING/ERROR
    logger.addHandler(RichHandler(console=Console(stderr=True), show_path=True))
    logger.propagate = False


def merge_prefilter_with_manual(
    filtered: Iterable[str],
    manual_seed: Iterable[str],
    manual_overrides: Iterable[str],
) -> tuple[list[str], set[str]]:
    """Об'єднує результати префільтра з ручним whitelist.

    Args:
        filtered: символи, що пройшли автоматичний фільтр.
        manual_seed: початковий статичний список із конфігу.
        manual_overrides: користувацькі ручні символи (Redis).

    Returns:
        Кортеж (повний список у нижньому регістрі, набір символів,
        доданих саме ручними списками).
    """

    filtered_set = {str(sym).lower() for sym in filtered if sym}
    manual_cfg_set = {str(sym).lower() for sym in manual_seed if sym}
    manual_override_set = {str(sym).lower() for sym in manual_overrides if sym}
    combined = filtered_set | manual_cfg_set | manual_override_set
    manual_added = combined - filtered_set
    return sorted(combined), manual_added


# ── Фоновий таск: періодичне оновлення fast_symbols через prefilter ─────────
async def periodic_prefilter_and_update(
    cache,
    session: aiohttp.ClientSession,
    thresholds,
    interval: int = PREFILTER_INTERVAL_SEC,
    buffer: UnifiedDataStore | None = None,
    lookback: int = PRELOAD_1M_LOOKBACK_INIT,
):
    """
    Періодично виконує prefilter та оновлює fast_symbols у Redis.
    Додає preload історії для нових активів.
    """
    # Початковий набір символів
    manual_seed_cfg = MANUAL_FAST_SYMBOLS_SEED
    try:
        manual_overrides = await cache.get_manual_fast_symbols()
    except AttributeError:
        manual_overrides = []

    initial_combined, manual_added_initial = merge_prefilter_with_manual(
        await cache.get_fast_symbols(), manual_seed_cfg, manual_overrides
    )
    initial_symbols = set(initial_combined)
    prev_symbols = initial_symbols.copy()
    if manual_added_initial:
        logger.info(
            "Prefilter bootstrap: додано ручні символи",
            extra={
                "added": sorted(list(manual_added_initial))[:6],
                "count": len(manual_added_initial),
            },
        )
    if initial_symbols:
        try:
            await cache.set_fast_symbols(
                sorted(initial_symbols), ttl=max(interval * 2, interval + 60)
            )
            logger.debug(
                "Prefilter bootstrap: продовжено TTL існуючого whitelist",
                extra={
                    "count": len(initial_symbols),
                    "head": sorted(list(initial_symbols))[:3],
                },
            )
        except Exception as exc:
            logger.warning("Не вдалося оновити TTL початкового whitelist: %s", exc)

    # Затримка перед першим оновленням (щоб уникнути конфлікту з первинним префільтром)
    await asyncio.sleep(interval)  # Чекаємо звичайний інтервал (600 сек)
    while True:
        batch_id = uuid.uuid4().hex[:8]
        t0 = time.perf_counter()
        try:
            logger.info(
                "🔄 Старт prefilter-циклу",
                extra={
                    "batch_id": batch_id,
                    "interval_sec": interval,
                    "prev_symbols_count": len(prev_symbols),
                    "prev_head": sorted(list(prev_symbols))[:3],
                    "prev_tail": sorted(list(prev_symbols))[-3:],
                },
            )

            fast_symbols = await get_filtered_assets(
                session=session,
                cache_handler=cache,
                thresholds=thresholds,
                dynamic=True,
            )

            try:
                manual_overrides = await cache.get_manual_fast_symbols()
            except AttributeError:
                manual_overrides = []

            combined_list, manual_added = merge_prefilter_with_manual(
                fast_symbols or [], manual_seed_cfg, manual_overrides
            )

            if combined_list:
                fast_symbols = combined_list
                current_symbols = set(fast_symbols)

                fast_symbols = sorted(current_symbols)
                await cache.set_fast_symbols(
                    fast_symbols, ttl=interval * 2
                )  # TTL 1200 сек

                added = sorted(list(current_symbols - prev_symbols))
                removed = sorted(list(prev_symbols - current_symbols))
                logger.info(
                    "Prefilter: оновлено fast_symbols",
                    extra={
                        "batch_id": batch_id,
                        "count": len(fast_symbols),
                        "head": fast_symbols[:3],
                        "tail": fast_symbols[-3:],
                        "added_count": len(added),
                        "removed_count": len(removed),
                        "added_head": added[:3],
                        "added_tail": added[-3:],
                        "removed_head": removed[:3],
                        "removed_tail": removed[-3:],
                        "manual_added": sorted(list(manual_added))[:6],
                    },
                )

                # ── preload для нових активів ──────────────────────────────
                if buffer is not None:
                    # Знаходимо ТІЛЬКИ нові символи
                    new_symbols = current_symbols - prev_symbols

                    # Додаємо debug-лог для відстеження станів символів
                    logger.debug(
                        "Стан символів",
                        extra={
                            "batch_id": batch_id,
                            "current": len(current_symbols),
                            "previous": len(prev_symbols),
                            "new": len(new_symbols),
                            "new_head": sorted(list(new_symbols))[:3],
                            "new_tail": sorted(list(new_symbols))[-3:],
                        },
                    )
                    if new_symbols:
                        new_symbols_list = sorted(list(new_symbols))
                        logger.info(
                            "Preload історії для нових активів",
                            extra={
                                "batch_id": batch_id,
                                "count": len(new_symbols_list),
                                "head": new_symbols_list[:3],
                                "tail": new_symbols_list[-3:],
                                "lookback": lookback,
                            },
                        )
                        await preload_1m_history(
                            new_symbols_list, buffer, lookback=lookback, session=session
                        )

                # Оновлюємо попередні символи
                prev_symbols = current_symbols
            else:
                manual_added = set()
                if prev_symbols:
                    logger.warning(
                        "Prefilter повернув порожній список, використовуємо попередній whitelist",
                        extra={
                            "batch_id": batch_id,
                            "count": len(prev_symbols),
                        },
                    )
                    await cache.set_fast_symbols(
                        sorted(prev_symbols), ttl=max(interval, interval // 2)
                    )
                else:
                    logger.debug(
                        "Prefilter повернув порожній список, а історії whitelist немає.",
                        extra={"batch_id": batch_id},
                    )
        except Exception as e:
            logger.warning(
                "Помилка оновлення prefilter",
                extra={"batch_id": batch_id, "error": str(e)},
            )
            if prev_symbols:
                try:
                    await cache.set_fast_symbols(
                        sorted(prev_symbols), ttl=max(interval, interval // 2)
                    )
                except Exception as ttl_exc:
                    logger.warning(
                        "Не вдалося продовжити TTL whitelist після помилки prefilter: %s",
                        ttl_exc,
                    )
        finally:
            t1 = time.perf_counter()
            logger.info(
                "✅ Завершення prefilter-циклу",
                extra={"batch_id": batch_id, "duration_sec": round(t1 - t0, 3)},
            )

        await asyncio.sleep(interval)  # 600 сек


# ── Preload історії для Stage1 ─────────────────────────────────────────────
async def _fetch_batch(
    symbols: list[str], interval: str, limit: int, session: aiohttp.ClientSession
) -> dict[str, pd.DataFrame]:
    """Пакетне завантаження даних для групи символів з обмеженням паралелізму."""
    if not symbols:
        return {}

    batch_id = uuid.uuid4().hex[:8]
    t0 = time.perf_counter()
    logger.info(
        "Старт пакетного завантаження",
        extra={
            "batch_id": batch_id,
            "symbols": len(symbols),
            "interval": interval,
            "limit": limit,
            "head": symbols[:3],
            "tail": symbols[-3:],
        },
    )

    semaphore = asyncio.Semaphore(5)  # Обмеження для денних даних
    results: dict[str, pd.DataFrame] = {}

    async def fetch_single(symbol: str):
        async with semaphore:
            try:
                df = await _fetch_klines(symbol, interval, limit, session)
                if df is not None and not df.empty:
                    results[symbol] = df
                    logger.debug(
                        "✅ Пакетне завантаження: дані отримано",
                        extra={
                            "batch_id": batch_id,
                            "symbol": symbol,
                            "rows": len(df),
                        },
                    )
                else:
                    results[symbol] = pd.DataFrame()
                    logger.warning(
                        "❌ Пакетне завантаження: порожні дані",
                        extra={"batch_id": batch_id, "symbol": symbol},
                    )
            except Exception as e:
                results[symbol] = pd.DataFrame()
                logger.warning(
                    "Помилка пакетного завантаження",
                    extra={"batch_id": batch_id, "symbol": symbol, "error": str(e)},
                )
            await asyncio.sleep(0.1)  # Невелика затримка між запитами

    tasks = [fetch_single(symbol) for symbol in symbols]
    await asyncio.gather(*tasks, return_exceptions=True)

    ok = sum(1 for df in results.values() if not df.empty)
    t1 = time.perf_counter()
    logger.info(
        "Завершено пакетне завантаження",
        extra={
            "batch_id": batch_id,
            "ok": ok,
            "total": len(symbols),
            "duration_sec": round(t1 - t0, 3),
        },
    )

    return results


async def _fetch_klines(
    symbol: str,
    interval: str,
    limit: int,
    session: aiohttp.ClientSession,
    start_time: int | None = None,
    end_time: int | None = None,
) -> pd.DataFrame | None:
    """Асинхронне отримання klines даних з Binance REST API."""
    params: dict[str, str | int] = {
        "symbol": symbol.upper(),
        "interval": interval,
        "limit": min(int(limit), 1000),
    }

    if start_time is not None:
        params["startTime"] = int(start_time)
    if end_time is not None:
        params["endTime"] = int(end_time)

    url = "https://api.binance.com/api/v3/klines"

    try:
        async with session.get(
            url, params=params, timeout=aiohttp.ClientTimeout(total=10)
        ) as response:
            if response.status == 200:
                data = await response.json()

                if not data:
                    logger.warning(
                        "Порожня відповідь від Binance", extra={"symbol": symbol}
                    )
                    return pd.DataFrame()

                df = pd.DataFrame(
                    data,
                    columns=[
                        "open_time",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "close_time",
                        "quote_asset_volume",
                        "trades",
                        "taker_buy_base",
                        "taker_buy_quote",
                        "ignore",
                    ],
                )

                # Конвертація типів даних
                numeric_cols = [
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "quote_asset_volume",
                ]
                for col in numeric_cols:
                    df[col] = pd.to_numeric(df[col], errors="coerce")

                df["open_time"] = pd.to_numeric(df["open_time"])
                df["close_time"] = pd.to_numeric(df["close_time"])
                df["trades"] = pd.to_numeric(df["trades"])

                # Логування структури фрейму
                if logger.isEnabledFor(logging.DEBUG):
                    try:
                        logger.debug(
                            "Колонки DataFrame",
                            extra={
                                "symbol": symbol,
                                "cols": list(map(str, df.columns.tolist())),
                            },
                        )
                    except Exception:
                        pass

                # Перевірка цілісності часових міток
                if len(df) > 1:
                    open_time_series = pd.to_numeric(df["open_time"], errors="coerce")
                    time_diff = open_time_series.diff().iloc[1:].astype("float64")
                    expected_interval = float(_get_interval_ms(interval))
                    deviation = (time_diff - expected_interval).abs()
                    anomalies = time_diff[deviation > 1000.0]  # Допуск 1 секунда

                    if not anomalies.empty:
                        logger.warning(
                            "Аномалії часу",
                            extra={
                                "symbol": symbol,
                                "interval": interval,
                                "first": anomalies.head(3).astype(int).tolist(),
                            },
                        )

                # Детальний лог тільки для DEBUG
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(
                        "Отримано klines",
                        extra={
                            "symbol": symbol,
                            "interval": interval,
                            "rows": len(df),
                            "ts_head": df["open_time"].head(3).astype(int).tolist(),
                            "ts_tail": df["open_time"].tail(3).astype(int).tolist(),
                        },
                    )

                return df
            else:
                txt = await response.text()
                logger.debug(
                    "HTTP помилка від Binance",
                    extra={
                        "symbol": symbol,
                        "interval": interval,
                        "status": response.status,
                        "body_head": txt[:200],
                    },
                )
                return None

    except TimeoutError:
        logger.error("Таймаут запиту", extra={"symbol": symbol, "interval": interval})
        return None
    except Exception as e:
        logger.error(
            "Критична помилка під час запиту",
            extra={"symbol": symbol, "interval": interval, "error": str(e)},
        )
        return None


def _get_interval_ms(interval: str) -> int:
    """Конвертує інтервал у мілісекунди."""
    intervals = {
        "1m": 60000,
        "3m": 180000,
        "5m": 300000,
        "15m": 900000,
        "30m": 1800000,
        "1h": 3600000,
        "2h": 7200000,
        "4h": 14400000,
        "6h": 21600000,
        "8h": 28800000,
        "12h": 43200000,
        "1d": 86400000,
    }
    return intervals.get(interval, 60000)


async def preload_1m_history(
    fast_symbols: list[str],
    store: UnifiedDataStore,
    lookback: int = SCREENING_LOOKBACK,
    session: aiohttp.ClientSession | None = None,
) -> dict:
    """Масове завантаження 1х хвилинної історії для списку символів.

    Args:
        fast_symbols: Список символів для завантаження
        store: UnifiedDataStore (не словник!)
        lookback: Глибина історії в барах
        session: AIOHTTP сесія (створить нову якщо None)

    Returns:
        Статистика завантаження
    """
    if not fast_symbols:
        logger.warning("Список символів для preload порожній")
        return {"total": 0, "success": 0, "failed": 0, "duration": 0}

    close_session = False
    if session is None:
        session = aiohttp.ClientSession()
        close_session = True

    stats: dict[str, Any] = {
        "total": len(fast_symbols),
        "success": 0,
        "failed": 0,
        "start_time": time.time(),
        "symbols_loaded": [],
    }

    try:
        semaphore = asyncio.Semaphore(10)

        async def fetch_with_semaphore(symbol):
            async with semaphore:
                df = await _fetch_klines(symbol, "1m", lookback, session)
                if df is not None and not df.empty:
                    # Використовуємо правильний API UnifiedDataStore
                    await store.put_bars(symbol, "1m", df)
                    stats["success"] = cast(int, stats.get("success", 0)) + 1
                    stats["symbols_loaded"].append(symbol)

                    logger.debug(
                        f"✅ {symbol}: {len(df)} барів | "
                        f"Останній: {pd.to_datetime(df['open_time'].iloc[-1], unit='ms').strftime('%H:%M:%S')}"
                    )
                    return True
                else:
                    stats["failed"] = cast(int, stats.get("failed", 0)) + 1
                    logger.warning(f"❌ {symbol}: не вдалося завантажити")
                    return False

        tasks = [fetch_with_semaphore(symbol) for symbol in fast_symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                stats["failed"] = cast(int, stats.get("failed", 0)) + 1
                logger.error(f"Помилка у {fast_symbols[i]}: {result}")

    finally:
        if close_session:
            await session.close()

    end_time = time.time()
    start_time_val = cast(float, stats.get("start_time", end_time))
    stats["end_time"] = end_time
    stats["duration"] = float(end_time - start_time_val)

    success = cast(int, stats.get("success", 0))
    total = cast(int, stats.get("total", 0))
    success_rate = (success / total * 100) if total > 0 else 0.0
    logger.info(
        f"📊 Preload 1m: {stats['success']}/{stats['total']} "
        f"({success_rate:.1f}%) | Час: {stats['duration']:.2f}с"
    )

    return stats
