"""Константи та базовий конфіг для SMC-core."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SmcCoreConfig:
    """Налаштування, що визначають базову чутливість SMC."""

    min_swing_bars: int = 3  # Мінімальна довжина свінга в барах
    min_range_bars: int = 12  # Мінімальна довжина діапазону в барах
    eq_tolerance_pct: float = 0.12  # Допуск для рівноваги (EQ) в %
    ote_min: float = 0.62  # Мінімальний рівень OTE в %
    ote_max: float = 0.79  # Максимальний рівень OTE в %
    max_lookback_bars: int = 300  # Максимальна кількість барів для огляду назад
    default_timeframes: tuple[str, ...] = ("5m", "15m", "1h")  # Типові таймфрейми
    bos_min_move_atr_m1: float = 0.6  # Мінімальний рух BOS в ATR на 1 хвилину
    bos_min_move_pct_m1: float = 0.0018  # Мінімальний рух BOS в % на 1 хвилину (0.18 %)
    leg_min_amplitude_atr_m1: float = 0.8  # Мінімальна амплітуда ліг в ATR на 1 хвилину
    ote_trend_only_m1: bool = True  # OTE лише в напрямку тренду на 1 хвилину
    ote_max_active_per_side_m1: int = 1  # Макс. активних OTE на бік на 1 хвилину
    # --- OB_v1 freeze (грудень 2025): ці значення змінюються лише з прямим погодженням Stage4 ---
    structure_event_history_max_minutes: int = (
        60 * 24 * 7
    )  # Історія подій структури в хвилинах (до тижня)
    structure_event_history_max_entries: int = (
        500  # Макс. записів історії подій структури (до 500)
    )
    ob_leg_min_atr_mul: float = 0.8  # Мінімальний множник ATR для ноги OB
    ob_leg_max_bars: int = 40  # Максимальна кількість барів у легу, який розраховуємо
    ob_prelude_max_bars: int = (
        6  # Кількість барів у пошуку свічки-кандидата перед імпульсом
    )
    ob_body_domination_pct: float = 0.65  # Мінімальна домінація тіла для BODY_05 зони
    ob_body_min_pct: float = (
        0.25  # Мінімальний відсоток тіла для слабкої зони (BODY_TOUCH)
    )
    ob_max_active_distance_atr: float | None = (
        15.0  # Додатковий фільтр активних зон (ATR)
    )
    breaker_max_ob_age_minutes: int = 60 * 12  # Максимальний вік OB (720 хв)
    breaker_max_sweep_delay_minutes: int = 180  # Макс. пауза між sweep і BOS
    breaker_level_tolerance_pct: float = 0.0015  # Допуск збігу sweep і OB
    breaker_min_body_pct: float = 0.35  # Мінімальна частка тіла BOS-свічки
    breaker_min_displacement_atr: float = 0.75  # Мінімальний рух між sweep і BOS
    fvg_min_gap_atr: float = 0.5  # Мінімальний gap між свічками в ATR
    fvg_min_gap_pct: float = 0.0015  # Мінімальний gap у % (0.15%)
    fvg_max_age_minutes: int = 60 * 24 * 3  # TTL imbalance, не довше 3 діб


SMC_CORE_CONFIG = SmcCoreConfig()
