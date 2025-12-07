# Пам'ять Copilot для AiOne_t • v2025-10-29

## Terminal / Python snippets (Windows, PowerShell)

- ОС: Windows 11, VS Code, інтегрований термінал за замовчуванням — Windows PowerShell 5.1 (`powershell.exe`).
- Проєкт AiOne_t-whail розташований у: `C:\Aione_projects\smc_v1`.
- Віртуальне середовище Python: `.venv\Scripts\python.exe` у корені проєкту.
- Для одноразових тестових викликів Python-функцій я використовую **лише** формат
  `python -c "..."` з кодом в один рядок, інструкції розділені `;`.
- Bash-подібний синтаксис типу
  `python - <<'PY' ... PY` (heredoc) у PowerShell **не працює** і має вважатись невалідним для мого середовища.

## Stage1 Cold-start / Warmup

- `bootstrap()` у `app/main.py` прогріває `UnifiedDataStore` зі снапшотів одразу після старту (окремих прапорів більше немає).
- Живі дані йдуть **виключно** з зовнішнього FXCM конектора через Redis канали `fxcm:ohlcv`, `fxcm:heartbeat`, `fxcm:market_status`.
- `_await_fxcm_history()` очікує, поки стрім заповнить мінімум `SCREENING_LOOKBACK` барів на `1m`; якщо їх нема, Stage1 продовжує слухати канал, доки зовнішній конектор не надішле достатньо барів.
- Будь-які прямі виклики біржових API/локальних warmup-скриптів у цьому репозиторії відсутні та вважаються поза межами Stage1.

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
- Пули покривають EQH/EQL, трендові TLQ/SLQ, RANGE_EXTREME (верх/низ активного
   діапазону) та SESSION_HIGH/SESSION_LOW із `snapshot.context` (`pdh/pdl`).
- Магніти групують близькі пули (кластер по ціні), роль магніта = агрегована роль
   кластеру (PRIMARY, COUNTERTREND, NEUTRAL) й відображається у snapshot JSON.
- `SmcHint.liquidity` може бути `None`, але движок завжди повертає валідний стан
   з `meta.pool_count`, `meta.magnet_count`, `meta.bias` для QA/телеметрії.
- Додано детектор SFP/Wick (`smc_liquidity/sfp_wick.py`), що розширює пули ролями PRIMARY/COUNTERTREND та
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

### Підсумок аудиту SMC v1 • 2025-12-7 (етапи 1–3)

#### Етап 1. Каркас та типи

- `SmcCoreEngine.process_snapshot()` єдину точку входу: читає `snapshot.tf_primary`, викликає `smc_structure`, `smc_liquidity`, `smc_zones`, повертає `SmcHint`.
- Типи `SmcInput`, `SmcStructureState`, `SmcLiquidityState`, `SmcZonesState`, `SmcHint`, `SmcTrend`, `SmcRangeState`, `SmcLiquidityType`, `SmcAmdPhase`, `SmcZoneType` описані в `smc_types.py`.
- `SmcCoreConfig` містить усі пороги (swing, range, BOS/CHOCH, OTE, OrderBlock) й синхронізований з документацією.

#### Етап 2. Структура

- `compute_structure_state` виконує обрізання історії, детектор свінгів, побудову HH/HL/LH/LL ніг, розрахунок ATR, BOS/CHOCH, bias, активного ренджу та OTE‑зон.
- `structure_engine.build_legs` реалізує логіку HH/HL/LH/LL.
- Детектор BOS/CHOCH перевіряє умову `max(ATR * bos_min_move_atr_m1, |close| * bos_min_move_pct_m1)` і визначає тип події за попереднім bias.
- `ote_engine.build_ote_zones` генерує зони лише для ніг з амплітудою ≥ `leg_min_amplitude_atr_m1`, використовує фіболевелі 0.62–0.79, ролі PRIMARY/COUNTERTREND прив’язані до bias.

#### Етап 3. Ліквідність та AMD

