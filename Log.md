# Log змін (AiOne_t / smc_v1)

Цей файл — журнал змін у репозиторії. Формат записів: дата/час → що зроблено → де зроблено → причина → тести/перевірки → ризики/нотатки.

## 2025-12-24 — Bugfix: replay_snapshot_to_viewer падав на WICK_CLUSTER timestamps

- Симптом: `tools.replay_snapshot_to_viewer` падав з `AttributeError: 'str' object has no attribute 'isoformat'` у `smc_liquidity/sfp_wick.py` під час серіалізації `wick_clusters`.
- Причина: `_track_wick_clusters` міг підхопити `first_ts/last_ts` із `prev_wick_clusters` у `snapshot.context`, де timestamp інколи був ISO-рядком (JSON-friendly), а не `pd.Timestamp`.
- Фікс: у [smc_liquidity/sfp_wick.py](smc_liquidity/sfp_wick.py) додано нормалізацію `first_ts/last_ts` до `pd.Timestamp` перед побудовою `wick_meta` та `SmcLiquidityPool`.
- Тести/перевірки: додано регресійний тест [tests/test_smc_sfp_wick.py](tests/test_smc_sfp_wick.py) (`test_wick_cluster_prev_first_ts_string_does_not_crash`).

## 2025-12-24 — SMC producer: стабільність стану при `fast_symbols` флапах

- Симптом: інколи UI показує `Trend/Bias/Range/AMD = UNKNOWN` і немає pools/zones, хоча тики/ціна є.
- Ймовірний тригер: `fast_symbols` може тимчасово повертати неповний список → `smc_producer` видаляв asset зі `SmcStateManager`, а при повторному додаванні ініціалізував порожній INIT-стан.
- Фікс: у [app/smc_producer.py](app/smc_producer.py) оновлення `fast_symbols` стало недеструктивним:
 	- removed-символи не `pop()` з state; замість цього ставимо `signal=SMC_PAUSED` + hint, але **зберігаємо останній `smc_hint`**.
 	- додано юніт-тест [tests/test_smc_producer_fast_symbols_update.py](tests/test_smc_producer_fast_symbols_update.py).

## 2025-12-24 — SMC producer: не затирати `smc_hint` gated-empty Stage0 (preserve last known)

- Симптом: при Stage0 gate (наприклад `NO_5M_DATA`/`INSUFFICIENT_5M`/`STALE_5M`) продюсер міг записати `SmcHint` з `structure/liquidity/zones=None`.
- Наслідок: це перезаписувало попередній валідний стан → UI ставав повністю порожнім (`Trend/Bias/AMD = UNKNOWN`).
- Фікс: у [app/smc_producer.py](app/smc_producer.py) додано правило `_preserve_previous_hint_if_gated(...)`:
 	- якщо новий hint має `meta.gates` і він gated-empty, а попередній hint містить хоча б один блок (`structure`/`liquidity`/`zones`), то зберігаємо попередній, але оновлюємо `meta` з нового та ставимо `meta.smc_hint_preserved=true`.
- Тести/перевірки: додано [tests/test_smc_producer_preserve_hint_on_gates.py](tests/test_smc_producer_preserve_hint_on_gates.py).
- Ризики/нотатки:
 	- UI може показувати “останній відомий” стан, але це краще за порожній екран; причина прозоро видима через `meta.gates/history_state/tf_health`.

## 2025-12-24 — Рейка процесу: заборона змін без `Log.md` (log-gate)

- Вимога: записи в `Log.md` — правило топ-рівня (вище тестів/чат-відповідей).
- Зміна: додано рейку-скрипт [tools/check_log_updated.py](tools/check_log_updated.py).
 	- Політика: якщо у `git diff` є зміни у системних файлах (код/конфіг/тести/UI/tools), то `Log.md` має бути серед змінених файлів.
 	- Артефакти/дані (наприклад `reports/`, `datastore/`, `tmp/`) не вимагають Log.md.
- Примітка: скрипт використовує лише `git diff` (read-only), без жодних git-операцій.
 	- Інтеграція: додано hook `log-md-required` у [.pre-commit-config.yaml](.pre-commit-config.yaml), і додано `pre-commit` у [requirements-dev.txt](requirements-dev.txt).

## 2025-12-24 — Висновки з QA-прогону (XAUUSD): інваріанти та фокус

- Головний шумогенератор: **pools/WICK_CLUSTER**, не zones.
 	- Preview vs close по pools нестабільний (низький jaccard, десятки preview_only/close_only на бар).
 	- Дуже короткий lifetime у pools (частка lifetime<=1/<=2 висока) → фліккер як системна норма.
 	- “Вбивця довіри”: `touched_late` після `invalidated_rule` та `evicted_cap` (semantics remove ≠ правда).
- Zones: між preview/close стабільні, але є “килим” overlap/dup (потрібен dedup/merge у presentation-layer).
- Інваріанти/обмеження (обов’язково дотримуватись):
 	- **CHOCH не чіпаємо** (це базовий “truth” індикатор).
 	- Формування BOS також **не чіпаємо** (допускаємо лише presentation-політики без зміни детекції).
 	- Не чіпаємо “успішні” магніти/пули (EQL/EQH тощо) без прямої задачі.
 	- Ключова ідея: **розділити “існує в правді” і “видиме в UI”**; `evicted_cap` має ставати hidden/archived, а не removed.
- План робіт (узгоджений напрямок):
 	- Спершу low-risk UI/presentation стабілізація (matured-only/anti-flicker, hidden-евікшн, маркери touched).
 	- Далі — lifecycle/ID семантика саме для pools (найвищий ROI по `touched_late`).
 	- Після цього — dedup/merge зон по IoU (читабельність, без зміни “truth”).

## 2025-12-24 — UI_v2 presentation: pools close-only + matured-only (age>=2) + лічильники truth/shown

- Ціль: різко зменшити фліккер pools без зміни SMC truth (за результатами QA: pools/WICK_CLUSTER — головний шумогенератор).
- Зміни:
 	- У [UI_v2/viewer_state_builder.py](UI_v2/viewer_state_builder.py) введено різні пороги newborn:
  		- zones: `MIN_CLOSE_STEPS_BEFORE_SHOW_ZONES=1` (як було за замовчуванням);
  		- pools: `MIN_CLOSE_STEPS_BEFORE_SHOW_POOLS=2` (matured-only).
 	- Pools зроблено **close-only**: на preview pools не віддаємо в `viewer_state.liquidity.pools`.
 	- Додано `viewer_state.liquidity.pools_meta` з лічильниками truth vs shown:
  		- `truth_count`, `shown_count`, `filtered_preview_count`, `filtered_newborn_count`, `dropped_by_cap_count`, `policy`.
 	- Контракт розширено non-breaking: [core/contracts/viewer_state.py](core/contracts/viewer_state.py) (`SmcViewerLiquidity.pools_meta`).
- Тести/перевірки:
 	- Оновлено [tests/test_ui_v2_viewer_state_builder.py](tests/test_ui_v2_viewer_state_builder.py) під нову політику (pools стають видимими з 3-го close).

## 2025-12-24 — UI_v2 presentation: cap-evicted pools => hidden (TTL) + причини в `pools_meta`

- Ціль: зняти “вбивцю довіри” для pools — коли сутність «пропадає» через cap/top-K і потім раптом фіксується як touched_late.
 	- Важливо: це **лише presentation-логіка**; SMC truth/детекцію не змінюємо.
- Зміни:
 	- У [UI_v2/viewer_state_builder.py](UI_v2/viewer_state_builder.py) додано кеш-політику для pools:
  		- якщо pool був показаний у UI, а на наступному close вилетів за `MAX_POOLS` (cap), він помічається як `hidden` у кеші на `POOLS_HIDDEN_TTL_CLOSE_STEPS` close-кроків;
  		- у `viewer_state.liquidity.pools_meta` додано: `hidden_count`, `hidden_reasons`, а також `policy.hidden_ttl_close_steps`.
