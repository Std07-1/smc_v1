"""Канонічні контракти viewer_state / SMC payload для UI v2.

Призначення:
- SSOT для TypedDict, які є "по дроту" (публікуються/консюмляться поза UI модулем);
- UI має імпортувати ці типи звідси.

Важливо:
- shape/outgoing payload не змінюємо (ті самі поля/назви/optional-логіка).
- `core/` не імпортує `UI_v2/*`.
"""

from __future__ import annotations

from typing import Any, TypedDict

# ── Версіонування контракту viewer_state ───────────────────────────────────

VIEWER_STATE_SCHEMA_VERSION: str = "smc_viewer_v1"


# ── SMC hint (plain) ─────────────────────────────────────────────────────


class SmcHintPlain(TypedDict, total=False):
    """Plain-контракт для SmcHint.

    Деталі полів див. у `smc_hint_contract.md`. Тут фіксуємо тільки топ-рівень,
    щоб не дублювати схему core-модуля.
    """

    structure: dict[str, Any] | None
    liquidity: dict[str, Any] | None
    zones: dict[str, Any] | None
    signals: list[dict[str, Any]]
    meta: dict[str, Any]


# ── FXCM meta (UI block) ─────────────────────────────────────────────────


class FxcmSessionMeta(TypedDict, total=False):
    """Спрощений опис сесії ринку для FXCM-блоку."""

    tag: str
    name: str
    next_open_utc: str
    seconds_to_open: float
    seconds_to_close: float


class FxcmMeta(TypedDict, total=False):
    """Агрегований статус FXCM-конектора/ринку для UI."""

    market_state: str
    process_state: str
    price_state: str
    ohlcv_state: str
    lag_seconds: float
    last_bar_close_utc: str
    next_open_utc: str
    session: FxcmSessionMeta


# ── Envelope / payload (Redis → UI) ──────────────────────────────────────


class UiSmcMeta(TypedDict, total=False):
    """Мета-інформація для UiSmcStatePayload."""

    ts: str
    seq: int
    schema_version: str
    cycle_seq: int
    pipeline_state: str
    pipeline_ready_pct: float
    pipeline_ready_assets: int
    pipeline_min_ready: int
    pipeline_assets_total: int
    pipeline_ready_assets_min: int
    pipeline_min_ready_bars: int
    pipeline_target_bars: int
    pipeline_processed_assets: int
    pipeline_skipped_assets: int
    cycle_duration_ms: float
    fxcm: FxcmMeta


class UiSmcAssetPayload(TypedDict, total=False):
    """Один актив у SMC-пейлоаді, який публікується в Redis."""

    symbol: str
    stats: dict[str, Any]
    smc_hint: SmcHintPlain
    smc_structure: dict[str, Any]
    smc_liquidity: dict[str, Any]
    smc_zones: dict[str, Any]
    price: float
    price_str: str
    live_price_mid: float
    live_price_mid_str: str
    live_price_bid: float
    live_price_bid_str: str
    live_price_ask: float
    live_price_ask_str: str
    live_price_spread: float


class UiSmcStatePayload(TypedDict, total=False):
    """Повний SMC-only пейлоад для UI-консюмера."""

    type: str
    meta: UiSmcMeta
    counters: dict[str, Any]
    assets: list[UiSmcAssetPayload]
    fxcm: FxcmMeta
    analytics: dict[str, Any]


# ── Viewer state (per-asset) ─────────────────────────────────────────────


class SmcViewerStructure(TypedDict, total=False):
    """Структурний стан для viewer_state."""

    trend: str | None
    bias: str | None
    range_state: str | None
    legs: list[dict[str, Any]]
    swings: list[dict[str, Any]]
    ranges: list[dict[str, Any]]
    events: list[dict[str, Any]]
    ote_zones: list[dict[str, Any]]


class SmcViewerLiquidity(TypedDict, total=False):
    """Стан ліквідності для viewer_state."""

    amd_phase: str | None
    pools: list[dict[str, Any]]
    magnets: list[dict[str, Any]]


class SmcViewerZones(TypedDict, total=False):
    """Стан зон для viewer_state."""

    raw: dict[str, Any]


class SmcViewerPipelineLocal(TypedDict, total=False):
    """Локальний (per-symbol) pipeline-стан з asset.stats."""

    state: str
    ready_bars: int
    required_bars: int
    ready_ratio: float


class SmcViewerState(TypedDict, total=False):
    """Агрегований стан одного активу для SMC viewer."""

    symbol: str
    payload_ts: str | None
    payload_seq: int | None
    schema: str | None
    price: float | None
    session: str | None
    structure: SmcViewerStructure
    liquidity: SmcViewerLiquidity
    zones: SmcViewerZones
    pipeline_local: SmcViewerPipelineLocal
    fxcm: FxcmMeta | None
    meta: UiSmcMeta


# ── OHLCV HTTP response ──────────────────────────────────────────────────


class OhlcvBar(TypedDict):
    """Єдиний OHLCV-бар для відповіді HTTP /ohlcv."""

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class OhlcvResponse(TypedDict):
    """Відповідь /smc-viewer/ohlcv."""

    symbol: str
    timeframe: str
    limit: int
    bars: list[OhlcvBar]


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
