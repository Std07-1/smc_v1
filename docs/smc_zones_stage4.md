# Breaker_v1 (Етап 4.3)

Документ фіксує мінімально життєздатну реалізацію breaker-зон поверх замороженого OB_v1 пайплайна. Усі правила, патерни й телеметрія сформульовані для продакшн-збірки Stage 4 і не можуть змінюватися без оновлення цього файлу.

## Послідовність подій

1. **Первинний Order Block.** Беремо лише PRIMARY OB (з ``role == "PRIMARY"``) та обов'язково зберігаємо посилання на BOS/CHOCH, який його підтвердив.
2. **Ліквідність/маніпуляція.** Після побудови OB має з'явитися sweep події типу ``SFP`` з боку зони: для SHORT OB шукаємо ``side == "HIGH"``, для LONG OB — ``side == "LOW"``. Подія повинна відбутися не пізніше ``breaker_max_sweep_delay_minutes`` після ``origin_time`` зони й потрапляти у ціновий діапазон з допуском ``breaker_level_tolerance_pct``.
3. **Протилежний BOS.** Після sweep протягом того ж вікна шукаємо ``SmcStructureEvent`` із напрямом, протилежним напрямку вихідного OB. Усі події мають вкладатися в TTL ``structure_event_history`` (``structure_event_history_max_minutes``). Це й фіксує breaker — supply перетворюється на demand і навпаки.

Без виконання всіх трьох пунктів зона breaker не створюється.

## Геометрія та поля

- Ціновий діапазон береться з тіла BOS-свічки. Якщо тіло надто мале, використовуємо повний high/low.
- ``direction`` завжди збігається з напрямом нового BOS.
- ``zone_id`` містить ``brk_{symbol}_{tf}_{bos_index}``, щоб відстежувати подію в QA.
- ``components`` мінімально містять ``["breaker", source_ob_id, bos_event_id]``.
- ``role`` успадковується від вихідного OB (PRIMARY лише з PRIMARY).

## Метадані breaker.meta

| Ключ | Опис |
| --- | --- |
| ``derived_from_ob_id`` | ``zone_id`` вихідного OB (дублюється також у ``source_orderblock_id`` задля сумісності) |
| ``sweep_time`` | ISO8601 момент SFP |
| ``sweep_level`` | Рівень SFP, що підтвердив sweep |
| ``sweep_source`` | Походження sweep (swing/range/session тощо) |
| ``bos_time`` | Момент BOS, який інвертував зону |
| ``bos_event_type`` | ``BOS``/``CHOCH`` (з історії структури) |
| ``break_event_id`` | Посилання на BOS/CHOCH, що зламав OB |
| ``breaker_age_min`` | Вік OB на момент BOS у хвилинах |
| ``distance_to_sweep`` | Абсолютна різниця (у пунктах) між ціновим центром OB і SFP |
| ``breaker_params`` | Знімок використаних конфігів breaker |

## Конфіг SmcCoreConfig

| Поле | Тип | Значення за замовчуванням | Опис |
| --- | --- | --- | --- |
| ``breaker_max_ob_age_minutes`` | int | 720 | Максимальний вік OB на момент BOS |
| ``breaker_max_sweep_delay_minutes`` | int | 180 | Макс. пауза між sweep і BOS |
| ``breaker_level_tolerance_pct`` | float | 0.0015 | Допуск збігу sweep-рівня з OB |
| ``breaker_min_body_pct`` | float | 0.35 | Мінімальна частка тіла BOS-свічки для побудови BODY-зони |
| ``breaker_min_displacement_atr`` | float | 0.75 | Мінімальний ATR-дислпейсмент між sweep та BOS |

QA/бектести повинні логувати хоча б кількість знайдених breaker-зон по символах, їх ролі та відповідність фільтрам (вік OB, sweep-delta, допуск). Будь-які зміни алгоритму документуються окремим пунктом changelogу в цьому файлі.

## Вимоги до даних Breaker_v1

- **Обов'язковий ATR.** Breaker розраховує displacement як \|BOS − sweep\| / ATR. Якщо структура не надає валідний `atr_last` (або `atr_median`) для `tf_primary`, детектор повертає порожній список. Це дизайн-рішення, а не баг: без ATR немає нормованої шкали сили. Запуски на «сирих» даних без ATR треба або забороняти, або вводити окремий fallback після нового RFC/QA.
- **Толеранс sweep ≈ 15% span OB.** Порог `breaker_level_tolerance_pct` комбінується з відносним span (`max(price_span * 0.15, anchor * 0.0015)`). На широких блоках це може впускати sweep глибоко всередину діапазону, тому будь-які зміни множника потребують нового QA і опису в статус-доку.

## FVG/Imbalance_v1 (підготовка)

- **Шаблон 3 свічки.**
 	- Bullish FVG детектується, якщо `low[i+1] > high[i]` і gap перевищує хоча б один поріг (`fvg_min_gap_atr * ATR` або `fvg_min_gap_pct * price`).
 	- Bearish FVG — дзеркально (`high[i+1] < low[i]`).
- **Конфігуровані пороги:**
 	- `fvg_min_gap_atr`: мінімальна різниця у частках ATR.
 	- `fvg_min_gap_pct`: мінімальний gap у відсотках до поточного mid/close.
 	- `fvg_max_age_minutes`: TTL зони, після якого imbalance не потрапляє до `active_zones`.
- **У `SmcZone`:**
 	- `zone_type = SmcZoneType.IMBALANCE`.
 	- `price_min`, `price_max` = межі gap; `filled_pct` (0–1) показує, наскільки зона вже закрита.
 	- `role` визначається через bias: якщо bias збігається з напрямком FVG, ставимо PRIMARY; протилежний — COUNTER; інакше NEUTRAL.
 	- `meta` має містити джерельні індекси свічок та `age_min`.

Наступні кроки: додати ці параметри до `SmcCoreConfig`, реалізувати `smc_zones.fvg_detector` та відобразити нові лічильники у `tools/run_smc_5m_qa.py` перед freeze.