- Тести/перевірки:
 	- Додано тест cap-евікшну: [tests/test_ui_v2_viewer_state_builder.py](tests/test_ui_v2_viewer_state_builder.py).

## 2025-12-24 — UI_v2 presentation: `touched_while_hidden` для pools (строго з truth)

- Ціль: прибрати “late-touch” як trust-killer на presentation-рівні, не підміняючи правду.
 	- Якщо pool прихований через `evicted_cap`, а в truth він отримав нові touch-ознаки, UI має змогу показати це як `touched_while_hidden`, а не як “щось зникло/з’явилось”.
- Зміни:
 	- У [UI_v2/viewer_state_builder.py](UI_v2/viewer_state_builder.py) додано відстеження touch-сигнатури для pools у кеші (лише на close):
  		- сигнал береться **строго з truth**: `n_touches` та/або зростання `last_time`.
  		- у `viewer_state.liquidity.pools_meta` додано: `touched_while_hidden_count`, `touched_while_hidden_reasons`.
- Тести/перевірки:
 	- Розширено тест cap-евікшну: [tests/test_ui_v2_viewer_state_builder.py](tests/test_ui_v2_viewer_state_builder.py) (симуляція `n_touches` зростає, поки pool hidden).

## 2025-12-24 — UI_v2 presentation: стабільний top-K pools + canonical keys (менше remove+create)

- Ціль: зменшити churn у списку pools (remove+create/evicted фліккер) через нестабільний порядок у truth.
 	- Важливо: це **presentation-only**; SMC truth/детекція/пороги не змінюються.
- Зміни:
 	- У [UI_v2/viewer_state_builder.py](UI_v2/viewer_state_builder.py) selection `MAX_POOLS` тепер детермінований:
  		- сортування pools за `(strength desc, n_touches desc, pool_key)` перед cap.
 	- Розширено canonical key для pools:
  		- `_pool_key(...)` використовує стабільні `meta` ID (де доступні): `cluster_id`/`wick_cluster_id` (WICK_CLUSTER), `range_extreme_id` (RANGE_EXTREME), `sfp_id` (SFP*).
- Тести/перевірки:
 	- Оновлено тест cap-евікшну під новий детермінований top-K (evict через зміну `strength`): [tests/test_ui_v2_viewer_state_builder.py](tests/test_ui_v2_viewer_state_builder.py).

## 2025-12-24 — UI_v2 presentation: dedup/merge зон по IoU (canonical + stack=N)

- Ціль: прибрати “килим” overlap/dup по zones у UI без зміни SMC truth.
- Зміни:
 	- У [UI_v2/viewer_state_builder.py](UI_v2/viewer_state_builder.py) додано merge зон у presentation:
  		- кластеризація зон за `(zone_type, direction, role, timeframe)`;
  		- merge якщо IoU по price-range >= `ZONES_MERGE_IOU_THRESHOLD`;
  		- для кластерів додаємо `meta.stack=N`; канонічний `price_min/max` **не роздуваємо**, а envelope зберігаємо в `meta.envelope_min/max`.
- Тести/перевірки:
 	- Додано тест overlap-мерджу: [tests/test_ui_v2_viewer_state_builder.py](tests/test_ui_v2_viewer_state_builder.py).

## 2025-12-24 — UI_v2 presentation: `zones_meta` (truth/shown) для QA-гейтів

- Ціль: зробити merge/фільтрацію зон прозорою та вимірюваною (truth vs shown), щоб QA не «покращувався» лише за рахунок приховування.
- Зміни:
 	- Контракт розширено non-breaking: [core/contracts/viewer_state.py](core/contracts/viewer_state.py) (`SmcViewerZones.zones_meta`).
 	- У [UI_v2/viewer_state_builder.py](UI_v2/viewer_state_builder.py) додано `zones_meta` з лічильниками:
  		- `truth_count`, `shown_count`, `merged_clusters_count`, `merged_away_count`, `max_stack`, `filtered_missing_bounds_count`.
  		- `policy`: `merge_iou_threshold`, `min_close_steps_before_show`, `max_zones_shown` (поки `null`), `scope_key=active_zones`.
 	- Для debug/прозорості merge додається `meta.merged_from_ids_sample` (до 3 id).
- Тести/перевірки:
 	- Розширено overlap-мердж тест + додано no-merge тест: [tests/test_ui_v2_viewer_state_builder.py](tests/test_ui_v2_viewer_state_builder.py).

## 2025-12-24 — QA gate (KPI) для кроків 1–5 у `tools/smc_journal_report.py`

- Вимога: після кожного кроку фіксувати не “враження”, а числа (KPI) і мати автоматичний pass/fail.
- Зміни у [tools/smc_journal_report.py](tools/smc_journal_report.py):
 	- Додано режим `--gate`, який друкує таблицю `qa_gate_kpi(steps_1_5)` і повертає **exit code 3** при FAIL.
 	- Пороги задаються CLI-аргументами:
  		- `--gate-min-pools-jaccard-p50`
  		- `--gate-max-pools-short-lifetime-le1-share`
  		- `--gate-max-zone-overlap-frames-share-iou-ge-08`
  		- `--gate-max-shown-counts-rel-range`
 	- Фікс CLI: аргумент з `0.8` у назві перейменовано на `...-08`, щоб уникнути крихкого доступу до `argparse` атрибутів.
 	- Фікс KPI: `shown_*_counts_rel_range` рахуємо з `frame.active_ids` (довжина set), а не з неіснуючого `frame.counts`.
- Smoke-команда (м’які пороги, лише для перевірки, що все рахується):
 	- `C:/Aione_projects/smc_v1/.venv/Scripts/python.exe tools/smc_journal_report.py --dir reports/smc_journal_p0_run1 --symbol XAUUSD --gate --gate-min-pools-jaccard-p50 0.0 --gate-max-pools-short-lifetime-le1-share 1.0 --gate-max-zone-overlap-frames-share-iou-ge-08 1.0 --gate-max-shown-counts-rel-range 999`
- Примітка: пороги для “реального” гейту треба калібрувати під наші очікувані пост-фікс KPI (окремою хвилею), щоб gate ловив регресії, а не просто “проходив завжди”.

---

## 2025-12-22

- **Ініціалізація журналу**: створено `Log.md` у корені репозиторію.
- **Причина**: вимога процесу — завжди робити змістовні записи про виконані дії/зміни ("дія/зміна → тести → запис → відповідь" або "дія/зміна → запис → відповідь").
- **Тести/перевірки**: не запускались (зміна лише документаційна).
- **Ризики/нотатки**: відтепер всі подальші правки коду/конфігів/логіки супроводжуються записом у цьому файлі.

- **Розслідування: "застарілі свічки" у UI + потреба рестарту**
 	- **Спостереження**: UI може показувати старі свічки (графік не оновлюється), тоді як бекенд/SMC виглядає живим.
 	- **Root-cause (ймовірний, підтверджено кодом)**: у web-клієнті `UI_v2/web_client/app.js` формувався `to_ms` навіть коли replay-курсор відсутній:
  		- `appState.replay.lastCursorMs` ініціалізований як `null`;
  		- `Number(null) === 0`, отже клієнт додавав `&to_ms=0` до кожного запиту `/smc-viewer/ohlcv`;
  		- сервер, отримавши `to_ms=0`, відсікав всі бари як "майбутні" → `bars=[]`;
  		- у клієнті `pushBarsToChart()` нічого не робив при `bars.length==0`, тому на екрані залишались старі свічки → виглядало як "фриз".
 	- **Зміна**: виправлено генерацію `cursorSuffix`, щоб `to_ms` додавався тільки коли `lastCursorMs` є кінцевим числом.
  		- Файл: `UI_v2/web_client/app.js`
 	- **Дотичне виправлення тестів**: оновлено фейковий провайдер OHLCV у тесті під новий параметр `to_ms`, щоб не було `TypeError`.
  		- Файл: `tests/test_ui_v2_viewer_state_server.py`
 	- **Тести/перевірки**:
  		- `pytest -q tests/test_ui_v2_viewer_state_server.py` (OK)
  		- Примітка: повний прогін `pytest -q tests` у репозиторії зараз має інші, не пов'язані з цією правкою фейли (зафіксовано під час перевірки).
 	- **Ризики/нотатки**:
  		- Поведінка replay не змінюється: коли курсор реально є числом, `to_ms` продовжує передаватись.
  		- Це саме "поломаний браузер/UI" сценарій, бо помилка в клієнтській логіці запиту.

