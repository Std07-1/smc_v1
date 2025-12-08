# Breaker_v1 — статус та freeze (стан на 2025-12-08)

## Конфіги

| Параметр | Значення | Примітка |
| --- | --- | --- |
| `breaker_max_ob_age_minutes` | 720 | TTL первинного OB до моменту BOS. |
| `breaker_max_sweep_delay_minutes` | 180 | Максимальна пауза між sweep (SFP) та BOS. |
| `breaker_level_tolerance_pct` | 0.0015 | Базовий допуск для збігу sweep-рівня з OB; фактичний котирувальний tol = max(`price_span * 0.15`, `anchor * 0.0015`). |
| `breaker_min_body_pct` | 0.35 | Мінімальна частка тіла BOS-свічки, щоб брати BODY-зону. |
| `breaker_min_displacement_atr` | 0.75 | Мінімальний ATR-дисплейсмент між sweep та BOS. |
| `ob_max_active_distance_atr` | 15.0 | Фільтр активних зон для OB/BREAKER у SmcZonesState. |

## QA-набори

QA прогін виконано скриптом `python tools/run_smc_5m_qa.py` (500 останніх закритих барів на 5m для кожного символу). Результати з `reports/smc_qa_5m_summary.json`:

- **XAUUSD (2025-12-03…2025-12-07)** — `breaker_zones_total = 0`, `breaker_active_zones_total = 0`. На поточному відрізку немає валідних PRIMARY OB, отже breaker’ів теж немає.
- **XAGUSD (2025-11-20…2025-11-21)** — `breaker_zones_total = 0`, `breaker_active_zones_total = 0`. Є дві PRIMARY SHORT OB-зони (age 835–1035 хвилин) з `max_zone_distance_atr ≈ 13.3`, проте sweep/BOS ланцюжок для breaker не виконано.
- **EURUSD (2025-11-25…2025-11-26)** — `breaker_zones_total = 0`, `breaker_active_zones_total = 0`. Дані спокійні; OB та breaker відсутні через брак BOS.

Зрізи XAU 10–14 та 17–21 (звідки походять golden-set Stage2) входять у джерельні JSONL файли; на поточному 500‑баровому відрізку вони не потрапили, тому breaker’ів немає. Поки статистика відповідає очікуванню: breaker повинен з’являтися лише на волатильних ділянках зі sweep + протилежним BOS.

> Breaker працює тільки на даних, де структура надає валідний `atr_last`/`atr_median`. На відрізках без ATR очікувано повертається пустий список, бо displacement не нормується.

## Правило freeze

1. Breaker_v1 вважається замороженим. Будь-яка зміна `breaker_*` або `ob_max_active_distance_atr` потребує нового прогону `tools/run_smc_5m_qa.py`, оновлення цього файлу та короткого опису змін у changelog’ах.
2. QA потрібно виконувати на мінімум трьох символах (XAUUSD, XAGUSD, EURUSD) з останніх 500 барів і, за потреби, на golden-зрізах 10–14 / 17–21 для XAUUSD.
3. До внесення нових результатів фіксованим вважається саме наведений вище набір параметрів.
