# Cold-start pipeline для SMC core

Документ фіксує поточний стан конфігурації, шарів зберігання й UI-пайплайна та описує план модульного cold-start прогону перед активацією live-стріму. Усі посилання актуальні для `main` гілки станом на листопад 2025.

## Ціль cold-start

Cold-start = History QA, а не просто наявність барів. Мета cold-start – не лише завантажити бари, а й автоматично виконати SMC-аналіз історії та зберегти результат як історичний контекст для UI і подальших рішень. Система вважається готовою лише тоді, коли:

1. `ensure_min_history` підтверджує достатню глибину історії в `UnifiedDataStore`.
2. Історія проганяється через `SmcCoreEngine` у режимі History QA, а підказки (`plain_smc_hint`) кешуються.
3. UI отримує доступ до цього кешу (`smc_history_{symbol}_{tf}.jsonl`) і може будувати структуру, зони та ліквідність без очікування live-даних.

Live-потік розглядаємо як продовження історичного QA: він лише добудовує хвіст існуючого контексту, не створюючи окремих паралельних станів.

## 1. Поточні джерела конфігурації

### 1.1 ENV / `app/settings.py`

- Redis: `Settings.redis_host|redis_port` (за замовчуванням `localhost:6379`).
- FXCM конектор: `Settings.fxcm_*` (username/password/connection, `fxcm_heartbeat_channel`, `fxcm_market_status_channel`, HMAC секрет/алгоритм/прапорець).
- Дані Stage1: `Settings.data_source` зафіксовано як `"fxcm"`. Інші джерела не підтримуються.
- Системні сервіси: `admin_enabled`, логування (`log_level`, `log_to_file`).
- `load_datastore_cfg()` читає `config/datastore.yaml` і задає профіль RAM/дискового кешу (див. нижче). Файл `data/dl_config.py` більше не існує.

### 1.2 `config/config.py`

- Namespace/шляхи: `NAMESPACE = "ai_one"`, `DATASTORE_BASE_DIR = <repo>/datastore` (файли `*_bars_<tf>_snapshot.jsonl`).
- Redis канали та ключі: `REDIS_CHANNEL_ASSET_STATE`, `REDIS_SNAPSHOT_KEY`, `ADMIN_COMMANDS_CHANNEL`, `STATS_CORE_KEY`, `STATS_HEALTH_KEY`, TTL `UI_SNAPSHOT_TTL_SEC=180`.
- FXCM параметри: `FXCM_FAST_SYMBOLS = ["xauusd"]`, `FXCM_STALE_LAG_SECONDS = 120`.
- Stage1 монітор: `STAGE1_MONITOR_PARAMS` (vol_z, RSI, ATR-low/high gates, `min_atr_percent`, `dynamic_rsi_multiplier`, `min_reasons_for_alert`).
- Screening: `SCREENING_LOOKBACK = 240`, `SCREENING_BATCH_SIZE = 12`, `DEFAULT_TIMEFRAME = "1m"`, `DEFAULT_LOOKBACK = 3`, `TRADE_REFRESH_INTERVAL = 3`.
- SMC: `SMC_PIPELINE_ENABLED = True`, `SMC_PIPELINE_CFG = {tf_primary="1m", tfs_extra=("5m","15m","1h"), limit=300, max_concurrency=4, log_latency=True}`.
- History QA конфіги: `HISTORY_QA_SYMBOLS` (whitelist для QA, якщо потрібно обмежити обсяг) і `HISTORY_QA_WARMUP_BARS` (кількість барів, що використовуються лише як контекст перед записом снапшотів; дефолт 49 → 300 − 49 = 251 снапшотів).
- Warmup: `DATASTORE_WARMUP_ENABLED=True`, `DATASTORE_WARMUP_INTERVALS={"1m":1800,"5m":1500}`.
- TTL карти: `INTERVAL_TTL_MAP` (1m→90 c, 5m→300 c, 1h→3600 c тощо) + внутрішні TTL RAM-профілю (`StoreProfile.hot_ttl_sec=6h`, `warm_ttl_sec=24h`).

### 1.3 Джерела правди cold-start