- **Warmup/backfill + live FXCM канали (під вимогу 300–800 барів)**
 	- **Спостереження**: live-оновлення барів може бути відсутнє, а warmup/backfill команди можуть «не доходити», якщо процес слухає/публікує `fxcm_local:*`, а конектор працює на канонічних `fxcm:*`.
 	- **Зміна**:
  		- У [config/config.py](config/config.py) дефолтний prefix FXCM каналів тепер **завжди** `fxcm` (незалежно від `AI_ONE_MODE`). Для ізоляції dev/локального конектора треба явно задати `FXCM_CHANNEL_PREFIX=fxcm_local`.
  		- У [config/config.py](config/config.py) піднято `SMC_RUNTIME_PARAMS["limit"]` з 50 до 300, щоб S3 requester просив мінімум ~300 барів на старті (у барах, не «останні хвилини»).
  		- Оновлено тест [tests/test_config_fxcm_channels_by_mode.py](tests/test_config_fxcm_channels_by_mode.py) під нову політику дефолтів.
 	- **Тести/перевірки**: заплановано `pytest -q tests/test_config_fxcm_channels_by_mode.py`.
 	- **Ризики/нотатки**:
  		- Якщо у вас справді локальний FXCM конектор публікує `fxcm_local:*`, треба виставити `FXCM_CHANNEL_PREFIX=fxcm_local` (і перезапустити процеси).
  		- Підняття warmup-ліміту може збільшити стартове завантаження історії; за потреби можна підкрутити в межах 300–800.

- **Спрощення вибору env-файлу (прибрано евристику `AI_ONE_MODE`)**
 	- **Вимога**: має бути один прозорий перемикач профілю через `AI_ONE_ENV_FILE`, а всі налаштування каналів/namespace/портів живуть у `.env.local` або `.env.prod`.
 	- **Зміна**: у [app/env.py](app/env.py) `select_env_file()` тепер вибирає env-файл тільки так:
  		- 1) `AI_ONE_ENV_FILE` у process-ENV
  		- 2) `AI_ONE_ENV_FILE` у dispatcher `.env`
  		- 3) fallback лише на `.env` (без автоматичного вибору `.env.local/.env.prod` і без `*.example`)
 	- **Тести/перевірки**: додано [tests/test_env_selection.py](tests/test_env_selection.py).
 	- **Ризики/нотатки**:
  		- Якщо в середовищі немає `AI_ONE_ENV_FILE` і немає dispatcher `.env`, система використовує `.env`.
  		- Це навмисно: без явного перемикача немає прихованих "або/або" гілок.

- **Стартові логи + коректний порядок завантаження ENV**
 	- **Проблема**: у [app/main.py](app/main.py) та [app/settings.py](app/settings.py) частина модулів (наприклад `config.config`) імпортувались раніше, ніж виконувався `load_dotenv(...)`, через що значення з `.env.local/.env.prod` могли не застосовуватись.
 	- **Зміна**:
  		- У [app/main.py](app/main.py) `load_dotenv(...)` виконується до імпортів, що читають ENV; додано лог-ланцюжок `[BOOT]` (env-файл/джерело/існування, ключові env-параметри, namespace, FXCM канали, Redis host/port, попередження по HMAC).
  		- У [app/settings.py](app/settings.py) `load_dotenv(...)` тепер виконується до імпорту `config.config`.
  		- У [app/env.py](app/env.py) додано `select_env_file_with_trace()` для прозорої діагностики джерела вибору env-файлу.
 	- **Тести/перевірки**: заплановано `pytest -q tests/test_env_selection.py` та smoke `pytest -q tests/test_ui_v2_viewer_state_server.py`.
 	- **Ризики/нотатки**:
  		- Поведінка runtime стає більш детермінованою: config читає ENV вже після підхоплення `.env.*`.

- **Уточнення BOOT-логів + стабільніший live після F5**
 	- **Проблема**: у логах `AI_ONE_ENV_FILE=None` могло виглядати як "env не підхопився", хоча профіль був взятий з dispatcher `.env`.
 	- **Зміни**:
  		- У [app/env.py](app/env.py) `EnvFileSelection` тепер містить `ref` (сире значення `AI_ONE_ENV_FILE`, з якого обрано профіль).
  		- У [app/main.py](app/main.py) `[BOOT]` лог показує `AI_ONE_ENV_FILE(process)` і `AI_ONE_ENV_FILE(ref)`.
  		- У [UI_v2/web_client/app.js](UI_v2/web_client/app.js) збільшено ліміт reconnect-спроб для FXCM OHLCV/ticks WS з 3 до 12, щоб після refresh не залишатися без live при тимчасовій недоступності WS.
 	- **Тести/перевірки**: таргетні pytest-тести без змін; JS-частина покривається ручною перевіркою через refresh.

- **Інцидент інтеграції: `fxcm_local:ohlcv` мовчить при `stream.async_supervisor=true` (FXCM connector)**
 	- **Симптом**:
  		- У цьому репо конфіг показує `FXCM_CHANNEL_PREFIX=fxcm_local` і `[BOOT]` лог підтверджує `fxcm_local:*` канали,
    але live OHLCV не приходить (UI не бачить «живу свічку», інжестор не отримує повідомлень).
  		- При цьому тики/інші частини могли працювати, що робило проблему «дивною».
 	- **Root-cause (у FXCM конекторі, інше репо)**:
  		- Коли `stream.async_supervisor=true`, публікація OHLCV ішла через `AsyncStreamSupervisor`.
  		- Усередині `_publish_ohlcv_batch()` виклик `publish_ohlcv_to_redis(...)` робився **без** параметра `channel=...`.
  		- Через це публікація падала в legacy-дефолт `fxcm:ohlcv` замість конфігурованого `config.ohlcv_channel` (`fxcm_local:ohlcv`).
  		- Наслідок: AiOne_t був підписаний на `fxcm_local:ohlcv`, а конектор фактично штовхав у `fxcm:ohlcv` → «мовчання».
 	- **Фікс (у FXCM конекторі)**:
  		- Додано `ohlcv_channel` у `AsyncStreamSupervisor`.
  		- Прокинуто `channel=self._ohlcv_channel` (і `source=batch.source`) у `publish_ohlcv_to_redis(...)`.
  		- При створенні supervisor тепер передається `ohlcv_channel=config.ohlcv_channel`.
  		- Файли у конекторі: `connector.py` (AsyncStreamSupervisor.**init**, _publish_ohlcv_batch, місце створення supervisor).
  		- Окремо оновлено запис у `UPDATE.md` у репо конектора.
 	- **Чому проявлялось саме так**:
  		- Без async supervisor все працювало (публікація йшла іншим шляхом і брала правильний канал).
  		- З async supervisor з'являлась «неузгодженість каналів» тільки для OHLCV.
 	- **Як швидко діагностувати наступного разу**:
  		- У цьому репо: звірити `[BOOT] FXCM канали: ...` і логи інжестора `[FXCM_INGEST] ... channel=...`.
  		- Якщо `FXCM_CHANNEL_PREFIX=fxcm_local`, але інжестор стартує на `fxcm:ohlcv` — це майже завжди означає, що конектор публікує не туди або env не підхопився.
  		- У конекторі: перевірити, в який Redis канал реально викликається publish для OHLCV у режимі supervisor.
 	- **Шляхи виправлення (коротко)**:
  		- Правильний: передавати `channel=` у всі publish-шляхи конектора (зокрема supervisor), не покладатися на legacy-дефолти.
  		- Тимчасовий workaround: підписати AiOne_t на `fxcm:*` (не бажано, бо ламає ізоляцію профілів).