- `compute_liquidity_state` працює з `tf_primary`, створює EQ‑пули (`build_eq_pools_from_swings`), TLQ/SLQ, range/session‑пули, викликає `sfp_wick` і формує магніти та AMD‑фазу.
- EQH/EQL кластери вимірюють допуск `eq_tolerance_pct`, вимагають ≥2 свінги.
- TLQ/SLQ базуються на bias і останньому свінгу; range і session пули відповідають верхній/нижній межам активного діапазону та рівням `pdh/pdl`.
- `sfp_wick.py` детектує sweep (пробій >0.2 % з протилежним закриттям) і wick‑кластери (фітиль ≥2.5× тіла).
- Магніти агрегують пули, наслідують найвищий пріоритет типу/ролі; ролі PRIMARY/COUNTERTREND/NEUTRAL узгоджені зі структурою.
- FSM AMD: `ACCUMULATION` (ціна в ренджі, спокійний ATR, без нових BOS), `MANIPULATION` (відхилення + sweep), `DISTRIBUTION` (BOS у напрямку тренду, домінують TLQ/SLQ), інакше `NEUTRAL`.

#### Висновки

- Документація точно відповідає реалізації етапів 1–3; ключові механізми та пороги конфігуровані через `SmcCoreConfig`.
- Вся логіка працює виключно на `snapshot.tf_primary`, без прихованих перемикань таймфреймів.
- Ролі OTE/пулів/магнітів узгоджені, що гарантує консистентність між структурою й ліквідністю.
- Каркас `SmcZonesState` уже інтегрований у `SmcCoreEngine`, тож етапи 1–3 готові до розширення Stage 4.
- Документація залишається джерелом істини для подальшої розробки зон та наступних підсистем.

## Етап 4 (`smc_zones`) — перезапуск із фокусом на OB_v1

- Stage1–3 завершено й задокументовано; `SmcCoreEngine.process_snapshot(...)` уже викликає `smc_zones.compute_zones_state(...)`, тому каркас і типи SmcZone зобовʼязані залишатися стабільними.
- Попередній план «усе й одразу» провалився: Breaker/FVG/POI мають власні залежності та edge-кейси, тому етап перезібрано на серію вузьких мікроетапів із чіткими acceptance-критеріями.

### 4.1 Skeleton & Types (закрито)

- Фасад `compute_zones_state(...)` гарантує повернення `SmcZonesState` навіть за порожнього `structure`/кадрів.
- Інваріанти: `zones` — усі знайдені зони, `active_zones` — lookback-фільтр, `poi_zones` — резерв під майбутні POI.
- Телеметрія включає `zone_count`, `orderblocks_total`, `ob_params` (snapshot конфігів) навіть для пустого кейсу.
- Тест: `tests/test_smc_zones_skeleton.py`.

### 4.2 OrderBlock_v1 (у прогресі)

- Детектор працює тільки по ногах зі `SmcStructureState`, що виконують умови: амплітуда ≥ `ob_leg_min_atr_mul * ATR`, тривалість ≤ `ob_leg_max_bars`, є BOS/CHOCH подія зі звʼязком `source_leg`.
- Свічка-прелюдія шукається в межах `ob_prelude_max_bars`; вимоги до тіла: `body_abs ≥ ob_body_min_pct * leg_amplitude`, `body_pct ≥ ob_body_domination_pct`. Великі тіні відсікаються.
- Побудована зона має чіткі поля (`entry_mode`, `role`, `reference_event_type`) і потрапляє в `active_zones`, якщо `origin_time` в межах `cfg.max_lookback_bars` відносно останнього бара.
- Meta `SmcZonesState` зберігає лічильники `orderblocks_primary/countertrend`, `active_zone_count`, `ob_params`. Тести: `tests/test_smc_zones_ob_basic.py` (PRIMARY long, COUNTERTREND, edge-кейси без BOS і з малим тілом).

### 4.3+ Backlog (починаємо лише після freeze OB_v1)

1. **Breaker_v1:** на основі вже записаних OB + свіп ліквідності.
2. **Imbalance/FVG_v1:** детектор розривів між high/low сусідніх барів, meta про partial/full fill.
3. **POI/FTA_v1:** кластеризація OB/FVG/liq-магнітів, перша проблемна область.
4. **Fusion & Stage2 bridge:** після стабілізації зон.

- Кожен наступний підетап → окремі тести, документація (roadmap + відповідний `docs/smc_*.md`). Переходимо далі лише після freeze попереднього кроку.
