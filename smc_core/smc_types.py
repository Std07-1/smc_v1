"""Базові типи та перерахування для SMC-core."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Literal

import pandas as pd


class SmcTrend(Enum):
    """Напрямок ринкової структури в інтерпретації SMC."""

    UP = auto()
    DOWN = auto()
    RANGE = auto()
    UNKNOWN = auto()


class SmcRangeState(Enum):
    """Стан ренджу: всередині чи в зоні відхилення."""

    NONE = auto()
    INSIDE = auto()
    DEV_UP = auto()
    DEV_DOWN = auto()


class SmcZoneType(Enum):
    """Типи зон/POI, які використовуємо для підказок."""

    ORDER_BLOCK = auto()
    BREAKER = auto()
    IMBALANCE = auto()
    FAIR_VALUE_GAP = auto()
    LIQUIDITY_VOID = auto()
    PREMIUM_ZONE = auto()
    DISCOUNT_ZONE = auto()
    RANGE_EXTREME = auto()
    CUSTOM = auto()


class SmcLiquidityType(Enum):
    """Типи ліквідності, доступні для Stage2/Stage3 фільтрів."""

    EQH = auto()
    EQL = auto()
    TLQ = auto()
    SLQ = auto()
    RANGE_EXTREME = auto()
    SESSION_HIGH = auto()
    SESSION_LOW = auto()
    SFP = auto()
    WICK_CLUSTER = auto()
    OTHER = auto()


class SmcAmdPhase(Enum):
    """Спрощений стан AMD-фази (Accumulation/Manipulation/Distribution)."""

    ACCUMULATION = auto()
    MANIPULATION = auto()
    DISTRIBUTION = auto()
    NEUTRAL = auto()


class SmcSignalType(Enum):
    """Категорії сигналів, які може повертати SMC движок."""

    CONTINUATION = auto()
    REVERSAL = auto()
    RANGE_PLAY = auto()
    LIQUIDITY_GRAB = auto()
    BREAKOUT = auto()
    NONE = auto()


@dataclass(slots=True)
class SmcPoi:
    """Точка інтересу/діапазон з поясненнями для входу/SL."""

    zone_type: SmcZoneType
    price_min: float
    price_max: float
    timeframe: str
    entry_hint: float | None = None
    stop_hint: float | None = None
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SmcZone:
    """Базова зона/POI, яку SMC повертає для Stage2/Stage3."""

    zone_type: SmcZoneType
    price_min: float
    price_max: float
    timeframe: str
    origin_time: pd.Timestamp
    direction: Literal["LONG", "SHORT", "BOTH"]
    role: Literal["PRIMARY", "COUNTERTREND", "NEUTRAL"]
    strength: float
    confidence: float
    components: list[str]
    zone_id: str | None = None
    entry_mode: Literal["BODY_05", "WICK_05", "BODY_TOUCH", "WICK_TOUCH", "UNKNOWN"] = (
        "UNKNOWN"
    )
    quality: Literal["STRONG", "MEDIUM", "WEAK", "UNKNOWN"] = "UNKNOWN"
    reference_leg_id: str | None = None
    reference_event_id: str | None = None
    bias_at_creation: Literal["LONG", "SHORT", "NEUTRAL", "UNKNOWN"] = "UNKNOWN"
    notes: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SmcSignal:
    """SMC-сигнал, який може бути використаний у Stage2/Stage3."""

    direction: SmcTrend
    signal_type: SmcSignalType
    confidence: float
    poi: SmcPoi | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SmcSwing:
    """Локальний swing high/low на основному таймфреймі."""

    index: int
    time: pd.Timestamp
    price: float
    kind: Literal["HIGH", "LOW"]
    strength: int


@dataclass(slots=True)
class SmcStructureLeg:
    """Перехід між двома сусідніми свінгами з класифікацією HH/HL/LH/LL."""

    from_swing: SmcSwing
    to_swing: SmcSwing
    label: Literal["HH", "HL", "LH", "LL", "UNDEFINED"]


@dataclass(slots=True)
class SmcRange:
    """Поточний або історичний діапазон торгівлі із станом відхилення."""

    high: float
    low: float
    eq_level: float
    start_time: pd.Timestamp
    end_time: pd.Timestamp | None
    state: SmcRangeState


@dataclass(slots=True)
class SmcStructureEvent:
    """Ключові події структури: BOS / ChoCH."""

    event_type: Literal["BOS", "CHOCH"]
    direction: Literal["LONG", "SHORT"]
    price_level: float
    time: pd.Timestamp
    source_leg: SmcStructureLeg


@dataclass(slots=True)
class SmcOteZone:
    """OTE-зона (62–79%) по останньому імпульсу тренду.

    ``role`` визначає використання: ``PRIMARY`` — бойова зона у напрямку bias,
    ``COUNTERTREND`` — лише для QA/діагностики (проти bias), ``NEUTRAL`` — коли
    структура без вираженого bias і зона не повинна запускати торгівлю.
    """

    leg: SmcStructureLeg
    ote_min: float
    ote_max: float
    direction: Literal["LONG", "SHORT"]
    role: Literal["PRIMARY", "COUNTERTREND", "NEUTRAL"] = "PRIMARY"


@dataclass(slots=True)
class SmcStructureState:
    """Проміжний стан структури/ренджів, доступний UI й Stage2.

    Основні поля:
    - ``trend`` — напрям останньої HH/HL/LH/LL послідовності (up/down/range/unknown).
    - ``bias`` — торгова упередженість: останній CHOCH → LONG/SHORT, fallback на ``trend``.
    - ``ranges``/``active_range``/``range_state`` — геометрія діапазону та відхилень.
    - ``events`` — список BOS/CHOCH із мітками часу для Stage2 telemetry.
    - ``ote_zones`` — OTE-рівні з ролями PRIMARY/COUNTERTREND/NEUTRAL.

    ``meta`` зберігає службові дані: ``atr_period``, ``atr_available``, ``atr_last``,
    ``atr_median``, ``bias``, ``last_choch_ts`` (використовується для відсікання старих
    імпульсів), ``bar_count``, ``snapshot_*``, ``swing_times`` та конфіг-пороги.
    """

    primary_tf: str = ""
    trend: SmcTrend = SmcTrend.UNKNOWN
    swings: list[SmcSwing] = field(default_factory=list)
    legs: list[SmcStructureLeg] = field(default_factory=list)
    ranges: list[SmcRange] = field(default_factory=list)
    active_range: SmcRange | None = None
    range_state: SmcRangeState = SmcRangeState.NONE
    events: list[SmcStructureEvent] = field(default_factory=list)
    ote_zones: list[SmcOteZone] = field(default_factory=list)
    bias: Literal["LONG", "SHORT", "NEUTRAL"] = "NEUTRAL"
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SmcLiquidityPool:
    """Пул ліквідності (кластер свінгів або рівень, який тягне ціну)."""

    level: float
    liq_type: SmcLiquidityType
    strength: float
    n_touches: int
    first_time: pd.Timestamp | None
    last_time: pd.Timestamp | None
    role: Literal["PRIMARY", "COUNTERTREND", "NEUTRAL"] = "NEUTRAL"
    source_swings: list[SmcSwing] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SmcLiquidityMagnet:
    """Зона-магніт, яка агрегує близькі пулі ліквідності."""

    price_min: float
    price_max: float
    center: float
    liq_type: SmcLiquidityType
    role: Literal["PRIMARY", "COUNTERTREND", "NEUTRAL"] = "NEUTRAL"
    pools: list[SmcLiquidityPool] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SmcLiquidityState:
    """Зведення про пулі, магніти та AMD-фазу."""

    pools: list[SmcLiquidityPool] = field(default_factory=list)
    magnets: list[SmcLiquidityMagnet] = field(default_factory=list)
    amd_phase: SmcAmdPhase | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SmcZonesState:
    """Зведення по всіх знайдених зонах та POI."""

    zones: list[SmcZone] = field(default_factory=list)
    active_zones: list[SmcZone] = field(default_factory=list)
    poi_zones: list[SmcZone] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SmcHint:
    """Фінальний результат роботи SMC движка."""

    structure: SmcStructureState | None = None
    liquidity: SmcLiquidityState | None = None
    zones: SmcZonesState | None = None
    signals: list[SmcSignal] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SmcInput:
    """Вхідний знімок даних для SMC-core.

    context використовується як легковаговий контейнер для HTF/ризикового контексту:
    - ``trend_context_h1``/``trend_context_4h``: тренди старших TF (dict із станом/схилом).
    - ``whale_flow``: агреговані метрики кітів (обсяги, buy/sell delta).
    - ``pdh``/``pdl``/``pwh``/``pwl``: попередні high/low сесії.
    - ``session_tag``: назва торгової сесії (London/NY/Asia).
    - ``vol_regime``: оцінка волатильності/ATR для risk-модулів.
    - додаткові ключі допускаються, якщо вони документовані в SmcInput notes.
    """

    symbol: str
    tf_primary: str
    ohlc_by_tf: Mapping[str, pd.DataFrame]
    context: dict[str, Any] = field(default_factory=dict)
