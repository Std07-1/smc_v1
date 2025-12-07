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
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from redis.asyncio import Redis

from app.settings import settings
from data.fxcm_models import (
    FxcmAggregatedStatus,
    FxcmHeartbeat,
    FxcmMarketStatus,
    FxcmSessionContext,
    parse_fxcm_aggregated_status,
    parse_fxcm_heartbeat,
    parse_fxcm_market_status,
)

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

    market_state: str | None = "unknown"
    process_state: str | None = "unknown"
    price_state: str | None = None
    ohlcv_state: str | None = None
    next_open_utc: str | None = None
    next_open_ms: int | None = None
    last_bar_close_ms: int | None = None
    last_heartbeat_ts: float | None = None
    last_market_status_ts: float | None = None
    lag_seconds: float | None = None
    market_pause: bool | None = None
    market_pause_reason: str | None = None
    seconds_to_open: float | None = None
    last_heartbeat_iso: str | None = None
    last_market_status_iso: str | None = None
    status_note: str | None = None
    status_ts: float | None = None
    status_ts_iso: str | None = None
    stream_targets: list[dict[str, Any]] | dict[str, Any] | None = None
    published_bars: int | None = None
    session: dict[str, Any] | None = None
    session_seconds_to_close: float | None = None
    session_seconds_to_next_open: float | None = None
    session_name: str | None = None
    session_state: str | None = None

    def to_metrics_dict(self) -> dict[str, Any]:
        """Формує словник для metrics_snapshot() у старому форматі UI."""

        market_state = (self.market_state or "unknown").lower()
        process_state = (self.process_state or "unknown").upper()
        if market_state == "open":
            market_label = "OPEN"
        elif market_state == "closed":
            market_label = "CLOSED"
        else:
            market_label = "UNKNOWN"

        lag_value = self.lag_seconds
        if lag_value is None and self.last_bar_close_ms is not None:
            lag_value = max(
                0.0, (time.time() * 1000.0 - self.last_bar_close_ms) / 1000.0
            )
        lag = float(lag_value or 0.0)
        lag_human = f"{int(lag)}s ({lag:.1f}s)"

        last_close_utc = _ms_to_utc_iso(self.last_bar_close_ms) or "-"
        next_open_utc = _ms_to_utc_iso(self.next_open_ms) or self.next_open_utc or "-"

        return {
            "market": market_label,
            "market_state": market_state,
            "process": process_state,
            "process_state": process_state,
            "price_state": (
                (self.price_state or "unknown").lower() if self.price_state else None
            ),
            "ohlcv_state": (
                (self.ohlcv_state or "unknown").lower() if self.ohlcv_state else None
            ),
            "lag_seconds": lag,
            "lag_human": lag_human,
            "last_bar_close_ms": self.last_bar_close_ms,
            "last_close_utc": last_close_utc,
            "next_open_ms": self.next_open_ms,
            "next_open_utc": next_open_utc,
            "heartbeat_ts": self.last_heartbeat_iso,
            "market_status_ts": self.last_market_status_iso,
            "status_note": self.status_note,
            "status_ts": self.status_ts,
            "status_ts_iso": self.status_ts_iso,
            "published_bars": self.published_bars,
            "stream_targets": self.stream_targets,
            "session": self.session,
            "session_seconds_to_close": self.session_seconds_to_close,
            "session_seconds_to_next_open": self.session_seconds_to_next_open,
            "session_name": self.session_name,
            "session_state": self.session_state,
        }


_MARKET_STATES = {"open", "closed"}
_PROCESS_STATES = {"warmup", "warmup_cache", "stream", "idle"}
_STATE_LOCK = threading.Lock()
_FXCM_FEED_STATE = FxcmFeedState()


def _session_model_to_dict(session: FxcmSessionContext | None) -> dict[str, Any] | None:
    if session is None:
        return None
    try:
        data = session.model_dump(exclude_none=True)
        return data or None
    except Exception:
        return None


def _ms_to_utc_iso(value: int | float | None) -> str | None:
    if value is None:
        return None
    try:
        ts = int(float(value)) / 1000.0
        dt = datetime.fromtimestamp(ts, tz=UTC)
        return dt.strftime("%Y-%m-%d %H:%M:%S") + "Z"
    except Exception:
        return None