- **Cold-start/історія: автодетект внутрішніх дірок + менше втрат хвоста на рестарті**
 	- **Симптоми (з поля)**:
  		- Видимі «міні-гепи» між свічками всередині історії (internal gaps).
  		- Ефект «нова свічка позаду» / «на 1 бар позаду» — коли з’являється бар з меншим `open_time`, ніж очікуваний у хвості.
  		- Після рестарту стабільно губляться «останні хвилини» (write-behind flush lag): RAM очищається, диск не встигає отримати snapshot, Redis тримає лише last-bar.
  		- На вихідних/закритому ринку система може генерувати зайві warmup/backfill запити, які не дають користі.
 	- **Зміни (мінімальний диф)**:
		1) **S2: детект `gappy_tail`**
			- У [app/fxcm_history_state.py](app/fxcm_history_state.py) `compute_history_status()` тепер додатково аналізує tail-вікно `open_time` і рахує:
				- `gaps_count` — кількість кроків `delta(open_time)` більших за `1.5 * tf_ms`;
				- `max_gap_ms` — найбільший виявлений gap.
			- Якщо базовий стан був `ok`, але `gaps_count > 0`, стан переводиться у `gappy_tail` і встановлюється `needs_backfill=True`.
		2) **S3: реакція на `gappy_tail` + policy на `market=closed`**
			- У [app/fxcm_warmup_requester.py](app/fxcm_warmup_requester.py) requester тепер:
				- для `gappy_tail` надсилає команду (для `1m` → `fxcm_warmup`, для TF>1m → `fxcm_backfill`) з `reason=gappy_tail`;
				- додає в payload діагностику `s2.gaps_count` та `s2.max_gap_ms`;
				- при `market=closed` не надсилає команди, якщо стан не `insufficient` (тобто «на вихідних не шумимо», але cold-start warmup дозволяємо).
		3) **Shutdown: зменшення flush lag**
			- У [app/main.py](app/main.py) в `run_pipeline()` у `finally` додано `await datastore.stop_maintenance()`, щоб виконати фінальний drain write-behind черги і зменшити ризик втрати хвоста історії при рестарті.
 	- **Тести/перевірки**:
  		- Оновлено [tests/test_s2_history_state.py](tests/test_s2_history_state.py): додано тест `gappy_tail`.
  		- Оновлено [tests/test_s3_warmup_requester.py](tests/test_s3_warmup_requester.py): додано тест на publish при `gappy_tail`.
  		- Запуск: `pytest -q tests/test_s2_history_state.py tests/test_s3_warmup_requester.py` (OK).
 	- **Ризики/нотатки**:
  		- `gappy_tail` — best-effort сигнал: requester може попросити warmup/backfill, але заповнення внутрішніх дірок залежить від можливостей конектора.
  		- Поріг gap-а зараз евристичний (`1.5 * tf_ms`). Якщо FXCM/конектор дає нерівномірні open_time, може знадобитись підкрутка.

- **Cold-start/історія: детект "бар позаду" (non_monotonic_tail) — уточнення без фолс-позитивів**
 	- **Проблема**: початковий детектор рахував `delta(open_time) <= 0` як не-монотонність, тобто дублікати `open_time` теж помилково зводили стан до `non_monotonic_tail`.
  		- Це ламало очікування в S3: замість `ok`/`prefetch_history` могли з'являтися зайві warmup/backfill.
  		- Також у тестовому кейсі "бар позаду" стан маскувався як `stale_tail`, бо `last_open_time` був занадто старий (пріоритет `stale_tail` вище).
 	- **Зміна**:
  		- У [app/fxcm_history_state.py](app/fxcm_history_state.py) `non_monotonic_count` тепер збільшується лише коли `delta < 0` (реальний крок назад у часі).
  		- У тестах non_monotonic-tail підігнано `base`, щоб хвіст був свіжий і не попадав у `stale_tail`.
  		- Додано регресійний тест: дублікати `open_time` не повинні давати `non_monotonic_tail`.
 	- **Тести/перевірки**: `pytest -q tests/test_s2_history_state.py tests/test_s3_warmup_requester.py`.

---

## 2025-12-23

- **UI_v2: форензика “разовий ривок/розмазування масштабу по Y” на першому wheel**
 	- **Вимога**: нічого не міняти у коді, а спочатку зафіксувати поточний стан максимально детально.
 	- **Симптом (зі слів користувача)**:
  		- Після refresh або зміни TF перший wheel по price-axis дає разовий “ривок/розмазування” (ніби двічі застосувався scale або “підкрутило” range).
  		- Далі wheel працює більш очікувано.
 	- **Контекст реалізації (поточний стан, “як є”)**:
		1) **Built-in scaling lightweight-charts увімкнений у дефолтних опціях**
			- Файл: [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js)
			- `DEFAULT_CHART_OPTIONS.handleScale.mouseWheel = true`
			- `DEFAULT_CHART_OPTIONS.handleScale.axisPressedMouseMove.price = true`
			- `DEFAULT_CHART_OPTIONS.handleScroll.mouseWheel = true`
			- Отже бібліотека має повне право скейлити/скролити на wheel сама.
		2) **Кастомний wheel-хендлер для price-axis (перехоплення у capture)**
			- Файл: [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js)
			- Коментар у коді: “wheel по price-axis перехоплюємо у capture-фазі… інакше lightweight-charts може встигнути застосувати власний scale, а наш manualRange додасться зверху”.
			- `WHEEL_OPTIONS = { passive: false, capture: true }`.
			- Listener: `container.addEventListener("wheel", handleWheel, WHEEL_OPTIONS)`.
			- Ключове: хендлер робить `event.preventDefault()`, `stopImmediatePropagation()` (якщо доступно), потім `stopPropagation()`.
			- Гейти перед тим, як він зупиняє подію:
				- `pointerInAxis = isPointerInPriceAxis(event)`.
				- `pointerInPane = isPointerInsidePane(event)`.
				- Якщо НЕ `pointerInAxis` і НЕ (`event.shiftKey` && `pointerInPane`) → `return`.
				- Якщо `getEffectivePriceRange()` повернув `null` → `return` (важливо: у цьому випадку НЕ виконується `preventDefault/stop*`).
			- Функціональність:
				- `Shift+wheel` у pane → `applyWheelPan()` (вертикальний pan manualRange).
				- Wheel у price-axis → `applyWheelZoom()` (zoom навколо anchor-price).
		3) **Manual price range через autoscaleInfoProvider**
			- У коді є `priceScaleState = { manualRange, lastAutoRange }`.
			- `candles` і `liveCandles` мають `autoscaleInfoProvider: priceScaleAutoscaleInfoProvider`.
			- Логіка:
				- Якщо `manualRange` не активний → використовується baseImplementation(), а `lastAutoRange` оновлюється з базового autoscale.
				- Якщо `manualRange` активний → series повертає ОДНАКОВИЙ `priceRange` для всіх серій на правій шкалі (щоб не було “стеля/підлога”).
		4) **Синхронізація після зміни manualRange: requestPriceScaleSync()**
			- `applyManualRange(range)` ставить `priceScaleState.manualRange = normalized` і викликає `requestPriceScaleSync()`.
			- `requestPriceScaleSync()` робить:
				- `logicalRange = chart.timeScale().getVisibleLogicalRange()`; якщо є — повторно викликає `setVisibleLogicalRange({from,to})`.
				- інакше бере `scrollPosition()` та викликає `scrollToPosition(position, false)`.
			- Важливо: тут немає явного guard-а “logicalRange ще не готовий після init/fitContent”, окрім fallback на scrollPosition.
		5) **Додатковий wheel listener “best-effort” (не для масштабу)**
			- Є окремий `container.addEventListener("wheel", onWheel, { passive: true })`, який лише планує перерахунок DOM-лейблів POI через RAF.
			- Коментар у коді: “wheel у capture-режимі вже обробляється price-axis. Тут — best-effort.”
			- Цей listener не викликає `preventDefault` і не змінює scale/range напряму.
  - **Найімовірніший механізм “перший wheel стрибає” (гіпотеза №1, найсильніша по коду)**:
    - На першому wheel після refresh/зміни TF `getEffectivePriceRange()` інколи повертає `null` (ще немає валідного `lastAutoRange`, або `paneSize/coordinateToPrice` тимчасово не дають чисел).
    - Через ранній `return` кастомний `handleWheel` НЕ викликає `preventDefault/stop*`.
    - Built-in wheel-scale бібліотеки (бо `handleScale.mouseWheel=true`) відпрацьовує і змінює scale/range → користувач бачить разовий “ривок”.
    - На наступних wheel `lastAutoRange` вже ініціалізовано, `getEffectivePriceRange()` стає не-null, і кастомний хендлер починає перехоплювати події в capture-фазі та глушити built-in → “після першого разу норм”.
  - **Альтернативний механізм (гіпотеза №2, слабша, але можлива)**:
    - Якщо lightweight-charts підписався на `wheel` у capture на тому ж target раніше, ніж наш `addEventListener`, то його built-in може спрацювати першим.
    - У цьому випадку навіть `stopImmediatePropagation` у нашому хендлері не зупинить вже виконаний built-in.
    - Але це гірше пояснює “саме перший wheel”, якщо немає одноразової неготовності (гіпотеза №1).
  - **Примітка**: `axisPressedMouseMove.price = true` також означає, що drag по price-axis може активувати built-in логіку шкали.
    Поточний кастомний код блокує лише `handleScroll.pressedMouseMove` під час нашого vertical-pan, але не вимикає `handleScale.axisPressedMouseMove.price` глобально.
  - **Тести/перевірки**: не запускались (зміна лише документаційна, код не чіпали).
  - **Ризики/нотатки**:
    - Цей запис — “зріз стану” перед будь-якими правками.
    - Якщо будемо робити мін-фікс, треба зберегти UX: wheel у pane лишається за бібліотекою, а wheel у price-axis має бути детерміновано перехоплений без одноразових пропусків.

