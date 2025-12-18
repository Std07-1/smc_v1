"""Контракти (schemas) між модулями проєкту.

Тут зберігаються TypedDict/dataclass-описання payload між шарами (Stage*/UI),
а також базові "конверти" (envelopes) із версіонуванням схеми.

Принцип: contract-first — спочатку описуємо payload, потім імплементуємо.
"""

from __future__ import annotations

from .base import Envelope, SCHEMA_VERSION, SchemaVersioned
from .smc_state import (  # noqa: F401
    SMC_STATE_SCHEMA_VERSION,
    SMC_STATE_SCHEMA_VERSION_ALIASES,
    is_supported_smc_schema_version,
    normalize_smc_schema_version,
)

__all__ = [
    "Envelope",
    "SCHEMA_VERSION",
    "SchemaVersioned",
    # SMC-state schema_version (C2)
    "SMC_STATE_SCHEMA_VERSION",
    "SMC_STATE_SCHEMA_VERSION_ALIASES",
    "normalize_smc_schema_version",
    "is_supported_smc_schema_version",
]
