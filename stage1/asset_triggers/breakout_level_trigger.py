"""Виявлення пробою локальних рівнів із підтвердженнями та ретестами."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger("asset_triggers.breakout_level")
logger.setLevel(logging.DEBUG)


def breakout_level_trigger(
    df: pd.DataFrame,
    stats: dict[str, Any],
    window: int = 20,
    near_threshold: float = 0.005,
    near_daily_threshold: float = 0.5,
    symbol: str = "",
    *,
    confirm_bars: int = 1,
    min_retests: int = 0,
) -> dict[str, bool]:
    """
    Виявляє пробій локальних екстремумів і близькість до них,
    а також до глобальних денних рівнів.

    Параметри:
    - df: останні бари, мінімум window+1
    - stats: словник з update_statistics, повинен містити 'daily_levels': List[float]
    - window: число барів для локальних екстремумів
    - near_threshold: поріг близькості до локальних рівнів (в частках, напр. 0.005=0.5%)
    - near_daily_threshold: поріг близькості до денних рівнів (в %)
    - symbol: символ для логування

    Повертає словник із ключами:
    - breakout_up, breakout_down, near_high, near_low,
    - near_daily_support, near_daily_resistance
    """
    triggers: dict[str, bool] = {
        "breakout_up": False,
        "breakout_down": False,
        "near_high": False,
        "near_low": False,
        "near_daily_support": False,
        "near_daily_resistance": False,
    }

    confirm_bars = max(1, int(confirm_bars or 1))
    min_retests = max(0, int(min_retests or 0))
    rows_required = window + confirm_bars
    if len(df) < rows_required:
        logger.debug(
            "[%s] Недостатньо даних: %d < window+confirm=%d",
            symbol,
            len(df),
            rows_required,
        )
        return triggers

    history_slice = df.iloc[:-confirm_bars]
    if history_slice.empty:
        history_slice = df.iloc[:-1]

    high_series = pd.to_numeric(history_slice["high"], errors="coerce").tail(window)
    low_series = pd.to_numeric(history_slice["low"], errors="coerce").tail(window)
    recent_high = float(high_series.max()) if not high_series.empty else float("nan")
    recent_low = float(low_series.min()) if not low_series.empty else float("nan")

    close_series = pd.to_numeric(df["close"], errors="coerce")
    confirm_closes = close_series.tail(confirm_bars)
    if confirm_closes.isna().any():
        logger.debug("[%s] Невалідні значення close для confirm-блоків", symbol)
        return triggers
    current_close = float(confirm_closes.iloc[-1])

    history_close = close_series.iloc[: len(df) - confirm_bars]
    if history_close.empty:
        history_close = close_series.iloc[:-1]
    prev_close = (
        float(history_close.iloc[-1]) if not history_close.empty else current_close
    )

    breakout_up = confirm_closes.gt(recent_high).all()
    breakout_down = confirm_closes.lt(recent_low).all()

    if breakout_up:
        retests = 0
        if near_threshold > 0 and not history_close.empty and pd.notna(recent_high):
            diff = (recent_high - history_close) / recent_high
            retests = int(((diff >= 0) & (diff <= near_threshold)).sum())
        if min_retests > 0 and retests < min_retests:
            breakout_up = False
        else:
            breakout_up = breakout_up and prev_close <= recent_high

    if breakout_down:
        retests = 0
        if near_threshold > 0 and not history_close.empty and pd.notna(recent_low):
            diff = (history_close - recent_low) / recent_low
            retests = int(((diff >= 0) & (diff <= near_threshold)).sum())
        if min_retests > 0 and retests < min_retests:
            breakout_down = False
        else:
            breakout_down = breakout_down and prev_close >= recent_low

    triggers["breakout_up"] = bool(breakout_up)
    triggers["breakout_down"] = bool(breakout_down)

    # Близькість до локальних рівнів
    near_high_dist = float("inf")
    near_low_dist = float("inf")
    if near_threshold > 0:
        if pd.notna(recent_high) and recent_high > 0:
            if current_close <= recent_high:
                near_high_dist = (recent_high - current_close) / recent_high
                triggers["near_high"] = near_high_dist < near_threshold
        if pd.notna(recent_low) and recent_low > 0:
            if current_close >= recent_low:
                near_low_dist = (current_close - recent_low) / recent_low
                triggers["near_low"] = near_low_dist < near_threshold

    if triggers["near_high"] and triggers["near_low"]:
        if near_high_dist <= near_low_dist:
            triggers["near_low"] = False
        else:
            triggers["near_high"] = False

    # Глобальні денні рівні з stats
    price = current_close
    daily_levels = stats.get("daily_levels", [])
    if daily_levels:
        nearest = min(daily_levels, key=lambda lv: abs(price - lv))
        dist_pct = abs(price - nearest) / nearest * 100
        if dist_pct < near_daily_threshold:
            if price > nearest:
                triggers["near_daily_support"] = True
            else:
                triggers["near_daily_resistance"] = True

    logger.debug(
        "[%s] breakout_up=%s, breakout_down=%s, near_high=%s, near_low=%s, "
        "near_daily_support=%s, near_daily_resistance=%s, "
        "recent_high=%.4f, recent_low=%.4f, close=%.4f, confirm_bars=%d, min_retests=%d",
        symbol,
        triggers["breakout_up"],
        triggers["breakout_down"],
        triggers["near_high"],
        triggers["near_low"],
        triggers["near_daily_support"],
        triggers["near_daily_resistance"],
        recent_high,
        recent_low,
        current_close,
        confirm_bars,
        min_retests,
    )
    return triggers
