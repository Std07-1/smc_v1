"""Телеметрійні утиліти для публікації стану у UI."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from core.serialization import json_dumps

if TYPE_CHECKING:  # pragma: no cover - лише для тайпінгу
    from data.unified_store import UnifiedDataStore

logger = logging.getLogger("app.telemetry")
if not logger.handlers:  # pragma: no cover - запобігання дублю логерів у тестах
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler())
    logger.propagate = False


def _hot_symbol_count(store: UnifiedDataStore) -> int | None:
    """Оцінює кількість гарячих символів на основі LRU з RAM шару."""

    try:
        ram_layer = getattr(store, "ram", None)
        if ram_layer is None:
            return None
        lru_cache = getattr(ram_layer, "_lru", None)
        if lru_cache is None:
            return None
        keys_iter = getattr(lru_cache, "keys", None)
        if keys_iter is None:
            return None
        symbols = {symbol for symbol, _interval in keys_iter()}
        return len(symbols)
    except Exception:
        return None


async def publish_ui_metrics(
    store: UnifiedDataStore,
    redis_pub: Any | None,
    *,
    channel: str = "ui.metrics",
    interval: float = 5.0,
    iteration_limit: int | None = None,
) -> None:
    """Періодично публікує метрики стану у Redis для UI."""

    logger.info("[Telemetry] Старт ui_metrics_publisher channel=%s", channel)
    iterations = 0
    sleep_interval = interval if interval >= 0 else 0.0
    while True:
        try:
            snapshot_raw = store.metrics_snapshot()
        except Exception as exc:
            logger.debug("[Telemetry] metrics_snapshot недоступний: %s", exc)
            snapshot_raw = {}
        snapshot: dict[str, Any]
        if isinstance(snapshot_raw, Mapping):
            snapshot = dict(snapshot_raw)
        else:
            snapshot = {"value": snapshot_raw}
        snapshot["hot_symbols"] = _hot_symbol_count(store)

        if redis_pub is not None:
            try:
                await redis_pub.publish(channel, json_dumps(snapshot))
            except Exception as exc:  # pragma: no cover - лог тільки для діагностики
                logger.debug("[Telemetry] ui_metrics publish fail: %s", exc)

        iterations += 1
        if iteration_limit is not None and iterations >= iteration_limit:
            break
        await asyncio.sleep(sleep_interval)