- **Redis FSM (`ai_one:stage1:cold_start_status`)** — головне джерело, яке використовує UI та моніторинг. Саме цей ключ зберігає `phase`, `history`, `qa` та TTL keepalive.
- **`tmp/cold_start_report.json`** — допоміжний артефакт для скриптів на кшталт `tools.cold_start_runner`. Він оновлюється автоматично після успішного QA, але в разі розбіжностей пріоритет завжди за Redis FSM.

## 2. Шар зберігання (`data/unified_store.py`)

- RAM (`RamLayer`): LRU + TTL, пріоритети (`Priority.ALERT/NORMAL/COLD`), оцінка пам'яті. Основні методи: `get`, `put`, `set_priority`, `sweep`.
- Redis (`RedisAdapter`): `jset/jget` пишуть JSON під ключами `ai_one:candles:{symbol}:{interval}` з TTL із `cfg.intervals_ttl`. Legacy blob API (`fetch_from_cache`, `store_in_cache`) із namespace `ai_one:blob:*` використовується лише сумісністю.
- Диск (`StorageAdapter`): файл `datastore/{symbol}_bars_{interval}_snapshot.jsonl` (або parquet, якщо `_HAS_PARQUET=True`). `save_bars` пише через тимчасовий файл, `load_bars` читає jsonl/legacy json, `_postfix_df` обрізає за `bars_needed`.
- UnifiedDataStore API: `get_df`, `get_last`, `put_bars`, `warmup`, `enforce_tail_limit`, `set_fast_symbols/get_fast_symbols`, `start_maintenance` (sweep + write-behind), `_drain_flush_queue` адаптує batch size до backlog.
- Інтервали TTL для Redis залежать від `StoreConfig.intervals_ttl` (default 6h для 1m). `write_behind=True` → RAM і Redis оновлюються синхронно, диск — асинхронно через `_flush_q`.

## 3. Ланцюжок SMC історія → UI

- `app/main.py`:
   1. `bootstrap()` → `UnifiedDataStore.start_maintenance()` → `_warmup_datastore_from_snapshots()`.
   2. `ensure_min_history()` (фаза `initial_load`) гарантує прогрів `UnifiedDataStore` до мінімальної глибини по кожному `symbol/tf` без очікування live-стріму.
   3. `HistoryQaRunner` (фаза `qa_history`) читає історію зі стора, викликає `smc_core.input_adapter.build_smc_input_from_store` та `SmcCoreEngine.process_snapshot` по всій траєкторії й формує кеш `smc_history_{symbol}_{tf}.jsonl`. Перші `HISTORY_QA_WARMUP_BARS` барів використовуються лише як контекст, тож у QA summary завжди видно `warmup_bars` і `bars_processed = min_rows_required − warmup_bars`.
   4. Лише після переходу FSM у `ready` підіймаються live-стріми (`run_fxcm_ingestor()`, `run_fxcm_status_listener()`) і активуються Stage1-компоненти (`AssetMonitorStage1`, `AssetStateManager`). Stage1 використовується виключно для live-оновлень і не бере участі в історичному QA.
- `app/screening_producer.py` у live-режимі:
  - `process_asset_batch()` бере `store.get_df(symbol, timeframe, limit=lookback)`, підставляє `FxcmFeedState`, викликає `AssetMonitorStage1.check_anomalies`, додає SMC hint через `smc_core.input_adapter.build_smc_input_from_store` + `SmcCoreEngine.process_snapshot`, зберігає в `AssetStateManager`.
  - `publish_full_state()` (UI module) серіалізує `state_manager.get_all_assets()`, додає форматовані рядки/SMC-блоки та телеметрію FXCM, після чого публікує snapshot у `ai_one:ui:asset_state` і `ai_one:ui:snapshot` (TTL 180 c).
- UI споживач (`UI/ui_consumer.py`) читає два основні джерела: `meta.fxcm` (телеметрія) та `smc_history_*` (історичний контекст BOS/CHOCH/MBOS, свіпів, пулів і імбалансів). Стандартні поля (`symbol`, `stats`, `smc_hint`, `fxcm_state`, `price_str`, `volume_str`) тепер базуються на підготовленому кеші, тож UI може одразу відмалювати історію після `ready`.

### UI / Viewer

- Extended Viewer першочергово читає `meta.fxcm` для телеметрії feed'а та окремий `smc_history`-кеш (локальний файл або API) для візуалізації структури, зон і ліквідності.
- Live `smc_hint` лише добудовує останні н декілька барів поверх історичного кешу; він не намагається самостійно обчислити всю історію.
- Будь-який UI/аналітичний модуль повинен вважати історичний контекст першокласним артефактом: без `smc_history_*` live-дані не відображаються.

