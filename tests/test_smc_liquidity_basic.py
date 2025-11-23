"""Базові тести для smc_liquidity."""

from __future__ import annotations

from typing import Literal

import pandas as pd

import smc_liquidity
from smc_core.config import SMC_CORE_CONFIG
from smc_core.smc_types import (
    SmcAmdPhase,
    SmcInput,
    SmcLiquidityType,
    SmcRange,
    SmcRangeState,
    SmcStructureState,
    SmcSwing,
    SmcTrend,
)


def _sample_frame() -> pd.DataFrame:
    timestamps = pd.date_range("2024-01-01", periods=16, freq="5min")
    prices = [100 + (i % 4) for i in range(len(timestamps))]
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": prices,
            "high": [p + 1.5 for p in prices],
            "low": [p - 1.5 for p in prices],
            "close": [p + 0.5 for p in prices],
            "volume": [100 + i for i in range(len(prices))],
        }
    )


def _base_structure(bias: Literal["LONG", "SHORT", "NEUTRAL"]) -> SmcStructureState:
    ts = pd.date_range("2024-01-01", periods=5, freq="5min")
    swings = [
        SmcSwing(index=0, time=ts[0], price=100.0, kind="LOW", strength=2),
        SmcSwing(index=1, time=ts[1], price=110.0, kind="HIGH", strength=2),
        SmcSwing(index=2, time=ts[2], price=100.5, kind="LOW", strength=2),
        SmcSwing(index=3, time=ts[3], price=110.8, kind="HIGH", strength=2),
        SmcSwing(index=4, time=ts[4], price=99.8, kind="LOW", strength=2),
    ]
    active_range = SmcRange(
        high=112.0,
        low=98.0,
        eq_level=105.0,
        start_time=ts[0],
        end_time=None,
        state=SmcRangeState.INSIDE,
    )
    return SmcStructureState(
        primary_tf="5m",
        trend=SmcTrend.UP if bias == "LONG" else SmcTrend.DOWN,
        swings=swings,
        active_range=active_range,
        ranges=[active_range],
        bias=bias,
        range_state=SmcRangeState.INSIDE,
        meta={"snapshot_end_ts": ts[-1]},
    )


def _snapshot(context: dict[str, float] | None = None) -> SmcInput:
    return SmcInput(
        symbol="XAUUSDT",
        tf_primary="5m",
        ohlc_by_tf={"5m": _sample_frame()},
        context=context or {"pdh": 115.0, "pdl": 97.0},
    )


def test_liquidity_marks_primary_eql_for_long_bias() -> None:
    structure = _base_structure(bias="LONG")
    snapshot = _snapshot()

    liquidity = smc_liquidity.compute_liquidity_state(
        snapshot=snapshot,
        structure=structure,
        cfg=SMC_CORE_CONFIG,
    )

    eql_pools = [
        pool for pool in liquidity.pools if pool.liq_type is SmcLiquidityType.EQL
    ]
    eqh_pools = [
        pool for pool in liquidity.pools if pool.liq_type is SmcLiquidityType.EQH
    ]

    assert eql_pools and all(pool.role == "PRIMARY" for pool in eql_pools)
    assert eqh_pools and all(pool.role != "PRIMARY" for pool in eqh_pools)
    assert "sfp_events" in liquidity.meta
    assert "wick_clusters" in liquidity.meta
    assert isinstance(liquidity.amd_phase, SmcAmdPhase)
    amd_reason = liquidity.meta.get("amd_reason")
    assert isinstance(amd_reason, str) and amd_reason


def test_liquidity_range_extremes_create_magnets() -> None:
    structure = _base_structure(bias="NEUTRAL")
    snapshot = _snapshot()

    liquidity = smc_liquidity.compute_liquidity_state(
        snapshot=snapshot,
        structure=structure,
        cfg=SMC_CORE_CONFIG,
    )

    range_pools = [
        pool for pool in liquidity.pools if pool.meta.get("source") == "range"
    ]
    assert len(range_pools) == 2
    range_magnets = [
        magnet
        for magnet in liquidity.magnets
        if magnet.liq_type is SmcLiquidityType.RANGE_EXTREME
    ]
    assert range_magnets
    assert "sfp_events" in liquidity.meta
    assert "wick_clusters" in liquidity.meta