- **UI_v2: P0 фікс “перший wheel не проскакує в built-in scale”**
  - **Проблема**: у `setupPriceScaleInteractions()` подію wheel глушили (preventDefault/stop*) лише після `getEffectivePriceRange()`.
    Якщо `getEffectivePriceRange()` на першій взаємодії повертає `null`, wheel проходить у built-in масштабування lightweight-charts → разовий “ривок/розмаз”.
  - **Зміна (мінімальний диф, UX не розширюємо)**:
    - У [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js) wheel-хендлер тепер викликає `preventDefault()` + `stopImmediatePropagation()` + `stopPropagation()` ДО перевірки `getEffectivePriceRange()`.
    - Якщо range ще не готовий — ми просто виходимо, але built-in вже заблоковано (замість разового стрибка).
  - **Ризики/нотатки**:
    - Якщо користувач одразу після refresh крутить wheel по price-axis, а метрики ще не готові, wheel “нічого не зробить” (але це краще за неконтрольований built-in ривок).
    - Обробка wheel у pane без Shift не змінюється (там ми не перехоплюємо подію).
  - **Smoke-перевірка (2 хв)**:
    - Refresh/F5 → одразу wheel по price-axis: не має бути разового ривка/«розмазу».
    - Wheel у pane без Shift: time-zoom як раніше.
    - Shift+wheel у pane: manual vertical pan працює, сторінка не скролиться.
  - **Факт після спроби**:
    - На практиці у конкретному середовищі користувача це НЕ прибрало симптом і місцями зробило UX гіршим (wheel інколи “глушиться”, але дія не застосовується).

- **UI_v2: P0.1 фікс (стабільний hit-test price-axis + відкладений wheel на 1 кадр)**
 	- **Гіпотеза**:
  		- Після init/зміни TF `chart.paneSize()` і/або `priceScale("right").width()` можуть тимчасово бути 0.
  		- Через це `isPointerInPriceAxis()`/`isPointerInsidePane()` могли повертати `false`, і перехоплення wheel ставало недетермінованим.
  		- Додатково, коли `getEffectivePriceRange()` ще `null`, ми глушили wheel, але дія не виконувалась → “відчуття гірше”.
 	- **Зміна**:
  		- У [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js) додано fallback hit-test для price-axis на випадок нульових метрик:
   			- Використовується `PRICE_AXIS_FALLBACK_WIDTH_PX = 56` (узгоджено з CSS-резервом: `styles.css` -> `.chart-overlay-actions { padding-right: 56px; }`).
   			- `isPointerInPriceAxis()`/`isPointerInsidePane()` більше не вимагають `paneWidth/paneHeight` як обов'язкові для роботи.
  		- Якщо `getEffectivePriceRange()` ще не готовий, wheel-дія (zoom/pan) відкладається на 1 кадр через `requestAnimationFrame`, щоб перша взаємодія не “помирала”.
 	- **Очікування**:
  		- Wheel по price-axis після refresh/зміни TF має бути детермінований: без built-in “ривка” і без “глухого” колеса.

- **UI_v2: live-оновлення (тик/WS) → зменшення “стрибків” від частих перерендерів**
 	- **Спостереження (з поля)**:
  		- “Стрибки” проявляються саме коли ринок відкритий і live-свічка/ціна рухається.
  		- На закритому ринку (коли свічка не рухається) графік стабільний.
  		- При вимкненні live-volume поведінка volume стабілізується.
 	- **Root-cause (ймовірний, по коду)**:
  		- `setLiveBar()` викликається часто (tick stream throttled ~200 мс) і робив:
   			- `liveVolume.setData([])` практично на кожен тик (бо volume=0 у tick-стрімі) → зайві перерахунки/перемальовки;
   			- `updateCurrentPriceLine()` пересоздавав price-line на кожне оновлення ціни (remove + create) → потенційне “смикання” бейджа/шкали.
 	- **Зміна (мінімальний диф, UX не міняємо)**:
  		- У [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js):
   			- `currentPriceLine` тепер оновлюється через `applyOptions({price,color})`, якщо owner не змінився (замість remove+create на кожен тик).
   			- `liveVolume` тепер НЕ робить `setData([])` на кожен тик: чиститься/малюється лише при зміні видимості або при зміні бару; у межах одного бару використовує `update()`.
 	- **Очікування**:
  		- Менше “дергання” при live-оновленнях, особливо по осі/бейджу ціни та по volume.

- **UI_v2: SSOT “інваріанти/межі графіка”**
 	- Додано документ [docs/ui/ui_v2_chart_invariants_and_boundaries.md](docs/ui/ui_v2_chart_invariants_and_boundaries.md) з правилами для wheel/drag/manualRange/lastAutoRange.

- **UI_v2: “80% unit/logic” — витяг чистих функцій + тести без Node.js**
 	- Витягнуто чисту логіку в [UI_v2/web_client/chart_adapter_logic.js](UI_v2/web_client/chart_adapter_logic.js): `normalizeRange`, hit-test (price-axis/pane), `computeEffectivePriceRange`, `clamp`.
 	- У [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js) підключено ці функції (через `globalThis.ChartAdapterLogic`) без зміни UX.
 	- Додано pytest-тести, які виконують JS через `quickjs`: [tests/test_ui_v2_chart_adapter_logic.py](tests/test_ui_v2_chart_adapter_logic.py).
 	- **Тести/перевірки**: `pytest -q tests/test_ui_v2_chart_adapter_logic.py`.
 	- Додано чисті wheel-функції: `computeWheelZoomRange`, `computeWheelPanRange` + тести на напрям/зсув/anchor.

