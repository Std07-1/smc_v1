"""Побудова простого ренджа та стану девіації."""

from __future__ import annotations

from typing import Any

import pandas as pd

from smc_core.smc_types import SmcRange, SmcRangeState


def detect_active_range(
    df: pd.DataFrame | None, min_range_bars: int, tolerance_pct: float
) -> tuple[SmcRange | None, SmcRangeState]:
    """Визначає останній діапазон і повертає його стан разом із об'єктом."""

    if (
        df is None
        or df.empty
        or len(df) < min_range_bars
        or {"high", "low"}.difference(df.columns)
    ):
        return None, SmcRangeState.NONE

    window = df.tail(min_range_bars)
    highest = float(window["high"].max())
    lowest = float(window["low"].min())
    eq_level = lowest + (highest - lowest) / 2
    start_time = _extract_timestamp(window, 0)
    end_time = _extract_timestamp(window, len(window) - 1)

    span = max(1e-9, highest - lowest)
    band = span * tolerance_pct
    last_close = (
        float(window["close"].iloc[-1]) if "close" in window.columns else eq_level
    )

    if last_close >= eq_level + band:
        state = SmcRangeState.DEV_UP
    elif last_close <= eq_level - band:
        state = SmcRangeState.DEV_DOWN
    else:
        state = SmcRangeState.INSIDE

    active_range = SmcRange(
        high=highest,
        low=lowest,
        eq_level=eq_level,
        start_time=start_time,
        end_time=end_time,
        state=state,
    )
    return active_range, state


def _extract_timestamp(df: pd.DataFrame, pos: int) -> pd.Timestamp:
    if "timestamp" in df.columns:
        ts = df["timestamp"].iloc[pos]
        if pd.notna(ts):
            return pd.Timestamp(ts)
    for column in ("open_time", "time", "timestamp", "close_time"):
        if column in df.columns:
            value = df[column].iloc[pos]
            ts = _coerce_scalar_timestamp(value)
            if ts is not None:
                return ts
    if isinstance(df.index, pd.DatetimeIndex):
        return pd.Timestamp(df.index[pos])
    return pd.Timestamp(pos)


def _coerce_scalar_timestamp(value: Any) -> pd.Timestamp | None:
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return value
    try:
        return pd.Timestamp(value)
    except (TypeError, ValueError):
        pass
    for unit in ("ms", "s"):
        try:
            return pd.to_datetime(float(value), unit=unit)
        except Exception:  # noqa: BLE001
            continue
    return None
