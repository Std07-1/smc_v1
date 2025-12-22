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
    # Stage5 (non-breaking extension): execution micro-події.
    execution: dict[str, Any] | None
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

    # Replay (offline/TV-like): курсор часу, щоб UI міг відтворювати "появу" барів
    # без lookahead. Якщо задано — /smc-viewer/ohlcv може відсікати бари з time > replay_cursor_ms.
    replay_mode: str
    replay_cursor_ms: int
    replay_timeline_tf: str
    replay_compute_tf: str


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
    # Stage3 (non-breaking extension): liquidity targets (internal/external).
    # Джерело: smc_liquidity.meta.liquidity_targets (публікується smc_liquidity).
    targets: list[dict[str, Any]]


class SmcViewerZones(TypedDict, total=False):
    """Стан зон для viewer_state."""

    raw: dict[str, Any]


class SmcViewerPipelineLocal(TypedDict, total=False):
    """Локальний (per-symbol) pipeline-стан з asset.stats."""

    state: str
    ready_bars: int
    required_bars: int
    ready_ratio: float


class SmcViewerScenario(TypedDict, total=False):
    """Stage6 (4.2 vs 4.3) — summary для viewer_state.

    Це не торговий «сигнал», а технічна класифікація сценарію.
    """

    scenario_id: str
    direction: str
    confidence: float
    why: list[str]
    key_levels: dict[str, Any]
    last_change_ts: str | None

    unclear_reason: str | None

    raw_scenario_id: str | None
    raw_direction: str | None
    raw_confidence: float | None
    raw_why: list[str]
    raw_key_levels: dict[str, Any]
    raw_inputs_ok: bool | None
    raw_gates: list[Any]
    raw_unclear_reason: str | None

    pending_id: str | None
    pending_count: int
    ttl_sec: int
    confirm_bars: int
    switch_delta: float

    # Non-breaking extension: пояснення анти-фліпу (чому stable не перемикається).
    anti_flip: dict[str, Any]
    last_eval: dict[str, Any]


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
    # Stage5 (non-breaking extension): execution micro-події (стрілочки на графіку).
    execution: dict[str, Any]
    pipeline_local: SmcViewerPipelineLocal
    scenario: SmcViewerScenario
    fxcm: FxcmMeta | None
    meta: UiSmcMeta

    # Діагностика TF-правди (Етап 0/1): per-TF готовність, лаг, кількість барів.
    # Джерело: smc_hint.meta.tf_health (публікується smc_producer).
    tf_health: dict[str, Any]

    # Stage0/1 мета (TF-план/факт/гейти). Джерело: smc_hint.meta.*
    tf_plan: dict[str, Any]
    tf_effective: list[str]
    gates: list[dict[str, Any]]
    history_state: str
    age_ms: int
    last_open_time_ms: int
    last_ts: str
    lag_ms: int
    bars_5m: int


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
    "SmcViewerScenario",
    "SmcViewerState",
    "OhlcvBar",
    "OhlcvResponse",
)
