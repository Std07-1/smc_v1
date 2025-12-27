"""Broadcaster SMC -> viewer_state.

Призначення:
- читати UiSmcStatePayload (snapshot або live-повідомлення);
- будувати SmcViewerState для кожного активу через build_viewer_state;
- підтримувати in-memory snapshot per symbol;
- оновлювати Redis snapshot viewer_state;
- публікувати viewer_state у Redis-канал для тонких клієнтів (UI/WS).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

try:  # pragma: no cover - залежність опційна для юніт-тестів
    from redis.asyncio import Redis
except Exception:  # pragma: no cover
    Redis = Any  # type: ignore[assignment]

from prometheus_client import Counter, Histogram

from core.contracts import normalize_smc_schema_version
from core.contracts.viewer_state import (
    SmcViewerState,
    UiSmcAssetPayload,
    UiSmcMeta,
    UiSmcStatePayload,
)
from core.serialization import json_dumps, json_loads, to_jsonable
from UI_v2.viewer_state_builder import ViewerStateCache, build_viewer_state

SMC_VIEWER_SMC_MESSAGES_TOTAL = Counter(
    "ai_one_smc_viewer_smc_messages_total",
    "Total number of SMC state messages processed by viewer broadcaster.",
)
SMC_VIEWER_VIEWER_STATES_TOTAL = Counter(
    "ai_one_smc_viewer_viewer_states_total",
    "Total number of viewer states built from SMC state messages.",
)
SMC_VIEWER_ERRORS_TOTAL = Counter(
    "ai_one_smc_viewer_errors_total",
    "Total number of errors in SMC viewer broadcaster.",
)
SMC_VIEWER_BUILD_LATENCY_MS = Histogram(
    "ai_one_smc_viewer_build_latency_ms",
    "Latency of processing SMC state message into viewer states (ms).",
    buckets=(1, 5, 10, 25, 50, 100, 250, 500, 1000, 2000),
)


SnapshotSaver = Callable[[], Awaitable[None]]
ViewerPublisher = Callable[[Mapping[str, SmcViewerState]], Awaitable[None]]


async def _process_smc_payload_with_metrics(
    *,
    payload: UiSmcStatePayload,
    cache_by_symbol: dict[str, ViewerStateCache],
    snapshot_by_symbol: dict[str, SmcViewerState],
    save_snapshot_cb: SnapshotSaver,
    publish_cb: ViewerPublisher,
) -> None:
    """Опрацьовує один UiSmcStatePayload та оновлює метрики."""

    SMC_VIEWER_SMC_MESSAGES_TOTAL.inc()
    start = perf_counter()
    try:
        viewer_states = build_viewer_states_from_payload(
            payload=payload,
            cache_by_symbol=cache_by_symbol,
        )
        if not viewer_states:
            return

        SMC_VIEWER_VIEWER_STATES_TOTAL.inc(len(viewer_states))
        snapshot_by_symbol.update(viewer_states)
        await save_snapshot_cb()
        await publish_cb(viewer_states)
    except Exception:
        SMC_VIEWER_ERRORS_TOTAL.inc()
        logger.warning(
            "[SMC viewer] Помилка під час обробки smc_state payload",
            exc_info=True,
        )
    finally:
        duration_ms = (perf_counter() - start) * 1000.0
        SMC_VIEWER_BUILD_LATENCY_MS.observe(duration_ms)


logger = logging.getLogger("smc_viewer_broadcaster")


# -- Конфіг broadcaster-а -----------------------------------------------------


@dataclass(slots=True)
class SmcViewerBroadcasterConfig:
    """Налаштування каналів/ключів для viewer-broadcaster-а.

    Цей шар навмисно не імпортує config.config, щоб залишатися незалежним
    від конкретного пайплайна. Конкретні назви каналів/ключів передаються
    ззовні (AiOne_t, інший сервіс).
    """

    smc_state_channel: str
    smc_snapshot_key: str
    viewer_state_channel: str
    viewer_snapshot_key: str

    @classmethod
    def from_namespace(cls, namespace: str) -> SmcViewerBroadcasterConfig:
        """Формує дефолтні імена каналів/ключів для заданого namespace.

        Приклад:
            cfg = SmcViewerBroadcasterConfig.from_namespace("ai_one_local")
        """
        prefix = f"{namespace}:ui"
        return cls(
            smc_state_channel=f"{prefix}:smc_state",
            smc_snapshot_key=f"{prefix}:smc_snapshot",
            viewer_state_channel=f"{prefix}:smc_viewer_extended",
            viewer_snapshot_key=f"{prefix}:smc_viewer_snapshot",
        )


# -- Pure-функція: payload -> viewer_states -----------------------------------


def build_viewer_states_from_payload(
    payload: UiSmcStatePayload,
    cache_by_symbol: dict[str, ViewerStateCache],
) -> dict[str, SmcViewerState]:
    """Конвертує UiSmcStatePayload у мапу symbol -> SmcViewerState.

    Використовується і для cold-start (snapshot), і для live-повідомлень.
    Redis тут не торкаємось — тільки pure-трансформація.
    """

    assets = payload.get("assets") or []
    meta_raw = payload.get("meta") or {}
    if isinstance(meta_raw, dict):
        schema_raw = meta_raw.get("schema_version")
        if schema_raw:
            schema_value = normalize_smc_schema_version(str(schema_raw))
            meta_raw = dict(meta_raw)
            meta_raw["schema_version"] = schema_value

    meta: UiSmcMeta = meta_raw  # type: ignore[assignment]
    fxcm_block = payload.get("fxcm")  # може бути None або dict

    result: dict[str, SmcViewerState] = {}

    for asset_obj in assets:
        asset: UiSmcAssetPayload = asset_obj  # type: ignore[assignment]
        symbol_raw = asset.get("symbol")
        if not symbol_raw:
            # Без символу немає сенсу будувати viewer_state.
            continue

        symbol = str(symbol_raw).strip().upper()
        if not symbol:
            continue
        cache = cache_by_symbol.setdefault(symbol, ViewerStateCache())

        try:
            viewer_state = build_viewer_state(
                asset=asset,
                payload_meta=meta,
                fxcm_block=fxcm_block,
                cache=cache,
            )
        except Exception:
            # Не валимо весь batch через один «кривий» актив.
            logger.warning(
                "[SMC viewer] Не вдалося побудувати viewer_state для %s",
                symbol,
                exc_info=True,
            )
            continue

        result[symbol] = viewer_state

    return result


# -- Основний клас broadcaster-а ----------------------------------------------


@dataclass(slots=True)
class SmcViewerBroadcaster:
    """Broadcaster SMC -> viewer_state поверх Redis Pub/Sub.

    Очікування:
        - redis: асинхронний клієнт redis.asyncio.Redis;
        - cfg: конфіг каналів/ключів;
        - cache_by_symbol: кеш подій/зон per symbol для стабільного UI.

    Публічний контракт:
        - load_initial_snapshot() — одноразове читання SMC-snapshot,
          побудова viewer_state і збереження viewer_snapshot;
        - run_forever() — підписка на smc_state_channel і транзит
          payload -> viewer_state -> snapshot + viewer_channel.
    """

    redis: Redis  # type: ignore
    cfg: SmcViewerBroadcasterConfig
    cache_by_symbol: dict[str, ViewerStateCache] = field(default_factory=dict)
    snapshot_by_symbol: dict[str, SmcViewerState] = field(default_factory=dict)

    # -- Cold start snapshot --------------------------------------------------

    async def load_initial_snapshot(self) -> dict[str, SmcViewerState]:
        """Читає SMC snapshot з Redis та формує початковий viewer_snapshot.

        Виконується один раз при старті сервісу. Якщо snapshot відсутній
        або некоректний — повертає порожню мапу.
        """
        try:
            raw = await self.redis.get(self.cfg.smc_snapshot_key)
        except Exception:
            logger.warning(
                "[SMC viewer] Не вдалося прочитати SMC snapshot (%s)",
                self.cfg.smc_snapshot_key,
                exc_info=True,
            )
            return {}

        if not raw:
            logger.info(
                "[SMC viewer] SMC snapshot порожній (%s)",
                self.cfg.smc_snapshot_key,
            )
            return {}

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")

        try:
            payload: UiSmcStatePayload = json_loads(raw)  # type: ignore[assignment]
        except Exception:
            logger.warning(
                "[SMC viewer] Некоректний JSON у SMC snapshot (%s)",
                self.cfg.smc_snapshot_key,
                exc_info=True,
            )
            return {}

        viewer_states = build_viewer_states_from_payload(
            payload=payload,
            cache_by_symbol=self.cache_by_symbol,
        )
        if not viewer_states:
            logger.info("[SMC viewer] Snapshot не містить валідних активів")
            return {}

        self.snapshot_by_symbol.update(viewer_states)
        await self._save_viewer_snapshot()
        logger.info(
            "[SMC viewer] Завантажено початковий snapshot (%d активів)",
            len(self.snapshot_by_symbol),
        )
        return dict(self.snapshot_by_symbol)

    async def _save_viewer_snapshot(self) -> None:
        """Зберігає поточний snapshot_by_symbol як один JSON у Redis."""
        try:
            payload_json = json_dumps(to_jsonable(self.snapshot_by_symbol))
            await self.redis.set(self.cfg.viewer_snapshot_key, payload_json)
        except Exception:
            SMC_VIEWER_ERRORS_TOTAL.inc()
            logger.debug(
                "[SMC viewer] Не вдалося оновити viewer_snapshot (%s)",
                self.cfg.viewer_snapshot_key,
                exc_info=True,
            )

    # -- Live-обробка smc_state повідомлень -----------------------------------

    async def run_forever(self) -> None:
        """Основний цикл: підписка на smc_state_channel і транзит viewer_state.

        Порядок:
        1) load_initial_snapshot();
        2) підписка на cfg.smc_state_channel;
        3) для кожного повідомлення:
           - JSON -> UiSmcStatePayload;
           - payload -> viewer_states_per_symbol;
           - оновлення snapshot_by_symbol;
           - збереження snapshot у Redis;
           - publish viewer_state per symbol у cfg.viewer_state_channel.
        """
        await self.load_initial_snapshot()

        backoff_sec = 1.0
        while True:
            pubsub = self.redis.pubsub()
            try:
                await pubsub.subscribe(self.cfg.smc_state_channel)
                logger.info(
                    "[SMC viewer] Підписка на канал %s",
                    self.cfg.smc_state_channel,
                )

                async for message in pubsub.listen():
                    backoff_sec = 1.0
                    if not isinstance(message, Mapping):
                        continue
                    if message.get("type") != "message":
                        continue

                    data = message.get("data")
                    if isinstance(data, bytes):
                        data = data.decode("utf-8", errors="replace")

                    try:
                        payload_raw = data if data is not None else ""
                        payload: UiSmcStatePayload = json_loads(  # type: ignore[assignment]
                            payload_raw
                        )
                    except Exception:
                        logger.warning(
                            "[SMC viewer] Некоректне повідомлення у %s",
                            self.cfg.smc_state_channel,
                            exc_info=True,
                        )
                        continue

                    await _process_smc_payload_with_metrics(
                        payload=payload,
                        cache_by_symbol=self.cache_by_symbol,
                        snapshot_by_symbol=self.snapshot_by_symbol,
                        save_snapshot_cb=self._save_viewer_snapshot,
                        publish_cb=self._publish_viewer_states,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                SMC_VIEWER_ERRORS_TOTAL.inc()
                logger.warning(
                    "[SMC viewer] Втрачено з'єднання з Redis (pubsub). Повтор через %.1f с.",
                    backoff_sec,
                    exc_info=True,
                )
                await asyncio.sleep(backoff_sec)
                backoff_sec = min(backoff_sec * 2.0, 60.0)
            finally:
                try:
                    await pubsub.unsubscribe(self.cfg.smc_state_channel)
                except Exception:
                    pass
                try:
                    await pubsub.close()
                except Exception:
                    pass

    async def _publish_viewer_states(
        self,
        viewer_states: Mapping[str, SmcViewerState],
    ) -> None:
        """Публікує viewer_state для кожного symbol у окремому повідомленні.

        Формат повідомлення:
            {
                "symbol": "XAUUSD",
                "viewer_state": { ... SmcViewerState ... }
            }
        """
        for symbol, state in viewer_states.items():
            try:
                payload = {
                    "symbol": symbol,
                    "viewer_state": state,
                }
                payload_json = json_dumps(to_jsonable(payload))
                await self.redis.publish(self.cfg.viewer_state_channel, payload_json)
            except Exception:
                SMC_VIEWER_ERRORS_TOTAL.inc()
                logger.debug(
                    "[SMC viewer] Не вдалося опублікувати viewer_state для %s",
                    symbol,
                    exc_info=True,
                )
