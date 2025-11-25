# Пам'ять Copilot для AiOne_t • v2025-10-29

## Terminal / Python snippets (Windows, PowerShell)

- ОС: Windows 11, VS Code, інтегрований термінал за замовчуванням — PowerShell (`pwsh`).
- Проєкт AiOne_t-whail розташований у: `C:\Aione_projects\smc_v1`.
- Віртуальне середовище Python: `.venv\Scripts\python.exe` у корені проєкту.
- Для одноразових тестових викликів Python-функцій я використовую **лише** формат
  `python -c "..."` з кодом в один рядок, інструкції розділені `;`.
- Bash-подібний синтаксис типу
  `python - <<'PY' ... PY` (heredoc) у PowerShell **не працює** і має вважатись невалідним для мого середовища.

## SMC API (Етап 1)

- Центральний вхід: `SmcCoreEngine.process_snapshot(snapshot: SmcInput) -> SmcHint`.
  Під капотом звертається до `smc_structure`, `smc_liquidity`, `smc_zones`.
- `SmcInput`: `symbol`, `tf_primary`, `ohlc_by_tf: dict[str, DataFrame]`,
  `context: dict[str, Any]`. Context очікує ключі `trend_context_h1/4h`, `whale_flow`,
  `pdh/pdl/pwh/pwl`, `session_tag`, `vol_regime` та інші документовані поля.
- `SmcHint`: містить `structure/liquidity/zones/signals/meta`. Нові поля додаємо
  тільки через `meta` або `Smc*State.meta`.
- Дані беремо через адаптер
  `build_smc_input_from_store(store, symbol, tf_primary, tfs_extra, limit, context)` —
  він читає виключно з `UnifiedDataStore`.
- QA-entrypoint:
  `python -m tools.smc_snapshot_runner <symbol> --tf 5m --extra 15m 1h --limit 500 --force`
  (S1 режим, без впливу на Stage1/Stage2).

## SMC_STRUCTURE_API

- Вхід: `SmcInput(symbol, tf_primary, ohlc_by_tf, context)` — лише через
   `build_smc_input_from_store`, сторонні дані заборонені.
- Вихід: `SmcHint.structure` (`SmcStructureState`) з trend/bias, діапазонами,
   BOS/CHOCH, OTE та `meta.last_choch_ts`.
- Stage2/Stage3 споживають тільки OTE, де `role == "PRIMARY"` і напрямок
   збігається з `structure.bias`.
- `role == "COUNTERTREND"` використовуємо виключно для QA/діагностики —
   бойова логіка їх ігнорує.
- Bias = останній CHOCH (fallback на `trend`), а `meta.last_choch_ts` — єдина
   опора для обрізання старих імпульсів/OTE.

## SMC_LIQUIDITY_API

- `compute_liquidity_state(snapshot, structure, cfg)` використовує вже готову
   структуру (swings/legs/range/bias) і **ніколи не мутує** `SmcStructureState`.
- `SmcLiquidityState` → список `pools` (`SmcLiquidityPool`) + `magnets`
   (`SmcLiquidityMagnet`) + `amd_phase` + `meta`. Тільки поля `role == "PRIMARY"`
   споживаються Stage2/Stage3 (аналог OTE).
- Пулі покривають EQH/EQL, трендові TLQ/SLQ, RANGE_EXTREME (верх/низ активного
   діапазону) та SESSION_HIGH/SESSION_LOW із `snapshot.context` (`pdh/pdl`).
- Магніти групують близькі пулі (кластер по ціні), роль магніта = агрегована роль
   кластеру (PRIMARY, COUNTERTREND, NEUTRAL) й відображається у snapshot JSON.
- `SmcHint.liquidity` може бути `None`, але движок завжди повертає валідний стан
   з `meta.pool_count`, `meta.magnet_count`, `meta.bias` для QA/телеметрії.
- Додано детектор SFP/Wick (`smc_liquidity/sfp_wick.py`), що розширює пулі ролями PRIMARY/COUNTERTREND та
   зберігає списки знайдених sweep/wick кластера у `SmcLiquidityState.meta`.