- **UI_v2: P2 фікс “перший vertical-pan/axis-drag не дає Y-стрибок при live-свічці”**
 	- **Симптом (з поля)**:
  		- Після refresh/зміни TF: wheel/scroll по часу працюють стабільно.
  		- Але перший легкий drag вгору у pane або взаємодія по правій шкалі ціни дає різкий Y-стрибок (“їжаки”), після чого поведінка стабілізується до наступного refresh/TF change.
  		- На закритому ринку (без live-руху) симптом відсутній.
 	- **Root-cause (ймовірний, по коду)**:
  		- `getEffectivePriceRange()` використовує `priceScaleState.lastAutoRange`, якщо він є.
  		- `lastAutoRange` оновлювався через `autoscaleInfoProvider` і для `candles`, і для `liveCandles`.
  		- Коли liveCandles активний (1 жива свічка, часто оновлюється), `lastAutoRange` міг “перетиратись” діапазоном саме live-серії.
  		- При першому vertical-pan `ensureManualRange(baseRange)` міг стартувати з цього “live-range”, що виглядає як різкий Y-zoom/розмаз.
 	- **Зміна**:
  		- У [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js) `lastAutoRange` тепер трекається тільки по основній серії `candles` (liveCandles не перетирає `lastAutoRange`).
  		- При reset датасету (`setBars([])`, `looksLikeNewDataset`, `clearAll`) додатково очищаємо `priceScaleState.lastAutoRange = null`, щоб не брати застарілий діапазон після TF/symbol reset.
 	- **Очікування**:
  		- Перший vertical-pan/axis взаємодія після refresh/TF change не повинна давати різкий Y-стрибок, навіть якщо live-свічка рухається.
 	- **Статус після первинної перевірки (з поля)**:
  		- Користувач підтвердив: “зараз все виглядає чудово та ніби виправили”.
  		- Примітка: потрібен час на польові тести/спостереження (різні TF, різні стани ринку, кілька refresh/перезапусків), щоб підтвердити відсутність регресій.

- **S2/S3: розслідування пропусків хвилин (дірки) + автоматичний repair**
 	- **Симптом (з поля)**: візуально помітні “розрізи” між 1m свічками, які виглядають як гепи; внаслідок цього "биті" також вищі TF (напр. 4h), бо 4h матеріалізується з 1m→5m→1h.
 	- **Root-cause (по коду, підтверджено)**:
  		- S2 `compute_history_status()` рахував `gaps_count/non_monotonic_count` лише на вікні `limit=min_history_bars` (типово 300), тому gap-и, що були глибше в історії (наприклад у видимих ~800 барах UI), могли не детектитись.
  		- S3 при `gappy_tail/non_monotonic_tail` просив у конектора лише `desired_bars` (типово 300), тож навіть коли gap був виявлений глибше, repair міг не перекрити ділянку розриву.
  		- S3 на `market=closed` пропускав команди для будь-якого стану, крім `insufficient`, що блокувало офлайн-добір “дір”, які утворились у відкритий ринок.
 	- **Зміни (мінімальний диф)**:
  		- У [app/fxcm_history_state.py](app/fxcm_history_state.py) додано `diagnostic_bars`: S2 може аналізувати ширше вікно для gaps/non-monotonic, не змінюючи поріг готовності `min_history_bars`.
  		- У [app/fxcm_warmup_requester.py](app/fxcm_warmup_requester.py):
   			- при `gappy_tail/non_monotonic_tail` збільшено `lookback_bars` до `~3x desired` (cap 1200), щоб реально перекривати видимі розриви;
   			- при `market=closed` repair дозволено для `gappy_tail/non_monotonic_tail` (а `stale_tail` як і раніше не шумимо на закритому ринку).
 	- **Тести/перевірки**:
  		- `pytest -q tests/test_s2_history_state.py` (OK)
  		- `pytest -q tests/test_s3_warmup_requester.py` (OK)
 	- **Ризики/нотатки**:
  		- S2 трохи частіше читає з UDS (до 1200 барів на пару), але cap 2000/1200 обмежує навантаження.
  		- Якість 4h напряму залежить від повноти 1m (агрегація зараз сувора: неповні bucket-и пропускаються).

- **UI_v2: стабілізація hover-tooltip на живому графіку**
 	- **Симптом (з поля)**: тултіпи нестабільні — швидко зникають або довго не з’являються; причина — live-оновлення графіка постійно «підбиває» crosshair callback.
 	- **Зміна**: у [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js)
  		- додано короткий grace на hide (250мс) для шумних подій без `time/point`;
  		- зменшено затримку показу з 1000мс до 200мс;
  		- якщо курсор не рухався (та сама точка/час) — не скидаємо show-таймер, щоб tooltip не “ніколи не з’являвся”.

- **UDS: on-demand refresh похідних TF (5m/1h/4h) при live-стрімі**
 	- **Симптом (з поля)**: 1h/4h можуть бути “діряві” саме під час стріму; після рестарту інколи виглядає краще.
 	- **Root-cause (по коду, підтверджено)**: у [data/unified_store.py](data/unified_store.py) `get_df()` матеріалізував 5m/1h/4h лише коли snapshot порожній; якщо snapshot уже існує, він повертався як є, без спроби дозбирати нові complete-бакети.
 	- **Зміна**:
  		- У [data/unified_store.py](data/unified_store.py) додано best-effort refresh похідних TF при читанні, якщо parent TF просунувся достатньо для нового complete bucket.
  		- Додано throttle (щоб часті UI-запити не викликали часту агрегацію).
  		- Матеріалізація для refresh читає лише tail джерельного TF (cap по барах), щоб обмежити навантаження.
 	- **Тести/перевірки**: `pytest -q tests/test_uds_tf_materialization.py` (OK).
 	- **Ризики/нотатки**:
  		- Це не “пом’якшує” сувору агрегацію: якщо в 1m є реальні пропуски, 1h/4h все одно можуть мати дірки, доки S3/backfill не добере хвилини.
  		- Але тепер, коли 1m дотягується, 1h/4h можуть підхопити виправлення без рестарту.

- **Інструмент: “доказовий” аудит гепів у 1m/5m**
 	- **Вимога**: почати з 1m та 5m і довести, що в хвості немає жодного гепа/дірки/незаповненого проміжку.
 	- **Зміна**: додано утиліту [tools/gap_audit.py](tools/gap_audit.py), яка читає дані через `UnifiedDataStore.get_df()` і строго перевіряє крок `open_time` (Δ має дорівнювати TF).
 	- **Тести/перевірки**:
  		- `pytest -q tests/test_gap_audit_logic.py` (OK)
 	- **Нотатки**:
  		- Рекомендований запуск під час live: `python tools/gap_audit.py --symbol xauusd --timeframes 1m 5m --limit 2000`.
  		- Гаряче виправлення: для Windows/скрипт-режиму додано підкладання кореня репо в `sys.path`, щоб `import app/...` працював.

- **UI_v2: “щільний” графік при live-гепах (gap-fill тільки у /smc-viewer/ohlcv)**
 	- **Проблема (з поля)**: гепи з'являються у live, отже видно й у UI; хочемо, щоб графік малювався без порожніх проміжків.
 	- **Зміна**: у [UI_v2/ohlcv_provider.py](UI_v2/ohlcv_provider.py) додано best-effort gap-fill у відповіді:
  		- пропущені TF-слоти заповнюються синтетичним flat-баром (OHLC=prev_close, volume=0);
  		- це робиться лише на рівні UI-віддачі, без запису в `UnifiedDataStore`.
 	- **Тести/перевірки**: `pytest -q tests/test_ui_v2_ohlcv_provider.py` (OK).
 	- **Ризики/нотатки**:
  		- Це косметичний фікс для UI; канонічну історію все одно має добирати S3/backfill.
  		- Для дуже великих розривів є ліміт на кількість синтетичних барів у відповіді.

