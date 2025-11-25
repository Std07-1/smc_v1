"""FXCM інжестор OHLCV-даних у UnifiedDataStore.

Шлях: ``data/fxcm_ingestor.py``

Призначення:
    • слухає Redis-канал з OHLCV-пакетами від окремого FXCM-конектора (Python 3.7);
    • перетворює JSON-повідомлення у DataFrame;
    • записує бари у UnifiedDataStore через put_bars(symbol, interval, bars).

Очікуваний формат повідомлення (JSON):
    {
      "symbol": "EURUSD",
      "tf": "1m",
      "bars": [
        {
          "open_time": 1764002100000,
          "close_time": 1764002159999,
          "open": 1.152495,
          "high": 1.152640,
          "low": 1.152450,
          "close": 1.152530,
          "volume": 149.0
        }
      ]
    }

Рішення:
    • інжестор не знає нічого про ForexConnect — тільки Redis;
    • нормалізація символів і tf (m1 → 1m) відбувається на стороні 3.7-конектора;
    • інжестор працює в тому ж процесі/loop, що й Stage1, використовуючи спільний UnifiedDataStore.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd
from redis.asyncio import Redis

from app.settings import settings
from data.unified_store import UnifiedDataStore

logger = logging.getLogger("fxcm_ingestor")


FXCM_OHLCV_CHANNEL = "fxcm:ohlcv"


def _bars_payload_to_df(bars: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """Конвертує список барів у DataFrame з очікуваними колонками.

    Навмисно не робимо складної валідації, щоб не гальмувати гарячий шлях.
    Перевірка схеми/монотонності покривається validate_on_write у UnifiedDataStore.
    """
    if not bars:
        return pd.DataFrame(
            columns=[
                "open_time",
                "close_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        )

    df = pd.DataFrame(bars)

    # Мінімальний sanity-check: потрібні базові колонки
    required_cols = {
        "open_time",
        "close_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }
    missing = required_cols - set(df.columns)
    if missing:
        logger.warning(
            "[FXCM_INGEST] Відсутні колонки у payload: %s, пропускаю пакет",
            sorted(missing),
        )
        return pd.DataFrame(
            columns=[
                "open_time",
                "close_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        )

    # Приводимо типи там, де це має значення для get_df/put_bars
    for col in ("open_time", "close_time"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Сортуємо по часу, щоб не ламати припущення merge/validate у сховищі
    df = df.sort_values("open_time").reset_index(drop=True)
    return df[
        [
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
        ]
    ]


async def run_fxcm_ingestor(
    store: UnifiedDataStore,
    *,
    redis_host: str | None = None,
    redis_port: int | None = None,
    channel: str = FXCM_OHLCV_CHANNEL,
    log_every_n: int = 1,
) -> None:
    """Основний цикл інжестора FXCM → UnifiedDataStore.

    Аргументи:
        store: Спільний UnifiedDataStore, який вже використовують Stage1/Stage2.
        redis_host: Хост Redis; за замовчуванням береться з app.settings.
        redis_port: Порт Redis; за замовчуванням береться з app.settings.
        channel: Назва Redis Pub/Sub каналу, з якого читаємо OHLCV-пакети.
        log_every_n: Як часто логувати успішний інжест (щоб уникнути спаму).
    """
    host = redis_host or settings.redis_host
    port = redis_port or settings.redis_port

    redis = Redis(host=host, port=port)
    pubsub = redis.pubsub()

    logger.info(
        "[FXCM_INGEST] Старт інжестора: host=%s port=%s channel=%s",
        host,
        port,
        channel,
    )

    await pubsub.subscribe(channel)

    processed = 0
    log_every_n = max(1, int(log_every_n))

    try:
        async for message in pubsub.listen():
            if message is None:
                continue

            mtype = message.get("type")
            if mtype != "message":
                # subscribe/unsubscribe та інші службові події ігноруємо
                continue

            raw_data = message.get("data")
            if not raw_data:
                continue

            try:
                if isinstance(raw_data, bytes):
                    payload = json.loads(raw_data.decode("utf-8"))
                elif isinstance(raw_data, str):
                    payload = json.loads(raw_data)
                else:
                    # Нестандартний тип від Redis — намагаємось привести до str
                    payload = json.loads(str(raw_data))
            except json.JSONDecodeError:
                logger.warning(
                    "[FXCM_INGEST] Некоректний JSON у повідомленні з каналу %s",
                    channel,
                )
                continue

            if not isinstance(payload, dict):
                logger.warning(
                    "[FXCM_INGEST] Очікував dict у payload, отримав %r",
                    type(payload),
                )
                continue

            symbol = payload.get("symbol")
            interval = payload.get("tf")
            bars = payload.get("bars")

            if not symbol or not interval or not isinstance(bars, Sequence):
                logger.warning(
                    "[FXCM_INGEST] Некоректний payload: symbol=%r interval=%r "
                    "bars_type=%r",
                    symbol,
                    interval,
                    type(bars),
                )
                continue

            # Усі внутрішні модулі очікують lower-case символи/таймфрейми
            symbol = str(symbol).lower()
            interval = str(interval).lower()

            df = _bars_payload_to_df(bars)
            if df.empty:
                continue

            try:
                await store.put_bars(symbol, interval, df)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[FXCM_INGEST] Помилка під час put_bars(%s, %s): %s",
                    symbol,
                    interval,
                    exc,
                )
                continue

            processed += len(df)
            if processed % log_every_n == 0:
                logger.info(
                    "[FXCM_INGEST] Інгестовано барів: %d (останній пакет: %s %s, rows=%d)",
                    processed,
                    symbol,
                    interval,
                    len(df),
                )
    except asyncio.CancelledError:
        # Очікуваний шлях завершення при зупинці пайплайна
        logger.info("[FXCM_INGEST] Отримано CancelledError, завершуємо роботу.")
    finally:
        try:
            await pubsub.unsubscribe(channel)
        except Exception:  # noqa: BLE001
            pass
        await pubsub.close()
        await redis.close()
        logger.info("[FXCM_INGEST] Інжестор FXCM зупинено коректно.")


__all__ = ["run_fxcm_ingestor"]
