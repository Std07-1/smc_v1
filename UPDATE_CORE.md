<!-- markdownlint-disable MD036 -->

# UPDATE_CORE.md

Журнал змін **core-логіки** (SMC core / liquidity / structure / zones / пайплайни, що впливають на сигнали).

- Для Web/UI змін використовуємо `UPDATE.md`.
- Конвенція: **зміна → тест → UPDATE_CORE.md → відповідь у чаті**.

## Формат запису (конвенція)

Кожен запис має містити:

- **Дата/час** (локально) + коротка назва зміни.
- **Що змінено**: 3–10 пунктів по суті.
- **Де**: ключові файли/модулі.
- **Тести/перевірка**: що саме запускалось і результат.
- **Примітки/ризики** (за потреби): що може вплинути на рантайм.

---

## 2025-12-21 — Stage6 (4.2 vs 4.3): чесний `UNCLEAR` + SCORE_DELTA (CONFLICT) + гейт структури

**Що змінено**

- Stage6 рішення (`4_2/4_3/UNCLEAR`) тепер явно пояснює `UNCLEAR` через `telemetry.unclear_reason`:
  - hard-gates: `NO_LAST_PRICE`, `NO_HTF`, `NO_RANGE`, `NO_STRUCTURE`.
  - soft-gates: `LOW_SCORE` (max score < min) та `CONFLICT` (|score_42-score_43| < score_delta).
- Додано симетричну “конфлікт-логіку” (SCORE_DELTA): якщо обидва сценарії майже рівні — повертаємо `UNCLEAR`, а не “confident lie”.
- Додано гейт `NO_STRUCTURE`, якщо вхідні факти структури недостатні (best-effort: events/swings).
- Non-breaking розширення контракту viewer_state: передаємо `unclear_reason` та `raw_unclear_reason` для прозорості в UI.

**Де**

- smc_core/stage6_scenario.py
- core/contracts/viewer_state.py
- UI_v2/viewer_state_builder.py

**Тести/перевірка**

- `pytest tests/test_smc_stage6_scenario.py` → passed.

**Примітки/ризики**

- Порогові значення (min_score/score_delta) наразі жорсткі в коді (мінімальний диф). Наступною хвилею можна винести в конфіг, якщо потрібен контроль без деплою.

---

## 2025-12-21 — P0 QA: приборкання pool/WICK_CLUSTER (throttling) + preview≠truth для lifecycle

**Що змінено**

**Де**

**Тести/перевірка**

## 2025-12-22 — QA: Випадок C (short-lived / flicker) + UI min-age гейт

**Що змінено**

- У `tools/smc_journal_report.py` додано метрики/секції:
  - `short_lifetime_share_by_type` (частка `lifetime_bars ≤1/≤2` по `type`);
  - `flicker_short_lived_by_type` (removed `reason_sub=flicker_short_lived` по `type`).
- У `UI_v2/viewer_state_builder.py` додано UI-гейт: не показувати «новонароджені» зони/пули, доки не пройде мінімум 1 close-крок (preview не промоутить сутності).
- У `smc_core/engine.py` прокинуто `smc_compute_kind` з `snapshot.context` у `SmcHint.meta` (preview vs close для UI/QA).

**Де**

- tools/smc_journal_report.py
- UI_v2/viewer_state_builder.py
- smc_core/engine.py

**Тести/перевірка**

- `python -m pytest -q tests/test_smc_journal_report_case_c.py` → passed
- `python -m pytest -q tests/test_ui_v2_viewer_state_builder.py` → passed

## 2025-12-16 — Dev process: введено `UPDATE_CORE.md` для змін core-логіки

**Що змінено**

- Додано окремий журнал `UPDATE_CORE.md` для змін у core-логіці (SMC core / liquidity / structure / zones / core-пайплайни).
- Уточнено правило: **зміна → тест → відповідний UPDATE → відповідь у чаті**, де для Web/UI використовується `UPDATE.md`.

**Де**

