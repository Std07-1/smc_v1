"""Детектор сплесків обсягу з опційною перевіркою Volume/ATR."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger("asset_triggers.volume_spike")
logger.setLevel(logging.DEBUG)


def _calc_true_range(df: pd.DataFrame) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    ranges = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    )
    return ranges.max(axis=1)


def volume_spike_trigger(
    df: pd.DataFrame,
    z_thresh: float = 2.0,
    atr_window: int = 14,
    symbol: str = "",
    *,
    use_vol_atr: bool = False,
) -> tuple[bool, dict[str, Any]]:
    """Повертає (спрацював, метадані) для сплеску обсягу.

    Метадані містять z-score, співвідношення volume/ATR та напрямок свічки,
    щоби Stage1 міг сформувати людинозрозумілий reason.
    """

    meta = {"z": 0.0, "ratio": 0.0, "upbar": True, "trigger": "none"}
    if df is None or df.empty:
        logger.debug("[VolumeSpike] Порожній DataFrame — нема що аналізувати")
        return False, meta
    if len(df) < max(atr_window, 5):
        logger.debug(
            f"[{symbol}] [VolumeSpike] Недостатньо даних ({len(df)}) для ATR={atr_window}"
        )
        return False, meta

    latest = df.iloc[-1]
    latest_vol = float(latest.get("volume", 0.0) or 0.0)
    vol_mean = float(df["volume"].mean() or 0.0)
    vol_std = float(df["volume"].std(ddof=0) or 0.0)
    z_score = 0.0 if vol_std == 0 else (latest_vol - vol_mean) / vol_std
    vol_spike_z = z_score > float(z_thresh)

    tr = _calc_true_range(df)
    atr = tr.tail(atr_window).mean()
    vol_atr_ratio = np.inf if atr in (None, 0) else latest_vol / float(atr)
    vol_spike_atr = vol_atr_ratio > 2.0

    prev_close = (
        float(df["close"].iloc[-2]) if len(df) >= 2 else float(latest.get("close", 0.0))
    )
    upbar = float(latest.get("close", 0.0)) >= prev_close

    meta.update({"z": float(z_score), "ratio": float(vol_atr_ratio), "upbar": upbar})

    fired = False
    if vol_spike_z:
        fired = True
        meta["trigger"] = "z"
    elif use_vol_atr and vol_spike_atr:
        fired = True
        meta["trigger"] = "vol_atr"

    logger.debug(
        f"[{symbol}] [VolumeSpike] Z-score={z_score:.2f} (>{z_thresh})? {vol_spike_z}, "
        f"Volume/ATR={vol_atr_ratio:.2f} (>2.0)? {vol_spike_atr}, trigger={meta['trigger']}"
    )

    return fired, meta
