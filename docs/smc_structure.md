# SMC Structure Layer

Документ описує підсистему `smc_structure`, яка після етапу «structure + liquidity +
SFP/Wick + AMD» вважається стабілізованою. Шар відповідає за формування
`SmcStructureState`, що йде в `SmcHint` та передається в UI/Stage2 без додаткових
обчислень.

## Призначення та межі

- Єдине джерело правди про тренд, bias, діапазони й BOS/ChoCH на основному ТФ.
- Приймає виключно `SmcInput`, побудований через
  `smc_core.input_adapter.build_smc_input_from_store` (тільки
  `UnifiedDataStore`).
- Не виконує побічних ефектів: усі результати повертаються через dataclass.
- Після формування `SmcStructureState` інші шари не мають права його мутувати.

## Конвеєр обробки

1. **Підготовка фрейму** (`_prepare_frame`) — обрізає історію за `cfg.max_lookback_bars`,
   вирівнює колонку `timestamp`, відкидає NaN.
2. **Свінги** (`swing_detector.detect_swings`) — симетричне вікно `cfg.min_swing_bars`
   для пошуку локальних high/low. Повертає `SmcSwing` із силою (`strength`).
3. **Ноги HH/HL/LH/LL** (`structure_engine.build_legs`) — проходить сусідні свінги,
   класифікує їх у `SmcStructureLeg`.
4. **Тренд** (`structure_engine.infer_trend`) — дивиться на останні high/low-мітки й
   повертає `SmcTrend.UP/DOWN/RANGE/UNKNOWN`.
5. **ATR** (`metrics.compute_atr`) — ATR(14) на основі `high/low/close`, зберігає
   останнє та медіану для телеметрії й порогів.
6. **Події BOS/CHOCH** (`structure_engine.detect_events`) — перевіряє кожну ногу на
   пробій `bos_min_move_atr_m1` або `bos_min_move_pct_m1`, додає `SmcStructureEvent`.
7. **Bias** (`_derive_bias`) — останній CHOCH визначає `bias` (fallback на тренд) та
   `meta.last_choch_ts`.
8. **Діапазон** (`range_engine.detect_active_range`) — ковзаюче вікно
   `cfg.min_range_bars`, оцінює `SmcRangeState` (`INSIDE/DEV_UP/DEV_DOWN`).
9. **OTE-зони** (`ote_engine.build_ote_zones`) — бере ноги після `last_choch_ts`,
   перевіряє амплітуду (`leg_min_amplitude_atr_m1`) і повертає до `cfg.ote_max`
   PRIMARY/COUNTERTREND зон.

## Ключові структури

- `SmcSwing` — індекс бару, час, ціна, тип (`HIGH/LOW`), сила.
- `SmcStructureLeg` — пара свінгів + класифікація HH/HL/LH/LL.
- `SmcStructureEvent` — BOS/CHOCH з напрямом LONG/SHORT і посиланням на ногу.
- `SmcRange` — останній діапазон (high/low/eq/state/start/end).
- `SmcOteZone` — межі 62–79 % від останнього імпульсу, роль визначається bias.
- `SmcStructureState` — агрегує все вище + `meta` (див. нижче).
- `SmcStructureState.event_history` — довга пам'ять BOS/CHOCH (до тижня) для зон/QA.

## Метадані `SmcStructureState.meta`

- `bar_count`, `symbol`, `tf_input`, `snapshot_start_ts`, `snapshot_end_ts`.
- Параметри конфіга: `cfg_min_swing`, `cfg_min_range_bars`, `bos_min_move_*`,
  `leg_min_amplitude_atr_m1`, `ote_*`.
- ATR-телеметрія: `atr_period`, `atr_available`, `atr_last`, `atr_median`.
- Bias-дані: `bias`, `last_choch_ts`, `swing_times`.
- Пам'ять подій: `events_retained_total`, `events_recent_total`.

`meta.last_choch_ts` — єдина опора для обрізання старих імпульсів у OTE, тож не
очищується в інших шарах.

## Конфігурація (см. `smc_core.config.SmcCoreConfig`)

