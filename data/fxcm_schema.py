"""Compat wrapper для FXCM TypedDict/validate.

SSOT:
- контракти (TypedDict) живуть у `core.contracts.fxcm_channels`;
- валідація payload'ів живе у `core.contracts.fxcm_validate`.

Цей модуль лишається як thin-compat шар для старих імпортів з `data.fxcm_schema`.
Runtime shape/outgoing не змінюється.

REMOVE_AFTER: 2026-02-01
"""

from __future__ import annotations

REMOVE_AFTER = "2026-02-01"

from core.contracts.fxcm_channels import (
    FxcmAggregatedStatusMessage,
    FxcmOhlcvBar,
    FxcmOhlcvBarOptional,
    FxcmOhlcvMessage,
    FxcmOhlcvMessageOptional,
    FxcmPriceTickMessage,
)
from core.contracts.fxcm_validate import (
    validate_fxcm_ohlcv_message,
    validate_fxcm_price_tick_message,
    validate_fxcm_status_message,
)

__all__ = (
    "FxcmOhlcvBar",
    "FxcmOhlcvBarOptional",
    "FxcmOhlcvMessage",
    "FxcmOhlcvMessageOptional",
    "FxcmPriceTickMessage",
    "FxcmAggregatedStatusMessage",
    "validate_fxcm_ohlcv_message",
    "validate_fxcm_price_tick_message",
    "validate_fxcm_status_message",
)