## 4. Контракт із FX Connector

- Канали: `fxcm:ohlcv` (OHLCV списки), `fxcm:heartbeat` (process state, `last_bar_close_ms`), `fxcm:market_status` (open/closed, `next_open_utc`).
- `_process_payload()` в `fxcm_ingestor.py` перевіряє HMAC (`FXCM_HMAC_SECRET/ALGO/REQUIRED`), нормалізує `symbol/tf` у lower-case, перетворює бари у DataFrame, викликає `store.put_bars`.
- `FxcmFeedState` (у `fxcm_status_listener.py`) зберігає `market_state`, `process_state`, `next_open_utc`, `lag_seconds`, `last_bar_close_ms`. Значення доступні Stage1/UI через `get_fxcm_feed_state()`.

## 5. Поточний dataflow / кеші ("стрілка" = метод/формат)

1. **FX Connector → Redis**: `publish_ohlcv_to_redis()` пише JSON `{symbol,tf,bars, sig?}` у `fxcm:ohlcv`; `publish_heartbeat` → `fxcm:heartbeat`; `publish_market_status` → `fxcm:market_status`.
2. **Redis → fxcm_ingestor**: `run_fxcm_ingestor()` (`redis.pubsub.listen()`) → `_process_payload()` → `UnifiedDataStore.put_bars(symbol, interval, DataFrame)`.
3. **UnifiedDataStore → кеш**:
   - RAM: `RamLayer.put` тримає `DataFrame` (full history до TTL).
   - Redis: `RedisAdapter.jset("candles", symbol, interval, last_bar)` з TTL `intervals_ttl`.
   - Диск: `StorageAdapter.save_bars()` → `datastore/{symbol}_bars_{interval}_snapshot.jsonl` (write-behind).
4. **History QA (cold-start)**: `HistoryQaRunner` бере `store.get_df` по всій історії (1m + додаткові TF), викликає `smc_core.input_adapter.build_smc_input_from_store` і `SmcCoreEngine.process_snapshot`, серіалізує `plain_smc_hint` у `smc_history_{symbol}_{tf}.jsonl`.
5. **Live Stage1**: після `cold_start_status.state == "ready"` `screening_producer.process_asset_batch()` читає останній історичний блок (`store.get_df(symbol, "1m", limit=SCREENING_LOOKBACK)`), використовує `AssetMonitorStage1`, оновлює `AssetStateManager` й інжектить live `smc_hint` поверх історичного кешу.
6. **UI**: `publish_full_state()` підхоплює `smc_history_*` + live `smc_hint`, публікує Redis `ai_one:ui:snapshot` (JSON) та Pub/Sub `ai_one:ui:asset_state` (payload з `meta.ts`, counters, assets[]).

## 6. Фізичне зберігання та індикатори свіжості

- Файлова історія: `datastore/*_bars_<tf>_snapshot.jsonl` (наприклад, `datastore/xauusd_bars_1m_snapshot.jsonl`). Формат — JSON Lines, зберігає останній snapshot, оновлюється write-behind або ручним warmup.
- Redis snapshot: `ai_one:ui:snapshot` (повний стан для UI cold-start, TTL 180 c).
- Redis live ключ: `ai_one:candles:{symbol}:{interval}` — останній бар, TTL ≈ `intervals_ttl[interval]`.
- RAM TTL: `hot_ttl_sec=6h` для 1m/5m, `warm_ttl_sec=24h` для TF >=15m (визначається `RamLayer._ttl_for`).
- Freshness метадані: `FxcmFeedState.lag_seconds` (heartbeat), `last_bar_close_ms`, `ensure_min_history` формує репорт символів із недостатньою глибиною, UI `K_STATS.timestamp`/`stats.price_ts` виводять час останнього бару.
- Механізм дозавантаження: наразі лише `store.warmup(symbols, interval, bars)` (з диску) + додаткові ручні перевірки; `ensure_min_history` слід розширити, щоб не блокуватися на live-стрімі. Часткового backfill із FXCM API в основному репо немає.

## 7. Поточні вузькі місця cold-start

