"""Лістенер FXCM price_stream (живі bid/ask/mid снапшоти).

Призначення:
    • підписується на Redis-канал із останніми тиковими цінами (fxcm:price_tik);
    • оновлює кеш `UnifiedDataStore`, роблячи live mid доступним для Stage1/UI;
    • логічно відокремлений від OHLCV-інжестора (fxcm:ohlcv).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Any

from redis.asyncio import Redis

from app.settings import settings
from data.unified_store import UnifiedDataStore

logger = logging.getLogger("fxcm_price_stream")
if not logger.handlers:  # guard від подвійного налаштування
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler())
    logger.propagate = False


def _handle_payload(store: UnifiedDataStore, payload: Mapping[str, Any] | None) -> bool:
    """Нормалізує повідомлення та прокидає його у кеш ціни."""

    if payload is None:
        return False
    snapshot = store.update_price_tick(payload)
    return snapshot is not None


async def run_fxcm_price_stream_listener(
    store: UnifiedDataStore,
    *,
    redis_host: str | None = None,
    redis_port: int | None = None,
    channel: str | None = None,
    log_every_n: int = 200,
) -> None:
    """Основний цикл лістенера fxcm:price_tik."""

    host = redis_host or settings.redis_host
    port = redis_port or settings.redis_port
    channel_name = (channel or settings.fxcm_price_tick_channel or "").strip()
    if not channel_name:
        channel_name = settings.fxcm_price_tick_channel

    redis = Redis(host=host, port=port)
    pubsub = redis.pubsub()

    logger.info(
        "[FXCM_PRICE] Старт лістенера price_stream host=%s port=%s channel=%s",
        host,
        port,
        channel_name,
    )

    await pubsub.subscribe(channel_name)
    processed = 0
    log_every_n = max(1, int(log_every_n))

    try:
        async for message in pubsub.listen():
            if not message:
                continue
            if message.get("type") != "message":
                continue
            raw_data = message.get("data")
            if raw_data is None:
                continue
            if isinstance(raw_data, bytes):
                raw_text = raw_data.decode("utf-8", errors="ignore")
            else:
                raw_text = str(raw_data)
            payload_obj: Mapping[str, Any] | None = None
            try:
                decoded = json.loads(raw_text)
                if isinstance(decoded, Mapping):
                    payload_obj = decoded
            except json.JSONDecodeError:
                logger.debug(
                    "[FXCM_PRICE] Некоректний JSON у повідомленні каналу %s",
                    channel_name,
                )
                continue
            stored = _handle_payload(store, payload_obj)
            if not stored:
                continue
            processed += 1
            if processed % log_every_n == 0:
                logger.debug(
                    "[FXCM_PRICE] Оновлено %d тикових снапшотів (channel=%s)",
                    processed,
                    channel_name,
                )
    except asyncio.CancelledError:
        logger.info("[FXCM_PRICE] Отримано CancelledError, завершуємо роботу")
        raise
    finally:
        try:
            await pubsub.unsubscribe(channel_name)
        except Exception:  # noqa: BLE001 - best effort
            pass
        await pubsub.close()
        await redis.close()
        logger.info("[FXCM_PRICE] Лістенер fxcm:price_tik зупинено коректно")


__all__ = ["run_fxcm_price_stream_listener"]
