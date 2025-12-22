"""Юніт-тести для Stage4 Breaker_v1 (мінімальний happy-path).

Breaker залежить від:
- PRIMARY OrderBlock (вхід)
- liquidity.meta.sfp_events (sweep подія)
- BOS події зі структури (events/event_history)

Тест перевіряє, що при валідних даних детектор створює хоча б одну breaker-зону.
"""

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


def test_breaker_detector_emits_zone_for_primary_ob_sweep_and_opposite_bos() -> None:
    cfg = SmcCoreConfig()

    # Часова шкала (5m).
    t0 = pd.Timestamp("2025-01-01T00:00:00Z")
    t1 = pd.Timestamp("2025-01-01T00:05:00Z")
    t2 = pd.Timestamp("2025-01-01T00:10:00Z")
    t3 = pd.Timestamp("2025-01-01T00:15:00Z")

    # Фрейм з DatetimeIndex, щоб breaker міг знайти рядок по часу BOS.
    frame = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0, 103.0],
            "high": [110.0, 111.0, 112.0, 125.0],
            "low": [99.0, 100.0, 101.0, 102.0],
            "close": [105.0, 106.0, 107.0, 124.0],
            "volume": [100, 110, 120, 130],
        },
        index=pd.DatetimeIndex([t0, t1, t2, t3]),
    )

    snapshot = SmcInput(
        symbol="TEST", tf_primary="5m", ohlc_by_tf={"5m": frame}, context={}
    )

    # Мінімальна структура: BOS LONG після sweep.
    swing0 = SmcSwing(index=0, time=t0, price=100.0, kind="LOW", strength=1)
    swing1 = SmcSwing(index=1, time=t1, price=110.0, kind="HIGH", strength=1)
    leg = SmcStructureLeg(from_swing=swing0, to_swing=swing1, label="HH")

    bos_event = SmcStructureEvent(
        event_type="BOS",
        direction="LONG",
        price_level=124.0,
        time=t3,
        source_leg=leg,
    )

    structure = SmcStructureState(
        primary_tf="5m",
        bias="SHORT",
        events=[bos_event],
        event_history=[bos_event],
        meta={"atr_last": 1.0, "bias": "SHORT"},
    )

    # PRIMARY OB SHORT => sweep_side=HIGH, target_direction=LONG.
    ob = SmcZone(
        zone_type=SmcZoneType.ORDER_BLOCK,
        price_min=108.0,
        price_max=110.0,
        timeframe="5m",
        origin_time=t1,
        direction="SHORT",
        role="PRIMARY",
        strength=1.0,
        confidence=0.7,
        components=["ob"],
        zone_id="ob1",
    )

    # Sweep по HIGH на рівні price_max OB після origin_time.
    liquidity = SmcLiquidityState(
        meta={
            "sfp_events": [
                {
                    "side": "HIGH",
                    "level": 110.0,
                    "time": t2,
                    "source": "test",
                }
            ]
        }
    )

    breakers = detect_breakers(
        snapshot=snapshot,
        structure=structure,
        liquidity=liquidity,
        orderblocks=[ob],
        cfg=cfg,
    )

    assert breakers, "Очікуємо breaker-зону за схемою OB→sweep→opposite BOS"
    assert breakers[0].zone_type is SmcZoneType.BREAKER
    assert breakers[0].direction == "LONG"
