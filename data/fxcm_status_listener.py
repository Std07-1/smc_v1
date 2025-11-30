"""Лістенер стану FXCM (market_status + heartbeat).

Підписується на Redis-канали конектора, підтримує локальний стан
``FxcmFeedState`` та (опційно) Prometheus-метрики.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from redis.asyncio import Redis

from app.settings import settings

logger = logging.getLogger("fxcm_status_listener")
try:  # pragma: no cover - опціонально
    PromGauge = importlib.import_module("prometheus_client").Gauge
except Exception:  # pragma: no cover - бібліотека може бути відсутня
    PromGauge = None
    PromGauge = None


class _NoopGauge:
    def labels(self, *args: Any, **kwargs: Any) -> _NoopGauge:  # noqa: D401
        return self

    def set(self, value: float) -> None:  # noqa: D401
        return None


def _build_gauge(
    name: str, description: str, *, labelnames: tuple[str, ...] = ()
) -> Any:
    if PromGauge is None:
        return _NoopGauge()
    try:
        return PromGauge(name, description, labelnames=labelnames)
    except Exception:  # pragma: no cover - gauge вже зареєстровано
        return _NoopGauge()


PROM_FXCM_FEED_LAG = _build_gauge(
    "ai_one_fxcm_feed_lag_seconds",
    "Затримка FXCM-фіда (секунди від останнього close_time).",
)
PROM_FXCM_FEED_STATE = _build_gauge(
    "ai_one_fxcm_feed_state",
    "Поточний стан FXCM (ринок/процес).",
    labelnames=("market_state", "process_state"),
)


@dataclass
class FxcmFeedState:
    """Актуальний стан FXCM фіда, агрегований із Redis-каналів."""

    market_state: str = "unknown"
    next_open_utc: str | None = None
    process_state: str = "unknown"
    last_bar_close_ms: int | None = None
    last_heartbeat_ts: float | None = None
    last_status_ts: float | None = None
    lag_seconds: float | None = None


_MARKET_STATES = {"open", "closed"}
_PROCESS_STATES = {"warmup", "warmup_cache", "stream", "idle"}
_STATE_LOCK = threading.Lock()
_FXCM_FEED_STATE = FxcmFeedState()


def get_fxcm_feed_state() -> FxcmFeedState:
    """Повертає копію поточного стану FXCM."""

    with _STATE_LOCK:
        return replace(_FXCM_FEED_STATE)


def _reset_fxcm_feed_state_for_tests() -> (
    None
):  # pragma: no cover - використовується у тестах
    global _FXCM_FEED_STATE
    with _STATE_LOCK:
        _FXCM_FEED_STATE = FxcmFeedState()


def _normalize_state(value: Any, allowed: set[str]) -> str:
    if not value:
        return "unknown"
    candidate = str(value).strip().lower()
    if candidate in allowed:
        return candidate
    return "unknown"


def _normalize_next_open(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    return text or None


def _update_metrics(snapshot: FxcmFeedState) -> None:
    try:
        lag = snapshot.lag_seconds if snapshot.lag_seconds is not None else 0.0
        PROM_FXCM_FEED_LAG.set(float(lag))
        PROM_FXCM_FEED_STATE.labels(
            market_state=snapshot.market_state,
            process_state=snapshot.process_state,
        ).set(1.0)
    except Exception:  # pragma: no cover - захист від помилок метрик
        pass


def _apply_market_status(payload: Mapping[str, Any]) -> FxcmFeedState:
    now = time.time()
    market_state = _normalize_state(payload.get("state"), _MARKET_STATES)
    next_open_norm = _normalize_next_open(payload.get("next_open_utc"))
    if market_state == "open":
        next_open_norm = None
    with _STATE_LOCK:
        _FXCM_FEED_STATE.market_state = market_state
        _FXCM_FEED_STATE.next_open_utc = next_open_norm
        _FXCM_FEED_STATE.last_status_ts = now
        snapshot = replace(_FXCM_FEED_STATE)
    _update_metrics(snapshot)
    return snapshot


def _apply_heartbeat(payload: Mapping[str, Any]) -> FxcmFeedState:
    now = time.time()
    process_state = _normalize_state(payload.get("state"), _PROCESS_STATES)
    last_close_ms = payload.get("last_bar_close_ms")
    last_close_int: int | None = None
    lag_seconds: float | None = None
    next_open_norm = _normalize_next_open(payload.get("next_open_utc"))
    try:
        if last_close_ms is not None:
            last_close_int = int(last_close_ms)
            lag_seconds = max(0.0, now - (last_close_int / 1000.0))
    except (TypeError, ValueError):
        last_close_int = None
        lag_seconds = None
    with _STATE_LOCK:
        _FXCM_FEED_STATE.process_state = process_state
        _FXCM_FEED_STATE.last_bar_close_ms = last_close_int
        _FXCM_FEED_STATE.lag_seconds = lag_seconds
        _FXCM_FEED_STATE.last_heartbeat_ts = now
        if next_open_norm is not None:
            _FXCM_FEED_STATE.next_open_utc = next_open_norm
        if (
            process_state == "idle"
            and next_open_norm is not None
            and _FXCM_FEED_STATE.market_state in (None, "", "unknown")
        ):
            _FXCM_FEED_STATE.market_state = "closed"
        snapshot = replace(_FXCM_FEED_STATE)
    _update_metrics(snapshot)
    return snapshot


async def run_fxcm_status_listener(
    *,
    redis_host: str | None = None,
    redis_port: int | None = None,
    heartbeat_channel: str = "fxcm:heartbeat",
    market_status_channel: str = "fxcm:market_status",
) -> None:
    """Слухає FXCM канали та оновлює ``FxcmFeedState``."""

    host = redis_host or settings.redis_host
    port = redis_port or settings.redis_port
    channels: list[str] = []
    for ch in (heartbeat_channel, market_status_channel):
        if ch and ch not in channels:
            channels.append(ch)
    if not channels:
        logger.warning("[FXCM_STATUS] Немає каналів для підписки, лістенер не стартує")
        return

    redis = Redis(host=host, port=port)
    pubsub = redis.pubsub()
    await pubsub.subscribe(*channels)
    logger.info(
        "[FXCM_STATUS] Старт лістенера host=%s port=%s channels=%s",
        host,
        port,
        channels,
    )
    try:
        async for message in pubsub.listen():
            if not message or message.get("type") != "message":
                continue
            channel_raw = message.get("channel")
            data_raw = message.get("data")
            try:
                channel = (
                    channel_raw.decode("utf-8")
                    if isinstance(channel_raw, bytes)
                    else str(channel_raw)
                )
            except Exception:
                continue
            payload: Mapping[str, Any] | None = None
            if isinstance(data_raw, bytes):
                raw_text = data_raw.decode("utf-8", errors="ignore")
            else:
                raw_text = str(data_raw)
            try:
                obj = json.loads(raw_text)
                if isinstance(obj, dict):
                    payload = obj
            except json.JSONDecodeError:
                logger.debug(
                    "[FXCM_STATUS] Неможливо розпарсити JSON з каналу %s", channel
                )
                continue
            if payload is None:
                continue
            if channel == heartbeat_channel:
                _apply_heartbeat(payload)
            elif channel == market_status_channel:
                _apply_market_status(payload)
    except asyncio.CancelledError:
        logger.info("[FXCM_STATUS] Отримано CancelledError, завершуємо роботу")
        raise
    finally:
        try:
            await pubsub.unsubscribe(*channels)
        except Exception:  # pragma: no cover - best effort
            pass
        await pubsub.close()
        await redis.close()
        logger.info("[FXCM_STATUS] Лістенер FXCM зупинено коректно")


__all__ = [
    "FxcmFeedState",
    "get_fxcm_feed_state",
    "run_fxcm_status_listener",
]