- UPDATE_CORE.md
- .github/copilot-memory.md

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_smc_core_contracts.py` → `1 passed`.

## 2025-12-16 — Docs(core): зафіксовано повний план “SMC без шуму” (етапи 0–8)

**Що змінено**

- Додано в пам’ять репо поетапний план (0–8) для “трейдерського” SMC-рендера без шуму.
- Окремо підсвічено Етап 0: TF-правда (`tf_exec=1m`, `tf_structure=5m`, `tf_context=[1h,4h]`), телеметрія (`tf_primary/tf_exec/tf_context/bars_used/last_ts/lag_ms`) та gate `NO_5M_DATA`.

**Де**

- .github/copilot-memory.md

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_smc_core_contracts.py` → `1 passed`.

## 2025-12-18 — Wave F (F0/F1/F2/F3): quality gates для SMC/Stage3 (рейки + тести + latency smoke)

**Що змінено**

- Додано документ з матрицею quality-gates (DATA/TF_TRUTH/PRIMARY_ONLY/SERDE/LATENCY/RISK) з режимами `accept/warn/drop` (у F0–F2 лише accept+warn).
- Додано tests-only гейти для DATA + TF_TRUTH + PRIMARY_ONLY, щоб ловити регреси без зміни runtime.
- Додано рейки імпортів (pre-commit boundary): заборонено тягнути SMC пакети у випадкові модулі поза контрольованими винятками.
- Додано latency smoke інструмент для локального виміру p50/p75/p95 та лічильників `no_data/exceptions` (без CI-гейту).
- Додано мінімальний bootstrap `sys.path` у smoke tool, щоб `python tools/smc_latency_smoke.py ...` працював без `-m`.

**Де**

- docs/quality_gates_smc_stage3.md
- tests/test_smc_data_gate_open_close_ms.py
- tests/test_smc_tf_truth_primary_present.py
- tests/test_smc_primary_only_gate.py
- tools/import_rules.toml
- tools/smc_latency_smoke.py

**Тести/перевірка**

- `python -m pre_commit run --all-files` → Passed
- `python tools/audit_repo_report.py` (production surface) → OK (0 findings)
- `python -m pytest -q` → `220 passed`

## 2025-12-18 — Docs(core): уточнено SSOT-план мультитаймфреймового SMC “без шуму” (3 шари + гейти + explain)

**Що змінено**

- Оновлено “план, якому можна довіряти”: SMC як технічний розбір (без “сигналів”), з 3 шарами рендера (Context 1h/4h, Structure 5m, Execution 1m).
- Зафіксовано принципи анти-шуму: ролі TF, ліміти об’єктів, `why[]` + `score`, та “чесні гейти” `NO_*` замість фантазій.
- Розширено опис етапів 0–8: TF-правда/телеметрія/гейти → реальні TF у store → структура/ліквідність/POI → execution → сценарій 4.2/4.3 → UI → QA.

**Де**

