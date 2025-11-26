"""Виділення локальних свінгів для подальшого аналізу структури."""

from __future__ import annotations

from typing import Any

import pandas as pd

from smc_core.smc_types import SmcSwing


def detect_swings(df: pd.DataFrame | None, min_separation: int) -> list[SmcSwing]:
    """Повертає список свінгів, використовуючи симетричне вікно навколо свічки.

    Вважаємо свінгом точку, де high (для HIGH) або low (для LOW) є екстремумом
    серед ``min_separation`` сусідніх барів ліворуч і праворуч. Це дає стабільну
    основу для побудови HH/LL навіть на шумних рядах.
    """

    if df is None or df.empty or "high" not in df.columns or "low" not in df.columns:
        return []

    window = max(1, min_separation)
    total = len(df)
    if total < window * 2 + 1:
        return []

    highs = df["high"].astype(float)
    lows = df["low"].astype(float)
    times = [_extract_timestamp(df, idx) for idx in range(total)]

    swings: list[SmcSwing] = []
    for idx in range(window, total - window):
        local_high = highs.iloc[idx]
        left_high = highs.iloc[idx - window : idx].max()
        right_high = highs.iloc[idx + 1 : idx + 1 + window].max()
        if local_high >= left_high and local_high >= right_high:
            swings.append(
                SmcSwing(
                    index=idx,
                    time=times[idx],
                    price=float(local_high),
                    kind="HIGH",
                    strength=window,
                )
            )

        local_low = lows.iloc[idx]
        left_low = lows.iloc[idx - window : idx].min()
        right_low = lows.iloc[idx + 1 : idx + 1 + window].min()
        if local_low <= left_low and local_low <= right_low:
            swings.append(
                SmcSwing(
                    index=idx,
                    time=times[idx],
                    price=float(local_low),
                    kind="LOW",
                    strength=window,
                )
            )

    swings.sort(key=lambda swing: swing.index)
    return swings


def _extract_timestamp(df: pd.DataFrame, idx: int) -> pd.Timestamp:
    if "timestamp" in df.columns:
        ts = df["timestamp"].iloc[idx]
        normalized = _coerce_scalar_timestamp(ts)
        if normalized is not None:
            return normalized
    for column in ("open_time", "time", "timestamp", "close_time"):
        if column in df.columns:
            value = df[column].iloc[idx]
            ts = _coerce_scalar_timestamp(value)
            if ts is not None:
                return ts
    if isinstance(df.index, pd.DatetimeIndex):
        ts = _coerce_scalar_timestamp(df.index[idx])
        if ts is not None:
            return ts
    return pd.Timestamp(idx, unit="s", tz="UTC")


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
