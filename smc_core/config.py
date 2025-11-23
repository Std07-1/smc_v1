"""Константи та базовий конфіг для SMC-core."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SmcCoreConfig:
    """Налаштування, що визначають базову чутливість SMC."""

    min_swing_bars: int = 3
    min_range_bars: int = 12
    eq_tolerance_pct: float = 0.12
    ote_min: float = 0.62
    ote_max: float = 0.79
    max_lookback_bars: int = 300
    default_timeframes: tuple[str, ...] = ("5m", "15m", "1h")
    bos_min_move_atr_m1: float = 0.6
    bos_min_move_pct_m1: float = 0.002
    leg_min_amplitude_atr_m1: float = 0.8
    ote_trend_only_m1: bool = True
    ote_max_active_per_side_m1: int = 1


SMC_CORE_CONFIG = SmcCoreConfig()