## NEXT: SMC_LIQUIDITY

1. Побудувати реальний AMD-phase (Accumulation/Manipulation/Distribution) з опорою
   на range/dev та історію магнітів.
2. Прокинути `liquidity` гілку в UI payload (пас-тру) та Stage2 telemetry, без додаткової
   обробки на UI-шарі.
3. Розширити тести: AMD-phase edge cases, кластеризація магнітів, інтеграція з UI bridge.

## SMC_STAGE3_DOCS (2025-11)

- Stage3 (structure + liquidity + SFP/Wick + AMD) вважається стабілізованим; контракт
   описано в `docs/smc_core_overview.md`, `docs/smc_structure.md`, `docs/smc_liquidity.md`.
- Будь-які доповнення робимо через `meta` або нові state-блоки, не змінюючи існуючі
   поля без плану міграції для UI/Stage2.
- Stage2 споживає лише PRIMARY-ролі та bridge `smc_core.liquidity_bridge.build_liquidity_hint`.
- Перед змінами перевіряти документацію та оновлювати її синхронно зі змінами в коді.

## Розбиття Етапу 4 на підетапи

### Щоб не створити надто складний модуль одразу, доцільно поділити smc_zones на серію незалежних підетапів

### 4.1. Skeleton & Types

- **Опис:** Створення типів та каркаса для зон.
- **Основні дії:**
  - Додати enum `SmcZoneType`, dataclass `SmcZone` і `SmcZonesState`.
  - Оновити `SmcHint`, зробивши поле `zones` опціональним.
  - Створити пакет `smc_zones` з фасадом `compute_zones_state`.
  - Додати заглушки `orderblock_detector.py` та `breaker_detector.py`.
  - Підготувати `imbalance_detector.py`, `poi_fta.py` і базові smoke-тести.

### 4.2. Обчислення Order Block

- **Опис:** Реалізація алгоритму пошуку OB згідно з книжковими правилами.
- **Основні дії:**
  - Використати структуру (BOS/CHOCH), щоб знайти останню протилежну свічку.
  - Перевіряти displacement і наявність FVG перед маркуванням блока.
  - Враховувати розмір тіла й тіней та ставити роль відповідно до bias.
  - Додавати OB у `SmcZonesState.zones` та покривати їх synthetic-тестами.

### 4.3. Breaker Block

- **Опис:** Виявлення пробитих OB та перетворення їх на breaker-блоки.
- **Основні дії:**
  - Визначати повернення ціни в OB і пробій з протилежного боку.
  - Фіксувати ретест, зміну тренду та інвертувати напрямок і роль блока.
  - Перевіряти алгоритм на простих прикладах тестами.

### 4.4. Fair Value Gap / Imbalance

- **Опис:** Детекція FVG (імбалансів) як магнітів.
- **Основні дії:**
  - Шукати розрив між high попередньої свічки та low наступної з порогом.
  - Зберігати області як `IMBALANCE` з роллю, що узгоджується з bias.
  - Відстежувати часткове чи повне закриття FVG на synthetic-даних.

### 4.5. POI та FTA

- **Опис:** Агрегація зон та пошук першої проблемної області.
- **Основні дії:**
  - Об’єднувати близькі центри зон (`poi_cluster_tolerance`) у `POI`.
  - Визначати `FTA` як перший сильний контрівень за напрямком потенційної угоди.
  - Реалізувати кластеризацію та призначення ролей для зон у пулі.

### 4.6. Інтеграція та UI

- позначити `FTA` як перший сильний контрівень у напрямку потенційної угоди.
- Реалізувати кластеризацію та визначення ролей.

### **4.6. Інтеграція та UI**

- **Опис:** Запис зон у `SmcHint`, серіалізація для UI та Stage2.
  - Додати серіалізацію `smc_zones` до `publish_full_state.py`, відображати зони у UI.
  - Підготувати місток для Stage2 (передача інформації про зони).

### Кожен підетап має завершуватися тестами та базовим документуванням API

### Лише після стабілізації попереднього підетапу можна переходити до наступного
