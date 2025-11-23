"""Юніт-тести для FSM визначення AMD-фази."""

from __future__ import annotations

import pandas as pd

from smc_core.config import SMC_CORE_CONFIG
from smc_core.smc_types import (
    SmcAmdPhase,
    SmcLiquidityPool,
    SmcLiquidityState,
    SmcLiquidityType,
    SmcRange,
    SmcRangeState,
    SmcStructureEvent,
    SmcStructureLeg,
    SmcStructureState,
    SmcSwing,
    SmcTrend,
)
from smc_liquidity.amd_state import derive_amd_phase


def _dummy_leg() -> SmcStructureLeg:
    ts = pd.Timestamp("2024-01-01T00:00:00Z")
    swing_low = SmcSwing(index=0, time=ts, price=100.0, kind="LOW", strength=2)
    swing_high = SmcSwing(
        index=1, time=ts + pd.Timedelta(minutes=5), price=105.0, kind="HIGH", strength=2
    )
    return SmcStructureLeg(from_swing=swing_low, to_swing=swing_high, label="HH")


def _base_range() -> SmcRange:
    ts = pd.Timestamp("2024-01-01T00:00:00Z")
    return SmcRange(
        high=110.0,
        low=100.0,
        eq_level=105.0,
        start_time=ts,
        end_time=None,
        state=SmcRangeState.INSIDE,
    )


def _liquidity(
    meta: dict | None = None, pools: list[SmcLiquidityPool] | None = None
) -> SmcLiquidityState:
    return SmcLiquidityState(
        pools=pools or [],
        magnets=[],
        amd_phase=None,
        meta=meta or {},
    )


def test_amd_accumulation_basic() -> None:
    active_range = _base_range()
    structure = SmcStructureState(
        primary_tf="5m",
        trend=SmcTrend.RANGE,
        swings=[],
        legs=[],
        ranges=[active_range],
        active_range=active_range,
        range_state=SmcRangeState.INSIDE,
        events=[],
        bias="LONG",
        meta={"atr_last": 0.5, "atr_median": 0.6},
    )
    liquidity = _liquidity(meta={"sfp_events": [], "wick_clusters": []})

    phase, reason = derive_amd_phase(structure, liquidity, SMC_CORE_CONFIG)

    assert phase is SmcAmdPhase.ACCUMULATION
    assert isinstance(reason, str) and reason


def test_amd_manipulation_basic() -> None:
    active_range = _base_range()
    structure = SmcStructureState(
        primary_tf="5m",
        trend=SmcTrend.RANGE,
        swings=[],
        legs=[],
        ranges=[active_range],
        active_range=active_range,
        range_state=SmcRangeState.DEV_UP,
        events=[],
        bias="LONG",
        meta={"atr_last": 0.7, "atr_median": 0.8},
    )
    liquidity = _liquidity(meta={"sfp_events": [{"level": 110.0}], "wick_clusters": []})

    phase, reason = derive_amd_phase(structure, liquidity, SMC_CORE_CONFIG)

    assert phase is SmcAmdPhase.MANIPULATION
    assert "sweep" in reason


def test_amd_distribution_basic() -> None:
    leg = _dummy_leg()
    event = SmcStructureEvent(
        event_type="BOS",
        direction="LONG",
        price_level=105.0,
        time=pd.Timestamp("2024-01-01T00:15:00Z"),
        source_leg=leg,
    )
    structure = SmcStructureState(
        primary_tf="5m",
        trend=SmcTrend.UP,
        swings=[leg.from_swing, leg.to_swing],
        legs=[leg],
        ranges=[],
        active_range=None,
        range_state=SmcRangeState.NONE,
        events=[event],
        bias="LONG",
        meta={},
    )
    tlq_pool = SmcLiquidityPool(
        level=99.0,
        liq_type=SmcLiquidityType.TLQ,
        strength=1.0,
        n_touches=1,
        first_time=pd.Timestamp("2024-01-01T00:20:00Z"),
        last_time=pd.Timestamp("2024-01-01T00:20:00Z"),
        role="PRIMARY",
        meta={},
    )
    liquidity = _liquidity(pools=[tlq_pool])

    phase, reason = derive_amd_phase(structure, liquidity, SMC_CORE_CONFIG)

    assert phase is SmcAmdPhase.DISTRIBUTION
    assert "BOS" in reason


def test_amd_neutral_fallback() -> None:
    structure = SmcStructureState(
        primary_tf="5m",
        trend=SmcTrend.UNKNOWN,
        swings=[],
        legs=[],
        ranges=[],
        active_range=None,
        range_state=SmcRangeState.NONE,
        events=[],
        bias="NEUTRAL",
        meta={},
    )
    liquidity = _liquidity(meta={"sfp_events": [], "wick_clusters": []})

    phase, reason = derive_amd_phase(structure, liquidity, SMC_CORE_CONFIG)

    assert phase is SmcAmdPhase.NEUTRAL
    assert reason.startswith("умови") or "range" in reason
