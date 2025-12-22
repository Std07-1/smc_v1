"""Юніт-тести для Stage4 FVG/Imbalance (мінімальна перевірка детектора)."""

from __future__ import annotations

import pandas as pd

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import SmcInput, SmcStructureState, SmcZoneType
from smc_zones.fvg_detector import detect_fvg_zones


def test_fvg_detector_emits_imbalance_zone_when_gap_large_enough() -> None:
    cfg = SmcCoreConfig()

    # 3-свічковий шаблон:
    # low(third) > high(first) => LONG FVG, price_min=high(first), price_max=low(third)
    ts0 = pd.Timestamp("2025-01-01T00:00:00Z")
    ts1 = pd.Timestamp("2025-01-01T00:05:00Z")
    ts2 = pd.Timestamp("2025-01-01T00:10:00Z")

    frame = pd.DataFrame(
        {
            "timestamp": [ts0, ts1, ts2],
            "open": [100.0, 101.0, 112.0],
            "high": [110.0, 111.0, 114.0],
            "low": [99.0, 100.0, 120.0],
            "close": [105.0, 110.0, 122.0],
            "volume": [100, 110, 120],
        }
    )

    snapshot = SmcInput(
        symbol="TEST", tf_primary="5m", ohlc_by_tf={"5m": frame}, context={}
    )
    structure = SmcStructureState(
        primary_tf="5m", bias="LONG", meta={"atr_last": 1.0, "bias": "LONG"}
    )

    zones = detect_fvg_zones(snapshot=snapshot, structure=structure, cfg=cfg)

    assert zones, "Очікуємо хоча б одну FVG/Imbalance зону"
    z = zones[0]
    assert z.zone_type is SmcZoneType.IMBALANCE
    assert z.direction == "LONG"
    assert z.price_min == 110.0
    assert z.price_max == 120.0
    assert isinstance(z.meta, dict)
    assert z.meta.get("gap") is not None
