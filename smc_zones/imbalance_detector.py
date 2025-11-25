"""Проста заглушка пошуку імбалансів (FVG)."""

from __future__ import annotations

import pandas as pd

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import SmcInput, SmcZone, SmcZoneType


def detect_imbalances(snapshot: SmcInput, cfg: SmcCoreConfig) -> list[SmcZone]:
    """Повертає один FVG, якщо останні три свічки мають розрив."""

    frame = snapshot.ohlc_by_tf.get(snapshot.tf_primary)
    if frame is None or len(frame) < 3:
        return []
    required_cols = {"high", "low"}
    if not required_cols.issubset(frame.columns):
        return []

    prev_row = frame.iloc[-3]
    last_row = frame.iloc[-1]
    bullish_gap = float(prev_row["high"]) < float(last_row["low"])
    bearish_gap = float(prev_row["low"]) > float(last_row["high"])

    if not bullish_gap and not bearish_gap:
        return []

    if bullish_gap:
        price_min = float(prev_row["high"])
        price_max = float(last_row["low"])
        direction = "LONG"
    else:
        price_min = float(last_row["high"])
        price_max = float(prev_row["low"])
        direction = "SHORT"

    width = abs(price_max - price_min)
    if width <= 0:
        return []

    origin_candidate = (
        prev_row.get("open_time")
        or last_row.get("open_time")
        or prev_row.get("close_time")
        or frame.index[-1]
    )
    origin_time = pd.Timestamp(origin_candidate)

    zone = SmcZone(
        zone_type=SmcZoneType.IMBALANCE,
        price_min=price_min,
        price_max=price_max,
        timeframe=snapshot.tf_primary,
        origin_time=origin_time,
        direction=direction,
        role="NEUTRAL",
        strength=min(width, 1.0),
        confidence=0.05,
        components=["stub_fvg"],
        zone_id=f"imbalance_{snapshot.symbol.lower()}_{len(frame)}",
        entry_mode="WICK_05",
        quality="WEAK",
        bias_at_creation="UNKNOWN",
        notes="Заглушка імбалансу FVG",
        meta={
            "stub": True,
            "width": width,
            "lookback": min(cfg.max_lookback_bars, len(frame)),
            "role": "NEUTRAL",
            "entry_mode": "WICK_05",
            "quality": "WEAK",
        },
    )
    return [zone]
