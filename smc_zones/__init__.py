"""Заглушковий модуль зон/POI для першого етапу."""

from __future__ import annotations

import pandas as pd

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import SmcInput, SmcPoi, SmcZonesState, SmcZoneType


def compute_zones_state(snapshot: SmcInput, cfg: SmcCoreConfig) -> SmcZonesState:
    """Формує список POI на базі останнього бару як плейсхолдер."""

    df = snapshot.ohlc_by_tf.get(snapshot.tf_primary)
    if df is None or df.empty or "high" not in df.columns or "low" not in df.columns:
        return SmcZonesState(zones=[], focus_zone_type=None, meta={"bar_count": 0})
    last_high = float(df["high"].iloc[-1])
    last_low = float(df["low"].iloc[-1])
    zone = SmcPoi(
        zone_type=SmcZoneType.ORDER_BLOCK,
        price_min=last_low,
        price_max=last_high,
        timeframe=snapshot.tf_primary,
        entry_hint=(last_low + last_high) / 2,
        stop_hint=last_low,
        notes={"stub": True, "lookback": min(cfg.max_lookback_bars, len(df))},
    )
    return SmcZonesState(
        zones=[zone],
        focus_zone_type=SmcZoneType.ORDER_BLOCK,
        meta={"bar_count": int(len(df))},
    )
