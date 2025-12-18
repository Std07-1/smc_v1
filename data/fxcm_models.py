"""Pydantic-моделі для телеметрії FXCM (heartbeat + market status)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field

from core.serialization import json_loads

__all__ = [
    "FxcmSessionContext",
    "FxcmHeartbeatContext",
    "FxcmHeartbeat",
    "FxcmMarketStatus",
    "FxcmAggregatedStatus",
    "parse_fxcm_heartbeat",
    "parse_fxcm_market_status",
    "parse_fxcm_aggregated_status",
]


class FxcmSessionContext(BaseModel):
    """Опис торгової сесії, що додається до heartbeat/market_status."""

    tag: str | None = None
    name: str | None = None
    state: str | None = None
    timezone: str | None = None
    weekly_open: str | None = None
    weekly_close: str | None = None
    daily_breaks: list[Any] | None = None
    next_open_utc: str | None = None
    next_open_ms: int | None = None
    current_open_utc: str | None = None
    current_close_utc: str | None = None
    seconds_to_close: float | None = None
    next_open_seconds: float | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "next_open_seconds",
            "next_open_in_seconds",
            "seconds_to_open",
            "seconds_to_next_open",
        ),
    )

    model_config = {"extra": "ignore"}


class FxcmHeartbeatContext(BaseModel):
    """Контекст heartbeat-пакета (опціональний блок)."""

    lag_seconds: float | None = None
    market_pause: bool | None = None
    market_pause_reason: str | None = None
    seconds_to_open: float | None = Field(
        default=None,
        validation_alias=AliasChoices("seconds_to_open", "next_open_seconds"),
    )
    stream_targets: list[dict[str, Any]] | dict[str, Any] | None = None
    bars_published: int | None = Field(
        default=None,
        validation_alias=AliasChoices("bars_published", "published_bars"),
    )
    next_open_utc: str | None = None
    next_open_ms: int | None = None
    cache_enabled: bool | None = None
    cache_source: str | None = None
    idle_reason: str | None = None
    session: FxcmSessionContext | None = None

    model_config = {"extra": "ignore"}


class FxcmHeartbeat(BaseModel):
    """Heartbeat із Redis-каналу `fxcm:heartbeat`."""

    type: Literal["heartbeat"]
    state: Literal["warmup", "warmup_cache", "stream", "idle"]
    last_bar_close_ms: int | None = None
    ts: str | None = None
    context: FxcmHeartbeatContext | None = None

    model_config = {"extra": "ignore"}


class FxcmMarketStatus(BaseModel):
    """Статус ринку з каналу `fxcm:market_status`."""

    type: Literal["market_status"]
    state: Literal["open", "closed"]
    next_open_ms: int | None = None
    next_open_utc: str | None = None
    seconds_to_open: float | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "next_open_in_seconds", "seconds_to_open", "next_open_seconds"
        ),
    )
    ts: str | None = None
    session: FxcmSessionContext | None = None

    model_config = {"extra": "ignore", "populate_by_name": True}


class FxcmAggregatedStatus(BaseModel):
    """Агрегований статус із каналу ``fxcm:status``."""

    ts: float | int | None = None
    process: str | None = None
    market: str | None = None
    price: str | None = None
    ohlcv: str | None = None
    note: str | None = None
    session: FxcmSessionContext | None = None

    model_config = {"extra": "ignore"}


def _coerce_payload(raw: str | bytes | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(raw, Mapping):
        return raw
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            raise ValueError("Порожній payload")
        data = json_loads(raw)
        if not isinstance(data, Mapping):
            raise ValueError("Очікувався JSON-об'єкт")
        return data
    raise TypeError("Непідтримуваний тип payload")


def parse_fxcm_heartbeat(raw: str | bytes | Mapping[str, Any]) -> FxcmHeartbeat:
    """Парсить heartbeat payload і повертає модель."""

    payload = _coerce_payload(raw)
    return FxcmHeartbeat.model_validate(payload)


def parse_fxcm_market_status(raw: str | bytes | Mapping[str, Any]) -> FxcmMarketStatus:
    """Парсить market_status payload і повертає модель."""

    payload = _coerce_payload(raw)
    return FxcmMarketStatus.model_validate(payload)


def parse_fxcm_aggregated_status(
    raw: str | bytes | Mapping[str, Any],
) -> FxcmAggregatedStatus:
    """Парсить payload з каналу ``fxcm:status``."""

    payload = _coerce_payload(raw)
    return FxcmAggregatedStatus.model_validate(payload)