def _seconds_to_iso(value: int | float | None) -> str | None:
    if value is None:
        return None
    try:
        dt = datetime.fromtimestamp(float(value), tz=UTC)
    except Exception:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S") + "Z"


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _update_session_snapshot(session_payload: dict[str, Any] | None) -> None:
    if session_payload is not None and not isinstance(session_payload, dict):
        session_payload = dict(session_payload)
    _FXCM_FEED_STATE.session = session_payload
    if not session_payload:
        _FXCM_FEED_STATE.session_seconds_to_close = None
        _FXCM_FEED_STATE.session_seconds_to_next_open = None
        _FXCM_FEED_STATE.session_name = None
        _FXCM_FEED_STATE.session_state = None
        return
    _FXCM_FEED_STATE.session_seconds_to_close = _coerce_float(
        session_payload.get("seconds_to_close")
    )
    next_open_candidate = (
        session_payload.get("seconds_to_next_open")
        or session_payload.get("next_open_seconds")
        or session_payload.get("seconds_to_open")
    )
    _FXCM_FEED_STATE.session_seconds_to_next_open = _coerce_float(next_open_candidate)
    _FXCM_FEED_STATE.session_name = _coerce_str(
        session_payload.get("name") or session_payload.get("tag")
    )
    _FXCM_FEED_STATE.session_state = _coerce_str(session_payload.get("state"))


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


def _apply_market_status(status: FxcmMarketStatus) -> FxcmFeedState:
    now_monotonic = time.monotonic()
    market_state = status.state if status.state in _MARKET_STATES else "unknown"
    next_open_utc = status.next_open_utc if market_state == "closed" else None
    with _STATE_LOCK:
        _FXCM_FEED_STATE.market_state = market_state
        _FXCM_FEED_STATE.next_open_utc = next_open_utc
        _FXCM_FEED_STATE.next_open_ms = status.next_open_ms
        _FXCM_FEED_STATE.seconds_to_open = status.seconds_to_open
        _FXCM_FEED_STATE.last_market_status_ts = now_monotonic
        _FXCM_FEED_STATE.last_market_status_iso = (
            status.ts or _FXCM_FEED_STATE.last_market_status_iso
        )
        session_payload = _session_model_to_dict(status.session)
        if session_payload is not None:
            _update_session_snapshot(session_payload)
        snapshot = replace(_FXCM_FEED_STATE)
    _update_metrics(snapshot)
    return snapshot


def _apply_heartbeat(heartbeat: FxcmHeartbeat) -> FxcmFeedState:
    now_monotonic = time.monotonic()
    now_ms = time.time() * 1000.0
    process_state = heartbeat.state if heartbeat.state in _PROCESS_STATES else "unknown"
    last_close_int = heartbeat.last_bar_close_ms
    lag_seconds: float | None = None
    context = heartbeat.context
    if context and context.lag_seconds is not None:
        try:
            lag_seconds = float(context.lag_seconds)
        except (TypeError, ValueError):
            lag_seconds = None
    if lag_seconds is None and last_close_int is not None:
        lag_seconds = max(0.0, (now_ms - last_close_int) / 1000.0)
    next_open_utc = context.next_open_utc if context else None
    next_open_ms = context.next_open_ms if context else None
    seconds_to_open = context.seconds_to_open if context else None
    market_pause = context.market_pause if context else None
    market_pause_reason = context.market_pause_reason if context else None
    stream_targets = context.stream_targets if context else None
    published_bars = context.bars_published if context else None
    session_payload = _session_model_to_dict(context.session) if context else None
    with _STATE_LOCK:
        _FXCM_FEED_STATE.process_state = process_state
        if last_close_int is not None:
            _FXCM_FEED_STATE.last_bar_close_ms = last_close_int
        _FXCM_FEED_STATE.lag_seconds = lag_seconds
        _FXCM_FEED_STATE.last_heartbeat_ts = now_monotonic
        if heartbeat.ts:
            _FXCM_FEED_STATE.last_heartbeat_iso = heartbeat.ts
        if _FXCM_FEED_STATE.market_state in (None, "", "unknown") and process_state in {
            "stream",
            "warmup",
            "warmup_cache",
        }:
            _FXCM_FEED_STATE.market_state = "open"
        if next_open_utc is not None:
            _FXCM_FEED_STATE.next_open_utc = next_open_utc
        if next_open_ms is not None:
            _FXCM_FEED_STATE.next_open_ms = next_open_ms
        if seconds_to_open is not None:
            _FXCM_FEED_STATE.seconds_to_open = seconds_to_open
        _FXCM_FEED_STATE.market_pause = market_pause
        _FXCM_FEED_STATE.market_pause_reason = market_pause_reason
        _FXCM_FEED_STATE.stream_targets = stream_targets
        _FXCM_FEED_STATE.published_bars = published_bars
        if session_payload is not None:
            _update_session_snapshot(session_payload)
        if (
            process_state == "idle"
            and _FXCM_FEED_STATE.market_state in (None, "", "unknown")
            and next_open_utc is not None
        ):
            _FXCM_FEED_STATE.market_state = "closed"
        snapshot = replace(_FXCM_FEED_STATE)
    _update_metrics(snapshot)
    return snapshot