- **FXCM ingest: live auto-backfill при гепі (kill-switch + cooldown)**
 	- **Вимога**: авто-backfill при виявленні гепа у live, з kill-switch і мінімальним навантаженням
		(важливо і для UI, і для SMC аналізу).
 	- **Зміна**: у [data/fxcm_ingestor.py](data/fxcm_ingestor.py) додано live gap guard:
  		- після успішного `put_bars()` інжестор тримає `last_ingested_open_time` per (symbol, tf);
  		- якщо наступний бар має `open_time` зі стрибком (Δ > tf_ms) → publish команду **для FXCM-конектора** у `FXCM_COMMANDS_CHANNEL` (Redis):
   			- `1m` → `fxcm_warmup`
   			- `TF>1m` → `fxcm_backfill`
  		- rate-limit per (symbol, tf) через cooldown.
 	- **Конфіг (kill-switch)**: у [config/config.py](config/config.py)
  		- `SMC_LIVE_GAP_BACKFILL_ENABLED` (default: `False`, керується конфігом)
  		- `SMC_LIVE_GAP_BACKFILL_COOLDOWN_SEC` (default: `120`)
  		- `SMC_LIVE_GAP_BACKFILL_LOOKBACK_BARS` (default: `800`, внутрішній cap `1200`)
  		- `SMC_LIVE_GAP_BACKFILL_MAX_GAP_MINUTES` (default: `180`) — щоб не спамити конектор
			на вихідних/закритому ринку.
 	- **Тести/перевірки**: `pytest -q tests/test_ingestor.py` (OK).
 	- **Ризики/нотатки**:
  		- Статус: тему зафіксовано та тимчасово закрито — потрібен час на польову перевірку в live
			(кілька сесій/TF/перезапусків).
  		- Великі розриви (наприклад вихідні) спеціально не тригерять live-команди (керується `MAX_GAP_MINUTES`);
			їх має добирати S3 requester (`gappy_tail`) без live-спаму.

- **Docs/Log: уточнення межі “не напряму FXCM”**
 	- **Контекст**: у цьому репо прямі команди в FXCM заборонені; взаємодія йде лише через FXCM-конектор.
 	- **Зміна**: уточнено формулювання, що “команди” — це публікація payload у Redis канал конектора (`FXCM_COMMANDS_CHANNEL`), а не прямий виклик FXCM.
 	- **Де**: [Log.md](Log.md), [config/config.py](config/config.py), [docs/architecture/migration_log.md](docs/architecture/migration_log.md).

- **Shutdown: менше шуму у логах при зупинці пайплайна**
 	- **Проблема (з поля)**: під час штатного завершення (Ctrl+C / Cancel) з'являвся traceback типу
		`redis.exceptions.ConnectionError: Connection closed by server` у
		[UI_v2/viewer_state_ws_server.py](UI_v2/viewer_state_ws_server.py).
 	- **Зміна**:
  		- У [UI_v2/viewer_state_ws_server.py](UI_v2/viewer_state_ws_server.py) додано `stopping`-гейт:
			під час shutdown pubsub помилки більше не логуються як warning+traceback.
  		- У [app/main.py](app/main.py) та воркерах [data/fxcm_ingestor.py](data/fxcm_ingestor.py),
			[data/fxcm_price_stream.py](data/fxcm_price_stream.py),
			[data/fxcm_status_listener.py](data/fxcm_status_listener.py) CancelledError-логи переведено на DEBUG.
 	- **Тести/перевірки**:
  		- `pytest -q tests/test_ui_v2_viewer_state_server.py` (OK)
  		- `pytest -q tests/test_ingestor.py tests/test_s3_warmup_requester.py` (OK)

- **UI_v2: Playwright E2E smoke (офлайн, без CDN) + тестовий lifecycle для HTTP сервера**
 	- **Мета**: “20% E2E” — зафіксувати регресії UI-фіксів (перший wheel після refresh/TF change без ривка; tooltip стабільний).
 	- **Зміни (мінімальний набір)**:
  		- Додано офлайн harness сторінку: [UI_v2/web_client/e2e_smoke.html](UI_v2/web_client/e2e_smoke.html).
  		- Додано локальний stub lightweight-charts для тестів: [UI_v2/web_client/lightweight_charts_stub.js](UI_v2/web_client/lightweight_charts_stub.js).
  		- Додано драйвер `window.__e2e__`: [UI_v2/web_client/e2e_smoke_driver.js](UI_v2/web_client/e2e_smoke_driver.js).
  		- Додано E2E smoke тести Playwright: [tests/e2e/test_ui_v2_playwright_smoke.py](tests/e2e/test_ui_v2_playwright_smoke.py);
		  маркер `e2e` зареєстровано у [pytest.ini](pytest.ini).
  		- Для програмного старт/стоп у тестах розширено HTTP сервер:
		  [UI_v2/viewer_state_server.py](UI_v2/viewer_state_server.py) (`start()`/`stop()`/`get_listen_url()` з `port=0`).
 	- **Тести/перевірки**:
  		- `pytest -q -m e2e` (OK)
  		- `pytest -q tests/e2e/test_ui_v2_playwright_smoke.py` (OK)
 	- **Ризики/нотатки**:
  		- Це smoke-рівень: ловить грубі регресії взаємодій, але не замінює польові live-перевірки.
  		- Таймінги tooltip залежать від реальних `SHOW_DELAY_MS/HIDE_GRACE_MS`, тому у тестах є контрольовані wait-и.

- **Config: виправлення дефолтів kill-switch та ENV false-values (без зміни контрактів)**
 	- **Проблема**:
  		- `_FALSE_ENV_VALUES` у [config/config.py](config/config.py) був некоректним (`{"1"}`), що могло зламати `_env_bool`-гейти.
  		- `SMC_LIVE_GAP_BACKFILL_ENABLED` був увімкнений попри коментар про kill-switch.
 	- **Зміна**: у [config/config.py](config/config.py)
  		- `_FALSE_ENV_VALUES = {"0", "false", "no", "off"}`
  		- `SMC_LIVE_GAP_BACKFILL_ENABLED = False` (default off; вмикати лише явним рішенням)
 	- **Тести/перевірки**: `pytest -q tests/test_ingestor.py` (OK) + E2E smoke (OK)

---

## 2025-12-24

