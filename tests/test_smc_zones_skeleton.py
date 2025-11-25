"""Перевірки для каркасної реалізації smc_zones (порожні зони)."""

from __future__ import annotations

import pandas as pd

import smc_liquidity
import smc_structure
import smc_zones
from smc_core.config import SmcCoreConfig
from smc_core.smc_types import SmcInput


def test_smc_zones_skeleton_returns_empty() -> None:
    cfg = SmcCoreConfig()
    frame = pd.DataFrame(
        {
            "open_time": [],
            "open": [],
            "high": [],
            "low": [],
            "close": [],
            "volume": [],
        }
    )
    snapshot = SmcInput(
        symbol="TEST",
        tf_primary="5m",
        ohlc_by_tf={"5m": frame},
        context={},
    )

    structure = smc_structure.compute_structure_state(snapshot, cfg)
    liquidity = smc_liquidity.compute_liquidity_state(snapshot, structure, cfg)
    zones = smc_zones.compute_zones_state(snapshot, structure, liquidity, cfg)

    assert zones.zones == []
    assert zones.active_zones == []
    assert zones.poi_zones == []
    assert zones.meta.get("orderblocks_total") == 0
