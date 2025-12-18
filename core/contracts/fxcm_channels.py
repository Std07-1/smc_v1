"""Канонічні контракти для FXCM Redis-каналів (hot-path).

Цей модуль описує **форму повідомлень**, які приходять/читаються через Redis
(наприклад ``fxcm:ohlcv``, ``fxcm:price_tik``, ``fxcm:status``).

Важливо:
- Тут немає валідації (валідація: `core.contracts.fxcm_validate`).
- Модуль не імпортує нічого з `data/`, `UI/`, `app/` тощо — `core` має бути SSOT.
- Shape payload у runtime **не змінюємо**: ці TypedDict лише фіксують контракт.
"""

from __future__ import annotations

from typing import Any, TypedDict

# Гачок на майбутнє: якщо колись додамо envelope/schema_version у FXCM-канали,
# використовуватимемо окрему версію, не змішуючи її з SMC-state.
FXCM_REDIS_SCHEMA_VERSION: str = "fxcm_redis_v1"

# SSOT: фактичні Redis-канали, які емітить зовнішній FXCM-конектор.
FXCM_CH_OHLCV: str = "fxcm:ohlcv"
FXCM_CH_PRICE_TIK: str = "fxcm:price_tik"
FXCM_CH_STATUS: str = "fxcm:status"


# ── fxcm:ohlcv ─────────────────────────────────────────────────────────────


class FxcmOhlcvBarRequired(TypedDict):
    """Обов'язкові поля OHLCV-бару."""

    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class FxcmOhlcvBarOptional(TypedDict, total=False):
    """Опціональні поля OHLCV-бару (forward-compatible)."""

    complete: bool
    synthetic: bool
    source: str


class FxcmOhlcvBar(FxcmOhlcvBarRequired, FxcmOhlcvBarOptional):
    """Повний OHLCV-бар: required + optional поля."""


class FxcmOhlcvMessageRequired(TypedDict):
    """Обов'язкові поля повідомлення ``fxcm:ohlcv``."""

    symbol: str
    tf: str
    # Важливо: `bars` допускає extra поля (microstructure тощо), тому тримаємо Any.
    bars: list[dict[str, Any]]


class FxcmOhlcvMessageOptional(TypedDict, total=False):
    """Опціональні поля повідомлення ``fxcm:ohlcv``."""

    sig: str


class FxcmOhlcvMessage(FxcmOhlcvMessageRequired, FxcmOhlcvMessageOptional):
    """Повідомлення з Redis-каналу ``fxcm:ohlcv``."""


# ── fxcm:price_tik ─────────────────────────────────────────────────────────


class FxcmPriceTickMessage(TypedDict):
    """Повідомлення з каналу ``fxcm:price_tik`` (bid/ask/mid снапшот)."""

    symbol: str
    bid: float
    ask: float
    mid: float
    tick_ts: float
    snap_ts: float


# ── fxcm:status ────────────────────────────────────────────────────────────


class FxcmAggregatedStatusMessage(TypedDict, total=False):
    """Агрегований статус із каналу ``fxcm:status`` (forward-compatible)."""

    ts: float
    process: str
    market: str
    price: str
    ohlcv: str
    note: str
    session: dict[str, Any]


__all__ = [
    "FXCM_REDIS_SCHEMA_VERSION",
    "FXCM_CH_OHLCV",
    "FXCM_CH_PRICE_TIK",
    "FXCM_CH_STATUS",
    "FxcmOhlcvBarRequired",
    "FxcmOhlcvBarOptional",
    "FxcmOhlcvBar",
    "FxcmOhlcvMessageRequired",
    "FxcmOhlcvMessageOptional",
    "FxcmOhlcvMessage",
    "FxcmPriceTickMessage",
    "FxcmAggregatedStatusMessage",
]
