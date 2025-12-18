"""Схеми (TypedDict) для SMC UI v2.

Контракти:
- SmcHintPlain - plain hint згідно `smc_hint_contract.md`;
- UiSmcStatePayload / UiSmcAssetPayload - пейлоад із Redis;
- SmcViewerState - агрегований стан для рендера (UI v2).
"""

# Примітка (C2): канонічні TypedDict переїхали у `core/contracts/viewer_state.py`.
# Тут лишається compat-reexport, щоб не ламати існуючі імпорти зі старого шляху.

from __future__ import annotations

# REMOVE_AFTER: 2026-02-01
REMOVE_AFTER = "2026-02-01"

from core.contracts.viewer_state import (
    VIEWER_STATE_SCHEMA_VERSION as VIEWER_STATE_SCHEMA_VERSION,
    FxcmMeta as FxcmMeta,
    FxcmSessionMeta as FxcmSessionMeta,
    OhlcvBar as OhlcvBar,
    OhlcvResponse as OhlcvResponse,
    SmcHintPlain as SmcHintPlain,
    SmcViewerLiquidity as SmcViewerLiquidity,
    SmcViewerPipelineLocal as SmcViewerPipelineLocal,
    SmcViewerState as SmcViewerState,
    SmcViewerStructure as SmcViewerStructure,
    SmcViewerZones as SmcViewerZones,
    UiSmcAssetPayload as UiSmcAssetPayload,
    UiSmcMeta as UiSmcMeta,
    UiSmcStatePayload as UiSmcStatePayload,
)

__all__ = (
    "VIEWER_STATE_SCHEMA_VERSION",
    "SmcHintPlain",
    "FxcmSessionMeta",
    "FxcmMeta",
    "UiSmcMeta",
    "UiSmcAssetPayload",
    "UiSmcStatePayload",
    "SmcViewerStructure",
    "SmcViewerLiquidity",
    "SmcViewerZones",
    "SmcViewerPipelineLocal",
    "SmcViewerState",
    "OhlcvBar",
    "OhlcvResponse",
)
