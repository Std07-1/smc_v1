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
    default_timeframes: tuple[str, ...] = ("5m", "15m", "1h", "4h")  # Типові таймфрейми
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

    # --- Stage4 / Zones: ширина зон (Випадок D) ---
    # Якщо span_atr = |price_max-price_min|/atr_last > max_zone_span_atr,
    # трактуємо таку зону як надто широку (скоріше range/area) і прибираємо з
    # active/POI (щоб не забивала top-K і не псувала читабельність).
    # None => вимкнено.
    max_zone_span_atr: float | None = 2.0

    # --- Stage4 / Zones: дублі/перекриття (Випадок E) ---
    # IoU (intersection / union) для 1D діапазонів [min,max].
    # Якщо дві зони мають один і той самий (zone_type, role, direction, timeframe)
    # і перекриваються сильніше за поріг — лишаємо одну «кращу», другу вважаємо merged.
    # None => вимкнено.
    zone_merge_iou_threshold: float | None = 0.6

    # --- Stage4/Journal: touch як правило (Випадок F) ---
    # Детермінований touch: перетин [low,high] з [min-eps, max+eps].
    # eps у абсолютних одиницях ціни (не ATR), щоб офлайн-аудит був повторюваним.
    touch_epsilon: float = 0.0

    # --- Stage5 / Execution (1m): мікро-події тільки коли in_play ---
    # Важливо: 1m — лише «тригер», не «мозок». Тому дефолти жорстко антишумні.
    exec_enabled: bool = True
    exec_tf: str = "1m"
    exec_in_play_radius_atr: float = 0.9  # A) радіус in_play навколо POI/targets
    exec_in_play_hold_bars: int = 0  # B) якщо >0 — in_play має триматися N барів
    exec_impulse_atr_mul: float = 0.0  # C) якщо >0 — імпульс (тіло) >= k*ATR
    exec_micro_pivot_bars: int = 8  # вікно для micro BOS/CHOCH
    exec_max_events: int = 6  # жорсткий cap для UI/логів

    # --- Stage3/Liquidity bridge ---
    # Якщо True: bridge може віддавати fallback nearest targets (з низькою confidence)
    # коли немає кандидатів. За замовчуванням вимкнено, щоб не "вигадувати" рівні.
    liquidity_nearest_fallback_enabled: bool = True

    # --- Liquidity pools throttling (P0, QA/UI стабільність) ---
    # Цілі:
    # - зменшити churn pool/WICK_CLUSTER у UI;
    # - контролювати cap/top-K без зміни доменної логіки Stage6.
    #
    # Примітка: дефолти збережені максимально «м'якими», щоб не ламати існуючу поведінку.
    liquidity_pools_max_total: int = 64  # Загальний cap по pools у liquidity.state
    liquidity_eq_topk_per_side: int = 12  # EQH/EQL: топ-K кластерів на бік
    liquidity_wick_cluster_topk_per_side: int = 8  # WICK_CLUSTER: топ-K на бік
    liquidity_sfp_topk_per_side: int = 6  # SFP: топ-K на бік
    liquidity_other_topk_per_group: int = 12  # Інші типи: топ-K на (type, role, side)

    # --- WICK_CLUSTER tracker + антишумні фільтри (Випадок G) ---
    # Мета: зменшити rebucket/flicker/context_flip для WICK_CLUSTER,
    # зберігаючи стабільний `cluster_id` між барами через match по близькості ціни.
    liquidity_wick_cluster_track_enabled: bool = True
    liquidity_wick_cluster_track_tol_pct: float = (
        0.0012  # допуск по рівню (частка ціни)
    )
    liquidity_wick_cluster_track_max_abs_move_atr: float = 0.60  # max зсув рівня в ATR

    # Сильно урізаємо шумні/короткі кластери ще до того, як вони потраплять у pools/UI.
    liquidity_wick_cluster_min_life_bars: int = (
        2  # мін. тривалість (за first_ts/last_ts)
    )
    liquidity_wick_cluster_min_density: float = 0.02  # count / lookback_bars
    liquidity_wick_cluster_min_amp_atr: float = (
        0.20  # max_wick / atr_last (якщо ATR доступний)
    )

    # Preview ≠ truth: на preview за замовчуванням не додаємо шумні "extras" (SFP/WICK_CLUSTER).
    # Це не впливає на live, якщо контекст не містить smc_compute_kind=preview.
    liquidity_preview_include_sfp_and_wicks: bool = False


SMC_CORE_CONFIG = SmcCoreConfig()