- .github/copilot-memory.md

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_smc_core_contracts.py` → passed.

---

## 2025-12-19 — Stage0: `tf_health` у `smc_hint.meta` (1m/5m/1h/4h)

**Що змінено**

- Додано `meta.tf_health` у `SmcHint` (і в gated, і в compute кейсі): `has_data/bars/last_ts/lag_ms` для TF з плану (`tf_exec`, `tf_structure`, `tf_context`).
- Розширено Stage0 тест, щоб фіксувати наявність `tf_health` і коректний `has_data` при відсутньому 5m.

**Де**

- app/smc_producer.py
- tests/test_smc_tf_truth_primary_present.py

**Тести/перевірка**

- `pytest -q tests/test_smc_tf_truth_primary_present.py` → passed.

---

## 2025-12-19 — Stage1(Data): матеріалізація 1m→5m→1h→4h у `UnifiedDataStore`

**Що змінено**

- `UnifiedDataStore.get_df(...)` тепер уміє on-demand матеріалізувати старші TF (`5m/1h/4h`) із нижчих TF за SSOT-ланцюжком `1m→5m→1h→4h`, якщо snapshot для target TF відсутній.
- Агрегатор повертає лише **complete** бари (без гепів усередині групи) і зберігає результат як snapshot у store (Disk), щоб наступні читання були такими ж “реальними”, як і для `1m`.
- Додано юніт-тести на матеріалізацію та персистентність snapshot-ів у `tmp_path`.

**Де**

- data/unified_store.py
- tests/test_uds_tf_materialization.py

**Тести/перевірка**

- `pytest -q tests/test_uds_tf_materialization.py tests/test_smc_input_adapter.py tests/test_tf_coverage_report.py` → passed.

---

## 2025-12-19 — Data: прибрано FutureWarning pandas у `UnifiedDataStore.get_df()`

**Що змінено**

- Прибрано `FutureWarning` pandas про конкатенацію DataFrame з empty/all-NA колонками: перед `pd.concat(...)` тепер відсікаються all-NA колонки у кожному фреймі, а після — гарантується базовий каркас OHLCV колонок.
- Прибрано попередження про застарілу частоту `'H'`: для агрегації використовуємо `1h/4h` замість `1H/4H`.

**Де**

- data/unified_store.py

**Тести/перевірка**

- `pytest -q tests/test_uds_tf_materialization.py` → passed.

---

## 2025-12-19 — Stage3(Liquidity): `liquidity_targets` (internal/external) + nearest targets

**Що змінено**

- Додано побудову `liquidity_targets[]` як “магнітів” ліквідності з ролями `internal/external`, типом, силою, TF і причиною.
- `internal` базується на вже знайдених `magnets` на primary TF (зазвичай 5m).
- `external` (baseline) витягується з HTF `1h/4h` як pivot swing highs/lows + fallback на extremes.
- Розширено `external`: підтримка session/day/week extremes через `SmcInput.context` (`pdh/pdl/pwh/pwl`) + легкий fallback з HTF OHLCV.
- Додано окремі ключі `session_high/session_low` (з fallback на `pdh/pdl`) і розширено FXCM contract, щоб ці поля не губились і доходили до UI через `fxcm` meta.
- У `Stage2`-місті додано поля `smc_liq_nearest_internal` та `smc_liq_nearest_external` (за наявності `ref_price` і `liquidity_targets`).

**Де**

- smc_liquidity/targets.py
- smc_liquidity/**init**.py
- smc_core/liquidity_bridge.py
- tests/test_smc_liquidity_basic.py
- tests/test_smc_liquidity_bridge.py

**Тести/перевірка**

- `pytest -q tests/test_smc_liquidity_basic.py tests/test_smc_liquidity_bridge.py` → passed.

---

## 2025-12-19 — Stage0/Context: власні Asia/London/NY session highs/lows з OHLCV + доступ через hints

**Що змінено**

- `smc_core.input_adapter.build_smc_input_from_store()` тепер сам рахує сесійні екстремуми з OHLCV за правилами UTC (ASIA 22–07, LONDON 07–13, NY 13–22) і додає в `SmcInput.context` стабільні ключі:
  - `smc_session_tag`, `smc_session_start_ms`, `smc_session_end_ms`, `smc_session_high`, `smc_session_low`, `smc_sessions`.
- `SmcCoreEngine` прокидає ці ключі у `SmcHint.meta`.
- `smc_core.liquidity_bridge.build_liquidity_hint()` додає `smc_session_*`/`smc_sessions` у вихідний hint, щоб downstream стадії могли діставати їх як SSOT через bridge.
- `smc_liquidity`: session pools та external targets тепер використовують власні `smc_session_*`/`smc_sessions` замість залежності від FXCM `session_high/session_low`.
- У `smc_sessions[ASIA|LONDON|NY]` додано поля `range` та `mid` для UI (best-effort; `null`, якщо high/low відсутні).

**Де**

- smc_core/input_adapter.py
- smc_core/engine.py
- smc_core/liquidity_bridge.py
- smc_liquidity/pools.py
- smc_liquidity/targets.py
- tests/test_smc_input_adapter.py
- tests/test_smc_liquidity_basic.py
- tests/test_smc_liquidity_bridge.py

**Тести/перевірка**

- `pytest -q tests/test_smc_input_adapter.py tests/test_smc_liquidity_basic.py tests/test_smc_liquidity_bridge.py` → passed.

---

## 2025-12-19 — Stage2(Liquidity bridge): literal acceptance для nearest targets (always present) + опційний fallback

**Що змінено**

- У `smc_core.liquidity_bridge.build_liquidity_hint()` ключі `smc_liq_nearest_internal` / `smc_liq_nearest_external` тепер **завжди присутні** в output (можуть бути `null`).
- Додано пояснення та “чесну якість” результату:
  - `smc_liq_nearest_*_why` (наприклад `no_ref_price`, `no_candidates_internal`, `from:liquidity_targets`).
  - `smc_liq_nearest_*_confidence` (0.0..1.0).
- Додано kill-switch у `SmcCoreConfig`: `liquidity_nearest_fallback_enabled` (default `False`). Якщо увімкнути — bridge може віддати fallback nearest targets з низькою `confidence=0.1` і явним reason.

**Де**

- smc_core/liquidity_bridge.py
- smc_core/config.py
- tests/test_smc_liquidity_bridge.py

**Тести/перевірка**

- `pytest -q tests/test_smc_liquidity_bridge.py tests/test_smc_primary_only_gate.py` → passed.

---

## 2025-12-20 — Stage4(Zones/POI): закриття етапу (OB/Breaker/FVG + POI/FTA, без шуму)

**Що змінено**

- Зафіксовано Stage4 як завершений: `compute_zones_state()` формує базові зони (OB/Breaker/FVG(Imbalance)) і будує POI/FTA-відбір.
- Інваріанти UX для трейдера: `active_zones` капиться (≤3/side) + distance/time фільтри; UI може малювати тільки active, без «павутини».
- POI дає explain-семантику: `score` (0–100), `why[]`, `filled_pct`, `state`, `distance_atr` (best-effort) — консюмери не мають домальовувати “магію”.

**Де**

- smc_zones/**init**.py
- smc_zones/orderblock_detector.py
- smc_zones/breaker_detector.py
- smc_zones/fvg_detector.py
- smc_zones/poi_fta.py

**Тести/перевірка**

- `pytest -q tests/test_smc_zones_skeleton.py tests/test_smc_zones_ob_basic.py tests/test_smc_zones_poi_active.py tests/test_smc_zones_fvg_basic.py tests/test_smc_zones_breaker_basic.py` → passed.

**Примітки/ризики**

- Breaker залежить від наявності `liquidity.meta.sfp_events` та BOS у структурі; при відсутності — детектор чесно повертає порожній список.

---

## 2025-12-20 — Stage5(Execution 1m): micro-події лише коли in_play біля POI/targets

**Що змінено**

- Додано Stage5 як окремий модуль `smc_execution`: 1m працює як «тригер», а не як «мозок».
- Введено `execution_events[]` (SWEEP, MICRO_BOS/MICRO_CHOCH, RETEST_OK) з жорстким антишумним правилом: події генеруються **лише** якщо `in_play=true`.
- `in_play` визначається як: ціна в POI (або з буфером) **або** близько до target-лівелів (liquidity pools/magnets + range high/low/eq + сесійні екстремуми з context).
- Додано 3 режими керування шумом через конфіг: A) radius (ATR), B) hold N bars, C) impulse >= k*ATR.

**Де**

- smc_execution/**init**.py
- smc_core/engine.py
- smc_core/smc_types.py
- smc_core/config.py

**Тести/перевірка**

- `pytest -q tests/test_smc_execution_in_play_gate.py tests/test_smc_execution_events_basic.py` → passed.

---

## 2025-12-20 — Stage5(Execution 1m): freeze (зафіксовано як є)

**Що змінено**

- Зафіксовано Stage5 як “trigger-only” шар: micro-події показуємо лише як підтвердження біля POI/targets (без спроб перетворити 1m у “мозок”).
- Далі Stage5 не розвиваємо/не «підкручуємо» без окремої явної команди (щоб не розмивати довіру та не ловити шум).

**Тести/перевірка**

- Немає нових runtime-змін; тести Stage5 залишаються “green” (див. запис вище).