1. **Повне читання snapshotів**: `_warmup_datastore_from_snapshots` читає всі файли інтервалу, навіть якщо потрібно лише `SCREENING_LOOKBACK` барів (обрізання робиться після завантаження).
2. **Відсутність часткового дозавантаження**: якщо JSONL старий >24h, система все ще покладається на live-стрім (бракує автоматизації в `ensure_min_history`), немає механізму заповнити прогалину через історичний fetch.
3. **Блокуючий IO у warmup**: читання JSONL і `store.warmup` виконується в одному event loop без throttling → cold-start затягується при великій кількості символів.
4. **Дублікати**: `store.put_bars` покладається на `open_time` для dedup, але якщо snapshot включає неповні бари, Stage1 отримує короткий "зубчастий" масив до приходу свіжих даних.
5. **Відсутність QA-прогону**: після warmup не запускається перевірка BOS/CHOCH чи UI-готовності; перший live-пакет одразу формує сигнал.
6. **Немає сигналізації статусу cold-start**: UI не знає, чи дані historical чи live (окрім загального `FxcmFeedState`).

## 8. Проєкт cold-start pipeline

Мета — мати явні етапи: `cache check → selective backfill → QA → snapshot publish → live takeover`.

| Етап | Опис | Ключові артефакти |
| --- | --- | --- |
| 0. Ініціалізація | Збір метаданих: список символів (`FXCM_FAST_SYMBOLS`), наявні snapshot-файли, Redis TTL. | `store.metrics_snapshot()`, `datastore/*.jsonl`, `FxcmFeedState`. |
| 1. Перевірка кешу | Для кожного `symbol/tf` читаємо `datastore` метадані (модифікований `StorageAdapter` може повертати `mtime`, `rows`) + Redis `candles` TTL. | `data.unified_store.UnifiedDataStore.build_cold_start_report()` → `ColdStartCacheEntry` (rows_on_disk, rows_in_ram, last_open_time, age_seconds, TTL). |
| 2. Рішення «теплий/холодний» | Якщо snapshot <12 год і містить ≥ `SCREENING_LOOKBACK` → позначаємо як warm-start. Інакше додаємо в чергу дозавантаження. | `needs_backfill = True/False`. |
| 3. (Не)довантаження історії | Для `needs_backfill` викликаємо `store.get_df`/`store.put_bars` з окремого джерела: або

   1. Попросити FX Connector віддати bulk (через новий Redis RPC `fxcm:warmup_request`).
   2. Тимчасово прочитати JSONL кеш конектора (шлях приходить із `fxcm_feed_state`).
   Після дозавантаження запускаємо `store.enforce_tail_limit(limit=N часових барів).` | Новий модуль `cold_start_backfill.py`. |
| 4. QA-прогін | Запускаємо History QA лише через `smc_core`:

- Для кожного symbol `HistoryQaRunner` зчитує прогріту історію з `UnifiedDataStore`.
- `smc_core.input_adapter.build_smc_input_from_store` + `SmcCoreEngine.process_snapshot` формують `plain_smc_hint`, BOS/CHOCH, liquidity pools.
   | Async task у `app/main.py` (або тимчасово `python -m tools.cold_start_runner` до появи сервісу). |
| 5. Збереження снапшоту | Після QA зберігаємо:
- `ai_one:ui:snapshot` (оновлено) + додаткове поле `cold_start_status = ready`.
- JSON-репорт (наприклад, `datastore/cold_start_report_<ts>.json`). | UI payload, artefact для аудиту. |
| 6. Перехід у live | Ставимо `FxcmFeedState.process_state = "stream"`, `Stage1` переключає `state_manager` у `visible=True`, `screening_producer` починає слухати live бари (запускаємо task лише після `ready`). | Новий статус `INITIAL_LOAD` → `READY`. |

### 8.1 Cold-start FSM (три фази + телеметрія)

