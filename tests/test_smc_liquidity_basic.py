"""Базові тести для smc_liquidity."""

from __future__ import annotations

import math
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


def _htf_frame(freq: str, periods: int) -> pd.DataFrame:
    timestamps = pd.date_range("2024-01-01", periods=periods, freq=freq)
    base = 100.0
    # Легка хвиля, щоб були pivot highs/lows по lookback.
    wave = [base + 3.0 * math.sin(i / 4.0) for i in range(len(timestamps))]
    highs = [p + 1.2 for p in wave]
    lows = [p - 1.2 for p in wave]
    closes = [p + 0.2 for p in wave]
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": wave,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1000 + i for i in range(len(timestamps))],
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
        ohlc_by_tf={
            "5m": _sample_frame(),
            "1h": _htf_frame("1h", periods=40),
            "4h": _htf_frame("4h", periods=30),
        },
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


def test_liquidity_targets_include_internal_and_external() -> None:
    structure = _base_structure(bias="NEUTRAL")
    snapshot = _snapshot()

    liquidity = smc_liquidity.compute_liquidity_state(
        snapshot=snapshot,
        structure=structure,
        cfg=SMC_CORE_CONFIG,
    )

    targets = liquidity.meta.get("liquidity_targets")
    assert isinstance(targets, list) and targets
    roles = {t.get("role") for t in targets if isinstance(t, dict)}
    assert "internal" in roles
    assert "external" in roles
    assert len(targets) <= 6  # 1–3 internal + 1–3 external
    for t in targets:
        assert isinstance(t, dict)
        assert isinstance(t.get("tf"), str)
        assert t.get("side") in {"above", "below"}
        assert isinstance(t.get("price"), (int, float))
        assert isinstance(t.get("type"), str)
        assert isinstance(t.get("strength"), (int, float))
        assert isinstance(t.get("reason"), list)


def test_liquidity_external_targets_use_context_session_extremes_when_present() -> None:
    structure = _base_structure(bias="NEUTRAL")
    base_5m = _sample_frame().copy()
    # Примусово робимо ref_price ~100.5 (close останнього 5m бару).
    try:
        base_5m.loc[base_5m.index[-1], "close"] = 100.5
    except Exception:
        pass
    snapshot = SmcInput(
        symbol="XAUUSDT",
        tf_primary="5m",
        ohlc_by_tf={"5m": base_5m},
        context={
            "session_tag": "LONDON",
            "smc_session_tag": "LONDON",
            "smc_session_high": 101.0,
            "smc_session_low": 99.5,
            "smc_sessions": {
                "ASIA": {"high": 100.7, "low": 99.2, "start_ms": 1, "end_ms": 2},
                "LONDON": {"high": 101.0, "low": 99.5, "start_ms": 3, "end_ms": 4},
                "NY": {"high": 101.4, "low": 99.7, "start_ms": 5, "end_ms": 6},
            },
        },
    )

    liquidity = smc_liquidity.compute_liquidity_state(
        snapshot=snapshot,
        structure=structure,
        cfg=SMC_CORE_CONFIG,
    )

    targets = liquidity.meta.get("liquidity_targets")
    assert isinstance(targets, list) and targets
    external = [
        t for t in targets if isinstance(t, dict) and t.get("role") == "external"
    ]
    assert external
    types = {t.get("type") for t in external}
    assert "SESSION_HIGH" in types
    assert "SESSION_LOW" in types