- **Фіксація фактів/артефактів: QA-прогін `tools/smc_journal_report.py` для XAUUSD (5m) з пріоритетом POI → OTE → pools**
  - **Вимога**: зафіксувати “доказово” (цифри + приклади для replay), що саме на 5m створює шум/недовіру у візуалізації, без передчасного “вимикання”.
  - **Що запущено (фактична команда)**:

    ```powershell
    ; & "C:\Aione_projects\smc_v1\.venv\Scripts\python.exe" tools\smc_journal_report.py --dir reports/smc_journal/2025-12-19 --frames-dir reports/smc_journal/frames/2025-12-19 --symbol XAUUSD --run-dir reports/smc_journal_p0_run5 --ohlcv-path datastore/xauusd_bars_5m_snapshot.jsonl
    ```

    - Статус: exit code = 0 (репорт/CSV згенеровано).
  - **Де артефакти (SSOT для цього прогону)**:
    - Звіт: `reports/smc_journal_p0_run5/report_XAUUSD.md`
    - TODO для ручного replay: `reports/smc_journal_p0_run5/audit_todo.md`
    - CSV-артефакти (ключові для цього аналізу):
      - `touch_rate.csv`, `created_per_hour.csv`
      - Case B: `case_b_removed_then_late_touch_examples.csv`
      - Case C: `case_c_short_lifetime_examples.csv`, `short_lifetime_share_by_type.csv`, `flicker_short_lived_by_type.csv`
      - Case D: `case_d_widest_zone_examples.csv`, `wide_zone_rate.csv`, `span_atr_vs_outcomes.csv`
      - Case E: `zone_overlap_examples.csv`, `zone_overlap_matrix_active.csv`, `merge_rate.csv`
      - Case F/H (offline): `missed_touch_rate_offline.csv`, `touch_outcomes_after_touch_offline.csv`
  - **Ключові метрики (з `report_XAUUSD.md`)**:
    - `touch_rate`:
      - `pool`: created=10298, touched=978 (touch_rate=9.5%), removed=10292, touched_late=7313 (late_touch_rate_vs_removed=71.1%).
      - `zone`: created=345, touched=88 (touch_rate=25.5%), removed=325, touched_late=207 (late_touch_rate_vs_removed=63.7%).
      - `magnet`: created=208, touched=160 (touch_rate=76.9%), removed=208, touched_late=208 (100%).
    - `wide_zone_rate(span_atr)`: zones_with_atr=345; span_atr>=3.0 → 18 (5.2%); span_atr_avg=0.926.
  - **POI / Zones (пріоритет №1): що саме дає “засмічення” на 5m**
    - **Case D: надширокі зони (span_atr)**
      - Факт: у прикладах є екстремальні ORDER_BLOCK PRIMARY LONG з `span_atr` до 7.393.
      - Конкретний приклад (top): `ob_xauusd_5m_157_162` (dt_created_utc=2025-12-19T04:49:00Z): price_min=4327.800, price_max=4342.940, atr_last=2.0479, span_atr=7.393.
      - Висновок для UI: навіть невелика частка таких зон (5.2% з span_atr>=3.0) може “розмазувати” картину (перекриття по ціні + конкуренція за top‑K).
    - **Case E: перекриття/дублі зон (IoU overlap)**
      - Факт (приклад стану): 2025-12-19T05:09:00Z — n_active=50, total_pairs=1225, pairs_iou_ge_0.4=67, max_iou=0.9697.
      - Max-overlap пара: `fvg_5m_1765957500000000000_78` vs `fvg_5m_1766069700000000000_440` (max_iou=0.9697).
      - Додатковий сигнал якості: `missing_bounds=12` у `zone_overlap_examples.csv`.
      - Висновок для UI: високий IoU (≈0.97) між активними FVG означає сильний ризик “дублів” та візуального шуму без merge/кластеризації.
  - **Pools (пріоритет №3): що саме підриває довіру/дає флікер**
    - **Case B: “видалили → пізно торкнуло” (late touch)**
      - Факт: late_touch_rate_vs_removed=71.1% (7313 late touches при 10292 removed).
      - Приклади з великим `bars_to_touch`: типово 250–272 бари (тобто 20–23 години на 5m).
      - Конкретний приклад: `pool:WICK_CLUSTER:COUNTERTREND:4349.180000:2025-12-17T16:10:00+00:00:2025-12-18T16:30:00+00:00` має `bars_to_touch=272`.
    - **Case C: коротке життя (short_lifetime<=1)**
      - Факт: `case_c_short_lifetime_examples.csv` містить багато прикладів pools з `lifetime_bars=1` і `reason=invalidated_rule` (close/preview).
      - Висновок для UI: масові one‑bar pools дають флікер (виникли/зникли) і засмічують шари.
  - **OTE (пріоритет №2): статус по цьому прогону**
    - Факт: за 2025‑12‑19 (XAUUSD 5m) немає “маси” OTE-прикладів у `audit_todo.md` як окремого кейсу/секції;
      для доказового аналізу OTE потрібен інший відрізок, де OTE реально генерується.
  - **Offline аудит журналу (touch) на цьому датасеті**
    - `missed_touch_rate(offline)`:
      - `preview`: zone_instances=268; should_touch_eps=66; journal_touched=72; missed_touch_fn=0 (0.0% FN); `journal_touch_but_no_ohlcv_touch_fp=6`.
      - `close`: zone_instances=16; should_touch_eps=1; journal_touched=1; missed_touch_fn=0; fp=0.
    - `case_F_missed_touch_examples(offline)` у звіті: “нема FN прикладів за поточними правилами offline-аудиту” (FN=0).
  - **Додатково: запуск `tools/gap_audit.py` (контекст для гепів, без змін коду)**

    ```powershell
    "C:\Aione_projects\smc_v1\.venv\Scripts\python.exe" tools\gap_audit.py --symbol xauusd --timeframes 1m 5m --limit 2000
    ```

    - Статус: exit code = 0 (вивід у консоль; артефакти не зберігались у файл у цьому прогоні).
  - **Ризики/нотатки**:
    - Це “заморозка фактів” по конкретному прогону (2025‑12‑19, TF=5m). Узагальнювати на весь ринок/періоди без повторних прогонів не можна.
    - Для OTE: потрібна інша дата/відрізок (або інший символ/режим), де OTE справді присутній, і повторення цього ж протоколу (звіт + audit_todo + CSV).

## 2025-12-24 (UI_v2: time-scale під час паузи ринку)

- Контекст: під час паузи ринку (ніч/вихідні/свята) конектор віддає `market=CLOSED` і свічки не приходять;
  UI раніше міг «тягнути» time-scale до wall-clock через live-оновлення з часом далеко попереду останньої відомої
  свічки, що створювало порожній простір праворуч і «дірку» до першої нової свічки.
- Зміна: додано guardrail у [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js) —
  `setLiveBar()` відхиляє/очищає live-бар, якщо його `time` занадто далеко попереду `lastBar.time`
  (порог: `barTimeSpanSeconds * 2`), щоб шкала часу залишалась прив’язаною до останнього бару
  (поведінка в стилі TradingView).
- Тест: додано smoke E2E у [tests/e2e/test_ui_v2_playwright_smoke.py](tests/e2e/test_ui_v2_playwright_smoke.py) — симуляція «future» live-тіка не має змінювати `chartTimeRangeMax`, а `lastLiveBarTime` має очищатися.

## 2025-12-24 (UI_v2: хардени проти whitespace/дір/мерехтіння)

- Причини, які реально дають “whitespace data points”/дірки/мерехтіння в lightweight-charts:
 	- `time` інколи приходить у ms/us замість sec → бар стрибає в “далеке майбутнє”.
 	- для історії/last_bar береться close/end time, а не open time бакету → UI думає “новий бар” замість `update()`.
 	- tick-стрім без timestamp фабрикує час через wall-clock → псевдо-бари та розтягнення шкали часу.
- Зміни (мінімальний, акуратний підхід без глобальних перехоплень серій):
 	- [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js): `normalizeBar()` тепер приймає sec/ms/us (евристика), бере `time` з кількох полів і відкидає NaN OHLC, щоб LWC не отримував “биті” значення.
 	- [UI_v2/web_client/app.js](UI_v2/web_client/app.js):
  		- `safeUnixSeconds()` та `normalizeTickTimestampToSeconds()` підтримують sec/ms/us.
  		- `normalizeOhlcvBar()` пріоритезує open/start time (open time бакету) замість end/close.
  		- `handleTickWsPayload()` більше не будує бар з `Date.now()` якщо у payload немає timestamp (цінові поля UI оновлюються як і раніше).
- Тест: [tests/e2e/test_ui_v2_playwright_smoke.py](tests/e2e/test_ui_v2_playwright_smoke.py) — додано smoke на ms-vs-s: live-бар з timestamp у мс не має створювати “бар у майбутньому” і не має міняти `chartTimeRangeMax`.

## 2025-12-24 (UI_v2: оверлеї можуть не повертатися після clearAll)

- Контекст: інколи трейдер бачить “то все є, то взагалі нічого немає” по оверлеях (pools/zones), хоча очікується стабільна наявність рівнів.
- Root-cause (по коду): при тимчасовому фейлі `/smc-viewer/ohlcv` фронтенд викликає `chart.clearAll()` (чистить бари/оверлеї).

- Наслідок: `overlaySeqBySymbol` зберігає попередній `seqKey`; якщо наступний `viewer_state` приходить з тим самим `seqKey`, `updateChartFromViewerState()` скіпає оновлення (seq-gate), тому оверлеї можуть не відмалюватися назад.

- Зміна: у [UI_v2/web_client/app.js](UI_v2/web_client/app.js) додано `resetOverlaySeqCache()` і виклик після `chart.clearAll()`, щоб після очищення графіка наступний `viewer_state` гарантовано перерендерив оверлеї навіть при незмінному `seqKey`.
- Тести/перевірки: таргетних автотестів саме на цей сценарій поки немає (існуючі Playwright smoke покривають chart_adapter, а не seq-gate у app.js).
