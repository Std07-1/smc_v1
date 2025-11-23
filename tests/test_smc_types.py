"""Smoke-тести для базових типів SMC."""

from __future__ import annotations

import pandas as pd

from smc_core.smc_types import (
    SmcHint,
    SmcLiquidityMagnet,
    SmcLiquidityPool,
    SmcLiquidityState,
    SmcLiquidityType,
    SmcPoi,
    SmcRangeState,
    SmcSignal,
    SmcSignalType,
    SmcStructureState,
    SmcTrend,
    SmcZonesState,
    SmcZoneType,
)


def test_smc_hint_instantiation() -> None:
    structure = SmcStructureState(trend=SmcTrend.UP, range_state=SmcRangeState.INSIDE)
    pool = SmcLiquidityPool(
        level=110.0,
        liq_type=SmcLiquidityType.EQH,
        strength=2.5,
        n_touches=2,
        first_time=pd.Timestamp("2024-01-01T00:00:00Z"),
        last_time=pd.Timestamp("2024-01-02T00:00:00Z"),
        role="PRIMARY",
    )
    magnet = SmcLiquidityMagnet(
        price_min=109.5,
        price_max=110.5,
        center=110.0,
        liq_type=SmcLiquidityType.EQH,
        role="PRIMARY",
        pools=[pool],
    )
    liquidity = SmcLiquidityState(pools=[pool], magnets=[magnet])
    poi = SmcPoi(
        zone_type=SmcZoneType.ORDER_BLOCK,
        price_min=100.0,
        price_max=110.0,
        timeframe="5m",
    )
    zones = SmcZonesState(zones=[poi], focus_zone_type=SmcZoneType.ORDER_BLOCK)
    signal = SmcSignal(
        direction=SmcTrend.UP,
        signal_type=SmcSignalType.CONTINUATION,
        confidence=0.8,
        poi=poi,
    )
    hint = SmcHint(
        structure=structure, liquidity=liquidity, zones=zones, signals=[signal]
    )

    assert hint.structure is not None
    assert hint.structure.trend is SmcTrend.UP
    assert hint.liquidity is not None
    assert hint.liquidity.pools[0].liq_type is SmcLiquidityType.EQH
    assert hint.zones is not None
    assert hint.zones.zones[0].zone_type is SmcZoneType.ORDER_BLOCK
    assert hint.signals[0].signal_type is SmcSignalType.CONTINUATION
