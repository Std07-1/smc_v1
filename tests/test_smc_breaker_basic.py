"""Базові тести для Breaker_v1 (Етап 4.3)."""

from __future__ import annotations

import pandas as pd

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import (
    SmcInput,
    SmcLiquidityState,
    SmcStructureEvent,
    SmcStructureLeg,
    SmcStructureState,
    SmcSwing,
    SmcZone,
    SmcZoneType,
)
from smc_zones.breaker_detector import detect_breakers


def test_breaker_created_after_sweep_and_bos() -> None:
    cfg = SmcCoreConfig()
    snapshot, structure, liquidity, orderblock = _build_context(
        include_sweep=True, include_bos=True
    )

    breakers = detect_breakers(
        snapshot=snapshot,
        structure=structure,
        liquidity=liquidity,
        orderblocks=[orderblock],
        cfg=cfg,
    )

    assert len(breakers) == 1
    zone = breakers[0]
    assert zone.zone_type is SmcZoneType.BREAKER
    assert zone.direction == "SHORT"
    assert zone.role == "PRIMARY"
    assert zone.meta["derived_from_ob_id"] == orderblock.zone_id
    assert zone.meta["sweep_source"] == "swing"
    assert zone.meta["break_event_id"] == zone.reference_event_id
    assert zone.meta["displacement_atr"] >= cfg.breaker_min_displacement_atr


def test_breaker_skipped_without_sweep() -> None:
    cfg = SmcCoreConfig()
    snapshot, structure, liquidity, orderblock = _build_context(
        include_sweep=False, include_bos=True
    )

    breakers = detect_breakers(
        snapshot=snapshot,
        structure=structure,
        liquidity=liquidity,
        orderblocks=[orderblock],
        cfg=cfg,
    )

    assert breakers == []


def test_breaker_skipped_without_bos() -> None:
    cfg = SmcCoreConfig()
    snapshot, structure, liquidity, orderblock = _build_context(
        include_sweep=True, include_bos=False
    )

    breakers = detect_breakers(
        snapshot=snapshot,
        structure=structure,
        liquidity=liquidity,
        orderblocks=[orderblock],
        cfg=cfg,
    )

    assert breakers == []


def _build_context(
    *, include_sweep: bool, include_bos: bool
) -> tuple[SmcInput, SmcStructureState, SmcLiquidityState, SmcZone]:
    timestamps = pd.date_range("2025-03-01", periods=12, freq="5min")
    frame = pd.DataFrame(
        {
            "open": [
                100,
                100.5,
                101.2,
                101.5,
                101.8,
                101.0,
                100.2,
                99.6,
                99.2,
                99.0,
                98.7,
                98.5,
            ],
            "high": [
                100.8,
                101.5,
                102.0,
                102.2,
                102.0,
                101.2,
                100.5,
                99.9,
                99.5,
                99.4,
                99.2,
                99.0,
            ],
            "low": [
                99.8,
                100.2,
                100.8,
                101.0,
                101.2,
                100.0,
                99.5,
                99.0,
                98.7,
                98.5,
                98.2,
                98.0,
            ],
            "close": [
                100.6,
                101.3,
                101.8,
                102.0,
                101.5,
                100.5,
                99.8,
                99.2,
                98.9,
                98.7,
                98.4,
                98.2,
            ],
            "timestamp": timestamps,
        },
        index=timestamps,
    )
    snapshot = SmcInput(symbol="XAUUSD", tf_primary="5m", ohlc_by_tf={"5m": frame})

    swing_high = SmcSwing(
        index=3, time=timestamps[3], price=102.0, kind="HIGH", strength=2
    )
    swing_low = SmcSwing(
        index=8, time=timestamps[8], price=98.7, kind="LOW", strength=2
    )
    leg = SmcStructureLeg(from_swing=swing_high, to_swing=swing_low, label="LL")

    events: list[SmcStructureEvent] = []
    if include_bos:
        events.append(
            SmcStructureEvent(
                event_type="BOS",
                direction="SHORT",
                price_level=swing_low.price,
                time=timestamps[9],
                source_leg=leg,
            )
        )

    structure = SmcStructureState(
        primary_tf="5m",
        swings=[swing_high, swing_low],
        legs=[leg],
        events=events,
        event_history=list(events),
        bias="SHORT",  # type: ignore
        meta={"atr_last": 1.2, "bias": "SHORT"},
    )

    if include_sweep:
        sfp_events = [
            {
                "level": 100.2,
                "side": "LOW",
                "time": timestamps[7].isoformat(),
                "source": "swing",
            }
        ]
    else:
        sfp_events = []
    liquidity = SmcLiquidityState(meta={"sfp_events": sfp_events})

    orderblock = SmcZone(
        zone_type=SmcZoneType.ORDER_BLOCK,
        price_min=100.2,
        price_max=100.8,
        timeframe="5m",
        origin_time=timestamps[4],
        direction="LONG",
        role="PRIMARY",
        strength=1.0,
        confidence=0.5,
        components=["orderblock"],
        zone_id="ob_long_test",
    )

    return snapshot, structure, liquidity, orderblock
