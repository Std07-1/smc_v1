"""FSM для визначення AMD-фази на базі структури та ліквідності."""

from __future__ import annotations

from typing import Any

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import (
    SmcAmdPhase,
    SmcLiquidityState,
    SmcLiquidityType,
    SmcRangeState,
    SmcStructureEvent,
    SmcStructureState,
    SmcTrend,
)

_LOW_ATR_RATIO = 1.25
_RECENT_EVENT_WINDOW = 3
_TREND_POOL_MIN = 1


def derive_amd_phase(
    structure: SmcStructureState,
    liquidity: SmcLiquidityState,
    cfg: SmcCoreConfig,
) -> tuple[SmcAmdPhase, str]:
    """Обчислює AMD-фазу за спрощеною FSM-логікою.

    Повертає кортеж ``(phase, reason)``, де ``reason`` додаємо в ``liquidity.meta``.
    FSM має фіксований пріоритет: MANIPULATION → DISTRIBUTION → ACCUMULATION → NEUTRAL.
    """

    _ = cfg  # зарезервовано для майбутніх порогів
    if structure is None or liquidity is None:
        return SmcAmdPhase.NEUTRAL, "немає структури чи ліквідності"

    checks = (
        _evaluate_manipulation(structure, liquidity),
        _evaluate_distribution(structure, liquidity),
        _evaluate_accumulation(structure),
    )
    for decision in checks:
        if decision is not None:
            return decision

    return SmcAmdPhase.NEUTRAL, "умови FSM не виконані"


def _evaluate_manipulation(
    structure: SmcStructureState, liquidity: SmcLiquidityState
) -> tuple[SmcAmdPhase, str] | None:
    if structure.active_range is None:
        return None
    if structure.range_state not in (SmcRangeState.DEV_UP, SmcRangeState.DEV_DOWN):
        return None
    if not _has_sweep_signals(liquidity):
        return None

    deviation = structure.range_state.name
    return (
        SmcAmdPhase.MANIPULATION,
        f"відхилення {deviation} + sweep біля екстремумів",
    )


def _evaluate_distribution(
    structure: SmcStructureState, liquidity: SmcLiquidityState
) -> tuple[SmcAmdPhase, str] | None:
    if (
        structure.active_range is not None
        and structure.range_state != SmcRangeState.NONE
    ):
        return None
    if structure.trend not in (SmcTrend.UP, SmcTrend.DOWN):
        return None
    if not _has_trend_bos(structure):
        return None
    if not _trend_pools_dominate(liquidity, structure.bias):
        return None

    return (
        SmcAmdPhase.DISTRIBUTION,
        f"trend {structure.trend.name} + BOS підтверджений TLQ/SLQ",
    )


def _evaluate_accumulation(
    structure: SmcStructureState,
) -> tuple[SmcAmdPhase, str] | None:
    if structure.active_range is None:
        return None
    if structure.range_state != SmcRangeState.INSIDE:
        return None
    if not _is_atr_calm(structure.meta):
        return None
    if _has_recent_bos(structure.events):
        return None

    return (
        SmcAmdPhase.ACCUMULATION,
        "range INSIDE + спокійна ATR без свіжих BOS",
    )


def _has_sweep_signals(liquidity: SmcLiquidityState) -> bool:
    meta = liquidity.meta or {}
    sfp_events = meta.get("sfp_events") or []
    wick_clusters = meta.get("wick_clusters") or []
    return bool(sfp_events or wick_clusters)


def _has_trend_bos(structure: SmcStructureState) -> bool:
    direction: str | None = None
    if structure.bias == "LONG":
        direction = "LONG"
    elif structure.bias == "SHORT":
        direction = "SHORT"
    elif structure.trend == SmcTrend.UP:
        direction = "LONG"
    elif structure.trend == SmcTrend.DOWN:
        direction = "SHORT"
    if direction is None:
        return False
    for event in reversed(structure.events or []):
        if event.event_type != "BOS":
            continue
        if event.direction == direction:
            return True
    return False


def _trend_pools_dominate(liquidity: SmcLiquidityState, bias: str) -> bool:
    target_type = None
    if bias == "LONG":
        target_type = SmcLiquidityType.TLQ
    elif bias == "SHORT":
        target_type = SmcLiquidityType.SLQ
    if target_type is None:
        return False
    count = sum(
        1
        for pool in liquidity.pools
        if pool.liq_type is target_type and pool.role == "PRIMARY"
    )
    return count >= _TREND_POOL_MIN


def _is_atr_calm(meta: dict[str, Any] | None) -> bool:
    if not meta:
        return False
    atr_last = meta.get("atr_last")
    atr_median = meta.get("atr_median")
    if atr_last is None:
        return False
    if atr_median in (None, 0):
        return True
    return atr_last <= atr_median * _LOW_ATR_RATIO


def _has_recent_bos(events: list[SmcStructureEvent] | None) -> bool:
    if not events:
        return False
    recent = list(events)[-_RECENT_EVENT_WINDOW:]
    return any(event.event_type == "BOS" for event in recent)
