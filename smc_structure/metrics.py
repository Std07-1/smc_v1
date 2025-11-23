"""Допоміжні метрики для smc_structure (наприклад, ATR)."""

from __future__ import annotations

import pandas as pd


def compute_atr(df: pd.DataFrame | None, period: int = 14) -> pd.Series | None:
    """Обчислює ATR для переданого DataFrame та повертає серію, вирівняну по індексу."""

    if df is None or df.empty:
        return None
    required = {"high", "low", "close"}
    if not required.issubset(df.columns):
        return None

    highs = df["high"].astype(float)
    lows = df["low"].astype(float)
    closes = df["close"].astype(float)
    prev_close = closes.shift(1)

    true_range = pd.concat(
        [
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = true_range.rolling(window=period, min_periods=period).mean()
    return atr
