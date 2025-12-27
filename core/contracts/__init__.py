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

from .levels_v1 import (  # noqa: F401
    LEVEL_LABELS_BAND_V1,
    LEVEL_LABELS_LINE_V1,
    LEVEL_LABELS_V1,
    LevelKindV1,
    LevelLabelBandV1,
    LevelLabelLineV1,
    LevelLabelV1,
    LevelSource,
    LevelTfV1,
    is_allowed_level_label_v1,
    make_level_id_band_v1,
    make_level_id_line_v1,
    normalize_pool_type_to_level_label_v1,
    round_price_for_level_id,
)

from .levels_v1_time import (  # noqa: F401
    find_active_session_tag_utc,
    get_day_window_utc,
    get_prev_day_window_utc,
    get_session_window_utc,
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
    # Levels-V1 (крок 3.1)
    "LevelTfV1",
    "LevelSource",
    "LevelKindV1",
    "LevelLabelLineV1",
    "LevelLabelBandV1",
    "LevelLabelV1",
    "LEVEL_LABELS_LINE_V1",
    "LEVEL_LABELS_BAND_V1",
    "LEVEL_LABELS_V1",
    "normalize_pool_type_to_level_label_v1",
    "is_allowed_level_label_v1",
    "round_price_for_level_id",
    "make_level_id_line_v1",
    "make_level_id_band_v1",
    # Levels-V1 time (крок 3.2.2a)
    "get_day_window_utc",
    "get_prev_day_window_utc",
    # Levels-V1 session time (крок 3.2.3a)
    "get_session_window_utc",
    "find_active_session_tag_utc",
]