- `min_swing_bars` — ширина вікна детектора свінгів.
- `min_range_bars` — мінімальна довжина вікна для detected range.
- `eq_tolerance_pct` — допуск під час групування swing high/low (впливає на
  діапазони та майбутню ліквідність).
- `max_lookback_bars` — обмеження історії для превенції повільних джерел.
- `bos_min_move_atr_m1`, `bos_min_move_pct_m1` — пороги підтвердження BOS/CHOCH.
- `leg_min_amplitude_atr_m1` — мінімальна амплітуда для включення ноги в OTE.
- `ote_min`, `ote_max`, `ote_trend_only_m1`, `ote_max_active_per_side_m1` — форма й
  кількість OTE-зон.
- `structure_event_history_max_minutes`, `structure_event_history_max_entries` — TTL
  та максимальна кількість BOS/CHOCH у пам'яті.

## Логування та історія подій

- `StructureEventHistory` веде кеш BOS/CHOCH з TTL до тижня, окремо логуючи
  кількість доданих/видалених подій для кожного `symbol/tf`.
- Детектор `structure_engine.detect_events` залишає debug-логи з інформацією про
  пороги й кожну сформовану подію BOS або CHOCH.
- `SmcStructureState.event_history` доступний для `smc_zones`, зокрема OB_v1
  підтягує історичні break-події для стійкої валідації зон.

## OB_v1 + довга пам'ять BOS/CHOCH

- Валідний break = будь-яка подія `SmcStructureEvent` типу BOS/CHOCH із легом HH/HL/LH/LL,
  яку `StructureEventHistory` зберіг у межах TTL. OB_v1 не створює зону без такої події.
- TTL та ємність зафіксовані в `SmcCoreConfig` і не тюнаться без Stage4-рішення:
  `structure_event_history_max_minutes = 10080` (7 діб) та
  `structure_event_history_max_entries = 500` (глобальний ліміт на символ/ТФ).
- Геометрія OB також зафіксована (грудень 2025):
  `ob_leg_min_atr_mul = 0.8`, `ob_leg_max_bars = 40`, `ob_prelude_max_bars = 6`,
  `ob_body_domination_pct = 0.65`, `ob_body_min_pct = 0.25`. Будь-яка зміна цих
  величин потребує нового QA і синхронізації з Stage3.
- Коли TTL вичищає break із історії, OB_v1 перестає відтворювати відповідну зону,
  навіть якщо ціна ще не торкнулась діапазону. Додатковий price-based фільтр для
  активних зон розглядається окремо, але в поточній версії відсутній.
- QA-сценарії для OB_v1 виконуються через `tools/run_smc_5m_qa.py`, який проганяє
  5m snapshot-и XAU/XAG/EUR і зберігає статистику в `reports/smc_qa_5m_summary.json`.

## Інваріанти

- `SmcStructureState` має бути детермінованим: однаковий `SmcInput` → однаковий
  результат.
- Bias змінюється тільки через новий CHOCH; fallback на тренд лише коли
  `events` порожній.
- `ranges` наразі містить лише активний діапазон; історичне зберігання заблоковано,
  доки не з'явиться Stage2-споживач.
- `ote_zones` сортуються за часом формування; PRIMARY завжди відповідає bias.

## QA та інструменти

- `tools/smc_snapshot_runner.py` дозволяє проганяти модуль на історії та знімати
  приклади для документації/тестів.
- `tools/run_smc_5m_qa.py` дає агреговану статистику OB_v1 на XAU/XAG/EUR 5m і
  використовується як базовий регресійний чек.
- Для кожного доопрацювання потрібні юніт-тести у `tests/test_smc_structure_*.py`
  (стискати кейси DataFrame без зовнішніх сервісів).
- Stage1/QA використовують тільки API, описані тут; приховані параметри або
  побічні ефекти вважаються регресією.

## Відомі обмеження / наступні кроки

- Немає історії `SmcRange` (лише активний), тому multi-range-аналітика відкладена.
- Bias поки що залежить лише від останнього CHOCH, без урахування HTF-контексту.
- Подальші роботи (smc_zones + Fusion) повинні розширювати `SmcStructureState`
  через нові ключі в `meta` або додаткові колекції, не ламаючи чинний контракт.
