"""Юніт-тести для Stage4 POI (відбір 1–3 на сторону + пояснення)."""

from __future__ import annotations

import pandas as pd

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import SmcInput, SmcStructureState, SmcZone, SmcZoneType
from smc_zones.poi_fta import build_active_poi_zones


def test_poi_builder_emits_active_poi_with_why() -> None:
    cfg = SmcCoreConfig()
    frame = _build_close_frame()
    snapshot = SmcInput(
        symbol="TEST",
        tf_primary="5m",
        ohlc_by_tf={"5m": frame},
        context={},
    )
    structure = SmcStructureState(
        primary_tf="5m",
        bias="LONG",
        meta={"atr_last": 1.0, "bias": "LONG"},
    )

    origin = frame.index[-2]
    zone = SmcZone(
        zone_type=SmcZoneType.ORDER_BLOCK,
        price_min=98.0,
        price_max=99.0,
        timeframe="5m",
        origin_time=origin,
        direction="LONG",
        role="PRIMARY",
        strength=1.2,
        confidence=0.7,
        components=["ob"],
        zone_id="z1",
    )

    poi_zones, active_poi, poi_meta = build_active_poi_zones(
        snapshot=snapshot,
        structure=structure,
        liquidity=None,
        zones=[zone],
        cfg=cfg,
    )

    assert len(poi_zones) == 1
    assert isinstance(poi_zones[0].meta, dict)
    assert poi_zones[0].meta.get("poi_type") == "OB"
    assert isinstance(poi_zones[0].meta.get("why"), list)
    assert len(poi_zones[0].meta.get("why") or []) >= 2

    assert isinstance(active_poi, list)
    assert len(active_poi) == 1
    assert active_poi[0]["type"] == "OB"
    assert active_poi[0]["direction"] == "LONG"
    assert active_poi[0]["filled_pct"] is not None
    assert active_poi[0]["score"] is not None
    assert isinstance(active_poi[0]["why"], list)
    assert len(active_poi[0]["why"]) >= 2

    assert poi_meta.get("poi_active") == 1
    assert poi_meta.get("poi_max_per_side") == 3


def test_poi_builder_caps_to_three_per_side_by_score() -> None:
    cfg = SmcCoreConfig()
    frame = _build_close_frame()
    snapshot = SmcInput(
        symbol="TEST",
        tf_primary="5m",
        ohlc_by_tf={"5m": frame},
        context={},
    )
    structure = SmcStructureState(
        primary_tf="5m",
        bias="NEUTRAL",
        meta={"atr_last": 1.0, "bias": "NEUTRAL"},
    )

    origin = frame.index[0]
    zones: list[SmcZone] = []
    for idx, strength in enumerate([0.2, 0.6, 1.0, 1.4, 1.8], start=1):
        zones.append(
            SmcZone(
                zone_type=SmcZoneType.ORDER_BLOCK,
                price_min=98.0,
                price_max=99.0,
                timeframe="5m",
                origin_time=origin,
                direction="LONG",
                role="PRIMARY",
                strength=strength,
                confidence=0.6,
                components=["ob"],
                zone_id=f"z{idx}",
            )
        )

    poi_zones, active_poi, poi_meta = build_active_poi_zones(
        snapshot=snapshot,
        structure=structure,
        liquidity=None,
        zones=zones,
        cfg=cfg,
    )

    # Відбір: максимум 3 LONG.
    assert len(poi_zones) == 3
    assert len(active_poi) == 3
    assert poi_meta.get("poi_active") == 3

    picked_ids = {z.zone_id for z in poi_zones}
    # Очікуємо три найсильніші з 5.
    assert picked_ids == {"z3", "z4", "z5"}


def _build_close_frame() -> pd.DataFrame:
    timestamps = pd.date_range("2025-01-01", periods=6, freq="5min")
    data = {
        "open": [100.0, 100.5, 101.0, 101.2, 101.3, 101.4],
        "high": [100.5, 101.0, 101.5, 101.6, 101.8, 102.0],
        "low": [99.5, 100.0, 100.5, 100.9, 101.0, 101.1],
        "close": [100.2, 100.8, 101.2, 101.3, 101.4, 101.5],
        "volume": [100, 110, 105, 120, 115, 130],
    }
    return pd.DataFrame(data, index=timestamps)
