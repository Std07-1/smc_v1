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
        normalized = _coerce_scalar_timestamp(ts)
        if normalized is not None:
            return normalized
    for column in ("open_time", "time", "timestamp", "close_time"):
        if column in df.columns:
            value = df[column].iloc[pos]
            ts = _coerce_scalar_timestamp(value)
            if ts is not None:
                return ts
    if isinstance(df.index, pd.DatetimeIndex):
        ts = _coerce_scalar_timestamp(df.index[pos])
        if ts is not None:
            return ts
    return pd.Timestamp(pos, unit="s", tz="UTC")


def _coerce_scalar_timestamp(value: Any) -> pd.Timestamp | None:
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return _ensure_utc(value)
    ts = None
    try:
        ts = pd.Timestamp(value)
    except (TypeError, ValueError):
        ts = None
    if ts is not None and ts.year >= 2000:
        return _ensure_utc(ts)
    numeric: float | None = None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = None
    if numeric is None:
        return None
    magnitude = abs(numeric)
    if magnitude < 1e8:
        return None
    if magnitude >= 1e17:
        unit = "ns"
    elif magnitude >= 1e14:
        unit = "us"
    elif magnitude >= 1e11:
        unit = "ms"
    else:
        unit = "s"
    try:
        ts = pd.to_datetime(numeric, unit=unit, utc=True)
        return _ensure_utc(ts)
    except Exception:  # noqa: BLE001
        return None


def _ensure_utc(ts: pd.Timestamp) -> pd.Timestamp:
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")