| Стан | Що відбувається | Критерій переходу | Артефакти |
| --- | --- | --- | --- |
| `initializing` | Запускається сервіс, читаються конфіги, готується список символів/TF. | `bootstrap()` завершив sync ініціалізацію. | `meta.cold_start.state=initializing`.
| `initial_load` | `ensure_min_history` прогріває `UnifiedDataStore`, збирає репорт історії, тригерить backfill. | Всі символи мають ≥ мінімальної глибини. | `history_report.json`, Redis `ai_one:cold_start_report`.
| `qa_history` | `HistoryQaRunner` проходить історію через `SmcCoreEngine`, будує `smc_history_*`, валідуює plain hints. | QA завершився без критичних помилок. | `smc_history_{symbol}_{tf}.jsonl`, `qa_summary.json`.
| `ready` | UI може довіряти історичному контексту, live-стрім добудовує хвіст. | Live компоненти активовані, `HistoryQaRunner` позначив успіх. | `meta.cold_start.ready_ts`, Redis `ai_one:ui:snapshot`.
| `error` / `degraded` | `ensure_min_history` або History QA впали/не довантажилися. | Таймаут, виключення або неповні артефакти. | `meta.cold_start.error`, alarms.

## 9. History QA Runner

- **Вхідні параметри**: `symbol`, `tf_primary`, `tfs_extra`, `history_limit`, посилання на `UnifiedDataStore`, `SmcCoreInputAdapter` та `SmcCoreEngine`.
- **Контракт**: пройти всю історію, яку гарантував `ensure_min_history`, викликати `SmcCoreEngine.process_snapshot` по кожному кроку й серіалізувати `plain_smc_hint` у файловий/Redis кеш (`smc_history_{symbol}_{tf}.jsonl` або інший storage). Файл є джерелом правди для всієї історичної візуалізації та подальших пояснень «чому ми тут».
- **Режим виконання**: автоматичний запуск під час cold-start (`qa_history`), без ручних CLI чи UI кроків. Завершення раннера — обов'язкова умова для `cold_start_status.state="ready"`.
- **Стратегія v1**: завжди перераховуємо всю історію, щоб гарантувати чистий кеш. **Стратегія v2** (roadmap): інкрементально добудовуємо хвіст, використовуючи ті самі артефакти, але з delta-обчисленнями.
- **Валідація**: раннер пише короткий QA summary (кількість BOS/CHOCH, liquidity pools, наявність `smc_hint`). Помилки або часткові кеші автоматично переводять FSM у `error/degraded`.

## 10. Механіка інкрементального донаповнення

1. **Інвентаризація**: при рестарті збираємо `CacheReport` (timestamp останнього бару в RAM/JSONL/Redis). Якщо `now - last_bar` ≤ 12–24 год → warm start (тільки QA та snapshot). Якщо > threshold → потрібно дозавантаження.
2. **Визначення межі**: `missing_start = last_bar_on_disk`, `target_end = now - 1 interval`. Обчислюємо кількість барів (`missing_minutes`).
3. **Запит даних**:
   - Якщо зовнішній конектор підтримує RPC, шлемо запит "дай N барів від T".
   - Інакше читаємо локальний cache (`FXCM connector cache dir` — див. readme) або резервний JSONL архів (наприклад, `datastore/XAUUSD/week_2025-11-24/`).