def _apply_status_snapshot(status: FxcmAggregatedStatus) -> FxcmFeedState:
    ts_value = None
    try:
        if status.ts is not None:
            ts_value = float(status.ts)
    except (TypeError, ValueError):
        ts_value = None
    with _STATE_LOCK:
        market_value = _coerce_str(status.market)
        if market_value:
            _FXCM_FEED_STATE.market_state = market_value
        process_value = _coerce_str(status.process)
        if process_value:
            _FXCM_FEED_STATE.process_state = process_value
        price_value = _coerce_str(status.price)
        if price_value:
            _FXCM_FEED_STATE.price_state = price_value
        ohlcv_value = _coerce_str(status.ohlcv)
        if ohlcv_value:
            _FXCM_FEED_STATE.ohlcv_state = ohlcv_value
        _FXCM_FEED_STATE.status_note = _coerce_str(status.note)
        if ts_value is not None:
            _FXCM_FEED_STATE.status_ts = ts_value
            _FXCM_FEED_STATE.status_ts_iso = _seconds_to_iso(ts_value)
        session_payload = _session_model_to_dict(status.session)
        if session_payload is not None:
            _update_session_snapshot(session_payload)
        snapshot = replace(_FXCM_FEED_STATE)
    _update_metrics(snapshot)
    return snapshot


def note_fxcm_bar_close(close_time_ms: int | None) -> None:
    """Оновлює last_bar_close_ms з каналу fxcm:ohlcv (fallback)."""

    if close_time_ms is None:
        return
    try:
        close_int = int(close_time_ms)
    except (TypeError, ValueError):
        return

    with _STATE_LOCK:
        _FXCM_FEED_STATE.last_bar_close_ms = close_int
        snapshot = replace(_FXCM_FEED_STATE)
    _update_metrics(snapshot)


async def run_fxcm_status_listener(
    *,
    redis_host: str | None = None,
    redis_port: int | None = None,
    heartbeat_channel: str = "fxcm:heartbeat",
    market_status_channel: str = "fxcm:market_status",
    status_channel: str | None = None,
) -> None:
    """Слухає FXCM канали та оновлює ``FxcmFeedState``."""

    host = redis_host or settings.redis_host
    port = redis_port or settings.redis_port
    status_ch = (status_channel or settings.fxcm_status_channel or "").strip()
    channels: list[str] = []
    for ch in (heartbeat_channel, market_status_channel, status_ch):
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
                try:
                    heartbeat = parse_fxcm_heartbeat(payload)
                except (ValidationError, ValueError, TypeError) as exc:
                    logger.warning(
                        "[FXCM_STATUS] Некоректний heartbeat payload: %s", exc
                    )
                    continue
                _apply_heartbeat(heartbeat)
            elif channel == market_status_channel:
                try:
                    status = parse_fxcm_market_status(payload)
                except (ValidationError, ValueError, TypeError) as exc:
                    logger.warning(
                        "[FXCM_STATUS] Некоректний market_status payload: %s", exc
                    )
                    continue
                _apply_market_status(status)
            elif status_ch and channel == status_ch:
                try:
                    combined_status = parse_fxcm_aggregated_status(payload)
                except (ValidationError, ValueError, TypeError) as exc:
                    logger.warning(
                        "[FXCM_STATUS] Некоректний status payload: %s",
                        exc,
                    )
                    continue
                _apply_status_snapshot(combined_status)
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
    "note_fxcm_bar_close",
]
