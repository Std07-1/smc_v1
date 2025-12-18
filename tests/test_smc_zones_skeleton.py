"""Перевірки для каркасної реалізації smc_zones (порожні зони)."""

from __future__ import annotations

import pandas as pd

import smc_zones
from smc_core.config import SmcCoreConfig
from smc_core.smc_types import SmcInput, SmcStructureState, SmcZonesState


def test_smc_zones_skeleton_handles_empty_structure() -> None:
    cfg = SmcCoreConfig()
    frame = pd.DataFrame(
        {
            "open_time": pd.date_range("2025-01-01", periods=4, freq="min"),
            "open": [1.0, 1.1, 1.2, 1.25],
            "high": [1.1, 1.2, 1.25, 1.3],
            "low": [0.95, 1.0, 1.15, 1.2],
            "close": [1.05, 1.15, 1.22, 1.28],
            "volume": [100, 110, 105, 115],
        }
    ).set_index("open_time")
    snapshot = SmcInput(
        symbol="TEST",
        tf_primary="5m",
        ohlc_by_tf={"5m": frame},
        context={},
    )

    empty_structure = SmcStructureState(primary_tf="5m")
    zones_state = smc_zones.compute_zones_state(snapshot, empty_structure, None, cfg)

    assert isinstance(zones_state, SmcZonesState)
    assert zones_state.zones == []
    assert zones_state.active_zones == []
    assert zones_state.poi_zones == []
    assert zones_state.meta.get("orderblocks_total") == 0
    assert zones_state.meta.get("zone_count") == 0
    assert zones_state.meta.get("active_zone_count") == 0
    ob_params = zones_state.meta.get("ob_params")
    assert isinstance(ob_params, dict)
    assert ob_params.get("ob_leg_min_atr_mul") == cfg.ob_leg_min_atr_mul