4. **Merge**: перетворюємо в DataFrame, викликаємо `store.put_bars(symbol, interval, df_missing)` (розіб'є duplicates через `_merge_bars`).
5. **QA delta**: History QA проганяє лише доданий відрізок (`df_missing.tail(k)`) через `SmcCoreEngine`, результати порівнюються зі старим `smc_history_*`. Записуємо diff у `cold_start_report`.
6. **Trimming**: викликаємо `store.enforce_tail_limit(symbol, interval, limit=N)` (де `N` = години * 60) щоб уникнути безконтрольного росту RAM/JSONL.

## 11. Інтеграція з live-моніторингом

| Елемент | Статус | Коментар |
| --- | --- | --- |
| Redis-ключ `ai_one:stage1:cold_start_status` зі станами `initializing`, `initial_load`, `ready`, `error` + вбудовування в UI `meta.cold_start` | ✅ Реалізовано | `app.main` оновлює ключ через `build_status_payload`, `UI.publish_full_state` читає його та передає в UI payload. |
| Додаткові статуси `backfilling`, `qa` | ⚠️ TODO | Задумано для повного відображення бекфілу/QA; наразі код не генерує ці значення. |
| Гейт live-стріму за `cold_start_status` (пропускаємо live update до `ready`) | ⚠️ TODO | Stage1 поки обробляє live бари одразу після запуску; потрібно додати перевірку стану й прапор `state_manager.visible`. |
| Розширення `FxcmFeedState.process_state` значеннями `cold_start_warmup`, `cold_start_qa` | ⚠️ TODO | Ідея зафіксована у документі, але `fxcm_status_listener` ще не застосовує нові значення. |

- **Черги/канали**: результати QA можна тимчасово публікувати у `ai_one:ui:asset_state` з тегом `snapshot_only` (UI відображає "історичний режим"), а після `ready` переходити на стандартний канал.
- **Безшовний перехід**: після впровадження live-гейта `screening_producer` і `AssetStateManager` мають приймати live апдейти лише коли `cold_start_status == "ready"`.
- **Health-check**: планується Prometheus gauge `ai_one_cold_start_state{phase=...}` + timer, щоб CI/ops бачили затримку cold-start (TODO разом із гейтом).

## 12. Тестовий + продакшн сценарії

### Dev / часті ресети

1. Згенерувати штучний snapshot (`tools/fxcm_cache_seed.py`, майбутній скрипт) і покласти у `datastore/`.
2. Запустити `python -m tools.cold_start_runner --symbols xauusd --interval 1m --json --output tmp/cold_start_report.json`.
3. Перевірити у виводі, що `summary.stale_symbols == []`, `summary.insufficient_symbols == []`, а кожен елемент `entries` має `age_seconds ≤ stale_threshold`. Якщо умови не виконано — повернутися до backfill.
4. Прогнати SMC QA (див. нижче) й таргетні тести: `pytest tests/test_stage1_thresholds.py tests/test_fxcm_telemetry.py`.

### Prod

1. **Cold-start**:
   - `python -m tools.cold_start_runner --symbols xauusd eurusd --interval 1m --json --output reports/cold_start_report.json`.
   - Файл у `reports/` зберігається як артефакт перевірки; у логах окремо фіксуємо контрольні значення `summary.*`.
2. **QA через SmcCore**:
   - `python -m tools.smc_snapshot_runner XAUUSD --tf 1m --extra 5m 15m 1h --limit 500 --force | Out-File -Encoding utf8 tmp/smc_snapshot_xauusd.json`.
   - Узагальнити результати (BOS/CHOCH count, кількість liquidity pools, наявність `smc_hint`) і зберегти у `tmp/smc_qa_summary_<symbol>.json` (можна використати одноразовий `python -c` для агрегації полів `structure.events`, `liquidity.pools`, `signals`).
   - QA вважається успішною, якщо BOS/CHOCH та `smc_hint` відповідають очікуванням трейдера або свідомо порожні; у звіті залишаємо посилання на snapshot/summary файли.
   - Увага: якщо `smc_snapshot_*.json` показує мітки `1970‑01‑01T00:29:24.x`, це означає, що `ensure_timestamp_column` отримав `timestamp` як `float64` без `unit`, інтерпретував значення як наносекунди (`1.7646e+12 ns ≈ 1764 s`). Виправлення — приводити сирі `open_time/close_time` до `Int64` перед викликом адаптера або дописати логіку `ensure_timestamp_column`, щоб вона визначала `unit` також для `float`. Лише після цього QA вважається валідною.
3. **Live hand-off**: після `ready` відкрити UI, перевірити, що `FxcmFeedState.lag_seconds < 120` с, `ai_one:ui:asset_state` отримує live payload, а `cold_start_status.state == "ready"`.
4. **Health-check**: cron job (кожні 4 години) запускає `python -m tools.cold_start_runner --json` і валідуює, що `summary.stale_symbols` порожній та `summary.max_age_seconds < 14400`. У випадку відхилень тригеримо ручний QA через `smc_snapshot_runner` перед поверненням у live.

## 13. Подальші кроки

1. Розширити `cold_start_runner` режимами `qa-only` та `health` (з автоматичними QA-прогонами).
2. Додати RPC у FX Connector для bulk-history (або стандартний jsonl drop у `datastore/XAUUSD/week_*`).
3. Додати банер cold-start статусу в UI (`publish_full_state.py`) з використанням результатів раннера.
5. Додати інтеграційні тести:
   - smoke `cold_start_runner` (на 200 synthetic барів);
   - end-to-end (використати локальний Redis pubsub, dummy FXCM producer).

---

- Пов'язані документи: `docs/stage1_pipeline.md` (загальний опис), `docs/fxcm_integration.md` (контракт із конектором), `docs/readme_for_conector.md` (окремий репозиторій). Цей файл описує новий cold-start pipeline і єдине джерело правди перед реалізацією.
