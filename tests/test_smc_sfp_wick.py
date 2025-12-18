"""Тести детектора SFP та wick-кластерів."""

from __future__ import annotations

from typing import Literal

import pandas as pd

import smc_liquidity
from smc_core.config import SMC_CORE_CONFIG
from smc_core.smc_types import (
    SmcInput,
    SmcLiquidityType,
    SmcRange,
    SmcRangeState,
    SmcStructureState,
    SmcSwing,
    SmcTrend,
)


def _structure(
    bias: Literal["LONG", "SHORT", "NEUTRAL"] = "LONG", high_price: float = 110.0
) -> SmcStructureState:
    ts = pd.date_range("2024-01-01", periods=4, freq="5min")
    swings = [
        SmcSwing(index=0, time=ts[0], price=100.0, kind="LOW", strength=2),
        SmcSwing(index=1, time=ts[1], price=high_price, kind="HIGH", strength=2),
        SmcSwing(index=2, time=ts[2], price=99.5, kind="LOW", strength=2),
    ]
    active_range = SmcRange(
        high=high_price,
        low=99.0,
        eq_level=104.5,
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


def _snapshot(rows: list[tuple[float, float, float, float]]) -> SmcInput:
    timestamps = pd.date_range("2024-01-01", periods=len(rows), freq="5min")
    open_time_ms = (timestamps.view("int64") // 1_000_000).astype("int64")
    # 5m бар: [start_ms, end_ms)
    close_time_ms = (open_time_ms + 5 * 60 * 1000 - 1).astype("int64")
    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open_time": open_time_ms,
            "close_time": close_time_ms,
            "open": [row[0] for row in rows],
            "high": [row[1] for row in rows],
            "low": [row[2] for row in rows],
            "close": [row[3] for row in rows],
            "volume": [100 + idx for idx in range(len(rows))],
        }
    )
    return SmcInput(
        symbol="XAUUSDT",
        tf_primary="5m",
        ohlc_by_tf={"5m": frame},
        context={"pdh": 120.0, "pdl": 95.0},
    )


def test_sfp_event_detected_after_sweep() -> None:
    snapshot = _snapshot(
        [
            (100.0, 101.0, 99.0, 100.5),
            (108.0, 109.2, 105.0, 108.5),
            (110.5, 114.2, 108.6, 108.8),
            (107.0, 108.0, 105.5, 106.8),
        ]
    )
    structure = _structure(bias="SHORT", high_price=110.0)

    liquidity = smc_liquidity.compute_liquidity_state(
        snapshot=snapshot,
        structure=structure,
        cfg=SMC_CORE_CONFIG,
    )

    assert any(pool.liq_type is SmcLiquidityType.SFP for pool in liquidity.pools)
    assert liquidity.meta.get("sfp_events")


def test_wick_cluster_tracked_near_range() -> None:
    snapshot = _snapshot(
        [
            (109.0, 111.0, 108.8, 109.05),
            (108.9, 111.2, 108.7, 108.95),
            (109.1, 111.1, 108.9, 109.0),
        ]
    )
    structure = _structure(bias="LONG", high_price=110.0)

    liquidity = smc_liquidity.compute_liquidity_state(
        snapshot=snapshot,
        structure=structure,
        cfg=SMC_CORE_CONFIG,
    )

    assert any(
        pool.liq_type is SmcLiquidityType.WICK_CLUSTER for pool in liquidity.pools
    )
    assert liquidity.meta.get("wick_clusters")
