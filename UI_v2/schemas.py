"""Схеми (TypedDict) для SMC UI v2.

Контракти:
- SmcHintPlain - plain hint згідно `smc_hint_contract.md`;
- UiSmcStatePayload / UiSmcAssetPayload - пейлоад із Redis;
- SmcViewerState - агрегований стан для рендера (UI v2).
"""

from __future__ import annotations

from typing import Any, TypedDict

VIEWER_STATE_SCHEMA_VERSION: str = "smc_viewer_v1"


class SmcHintPlain(TypedDict, total=False):
    """Plain-контракт для SmcHint.

    Деталі полів див. у `smc_hint_contract.md`. Тут ми фіксуємо тільки топ-рівень,
    щоб не дублювати схему core-модуля.
    """

    structure: dict[str, Any] | None
    liquidity: dict[str, Any] | None
    zones: dict[str, Any] | None
    signals: list[dict[str, Any]]
    meta: dict[str, Any]


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
    fxcm: FxcmMeta | None
    meta: UiSmcMeta


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
