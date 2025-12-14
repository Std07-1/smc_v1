# Аудит Redis-контрактів FXCM (OHLCV/телеметрія/команди)

Цей документ описує контракти, що надходять із зовнішнього FXCM конектора, та шлях даних усередині `smc_v1`.

Джерела істини:

- контракт конектора (файл `contracts.md` у репо конектора);
- реалізація в цьому репо: `data/fxcm_ingestor.py`, `data/fxcm_status_listener.py`, `data/fxcm_models.py`, `data/unified_store.py`, `app/main.py`.
- мінімальна схема (що саме ми вважаємо обов’язковим для споживання) зафіксована в `data/fxcm_schema.py`, а ключові інваріанти — у `tests/test_fxcm_schema_and_ingestor_contract.py`.

## 1. Загальний ланцюжок

1. **FXCM Connector (Python 3.7, поза репозиторієм)** отримує тики від FXCM/ForexConnect, формує OHLCV та телеметрію й публікує у Redis Pub/Sub канали.
2. **Redis канали**:
   - `fxcm:ohlcv` — основний потік барів (JSON із батчем барів, додатково `sig` для HMAC).
   - `fxcm:heartbeat` — службовий пінг процесу інжестора з затримкою, останнім close та коротким контекстом.
   - `fxcm:market_status` — календарна подія «ринок відкрито/закрито», наступне відкриття тощо.
   - `fxcm:price_tik` — price snapshot (bid/ask/mid) із позначками часу `tick_ts`/`snap_ts` (секунди Unix).
   - `fxcm:status` — агрегований «простий» статус (process/market/price/ohlcv/session/note).
   - `fxcm:commands` — вхідний канал команд до конектора (warmup/backfill/set_universe).
3. **Внутрішні консюмери**:
   - `run_fxcm_ingestor` (`data/fxcm_ingestor.py`) підписується на `fxcm:ohlcv`, валідує HMAC, перетворює на `DataFrame` та викликає `UnifiedDataStore.put_bars()`. Додатково він викликає `note_fxcm_bar_close()` щоб передати час останнього бара в `FxcmFeedState`.
   - `run_fxcm_status_listener` (`data/fxcm_status_listener.py`) підписується на агрегований канал `fxcm:status`, парсить payload і оновлює `FxcmFeedState` для UI/Stage1 (heartbeat/market_status у цьому репо більше не є обов'язковими).
   - `run_fxcm_price_stream_listener` (`data/fxcm_price_stream.py`) слухає `fxcm:price_tik` та оновлює кеш останньої ціни в `UnifiedDataStore`.
4. **Оркестрація**: `app/main.py` створює обидва лістенери під час `run_pipeline()` і зберігає стан у `UnifiedDataStore`. Цей стан далі публікується у канал `ui.metrics` (див. `ui_metrics_publisher`).
5. **UI / Stage1** отримують метрики через `UnifiedDataStore.metrics_snapshot()` → `publish_full_state()` / `UI.experimental_viewer_extended`, а Stage1 використовує самі OHLCV-бари з цього ж сховища.

## 2. Контракт `fxcm:ohlcv`

| Поле           | Тип / приклад                    | Опис |
|----------------|----------------------------------|------|
| `symbol`       | `"EURUSD"`                       | Символ у верхньому регістрі (нормалізується до lower у інжесторі). |
| `tf`           | `"1m"`                           | Таймфрейм; конектор вже переводить `m1→1m`, `h1→1h` тощо. |
| `source` (опц.)| `"stream"` / `"tick_agg"` / `"history_s3"` | Джерело/режим формування барів. Інжестор не покладається на це поле. |
| `bars`         | Список обʼєктів                   | Кожен бар має `open_time`, `close_time`, `open`, `high`, `low`, `close`, `volume`. Час у мілісекундах Unix. |
| `sig` (опц.)   | hex-рядок                         | HMAC підпис. Перевіряється, якщо увімкнено `FXCM_HMAC_SECRET`. |

Нотатки:

- HMAC (`sig`) верифікується по **base payload** `{"symbol","tf","bars"}` (без `source` та інших root-полів).
- Бари можуть містити додаткові поля (напр. `complete`, `synthetic`, microstructure). У цьому репо вони не зберігаються в UDS: перед записом бари санітизуються до базових OHLCV.
- Якщо бар має `complete=false`, інжестор пропускає його (live-бар у UDS не пишемо).

**Слухає**: `data/fxcm_ingestor.run_fxcm_ingestor()` створює `Redis.pubsub()`, виконує `await pubsub.subscribe("fxcm:ohlcv")` і обробляє всі повідомлення з `type="message"`.

**Обробка**:

- `_process_payload()` робить базову валідацію, опційно перевіряє HMAC (`_verify_hmac_signature`).
- `_bars_payload_to_df()` конвертує список барів у `pandas.DataFrame`, сортує за `open_time` та приводить типи.
- `UnifiedDataStore.put_bars(symbol, interval, df)` записує дані в RAM+Redis.
- `note_fxcm_bar_close()` отримує останній `close_time` і оновлює `FxcmFeedState.last_bar_close_ms`, що дозволяє метрикам розрахувати `lag_seconds`, навіть якщо heartbeat тимчасово не містить цієї інформації.
- Кожні `log_every_n` валідних пакетів інжестор логує `[FXCM_INGEST] Інгестовано барів: <total> (останній пакет: <symbol> <tf>, rows=<n>)`. Якщо конектор шле одразу закритий бар, `rows=1` є нормою й означає почасову доставку без батчів (не помилка).
- Перед передачею в Stage1/SMC всі DataFrame проходять через `ensure_timestamp_column`. Якщо бачимо мітку `1970-01-01T00:29:24.549000`, це означає, що `timestamp` лишився як `float64` й був інтерпретований як наносекунди (`1.7646e+12 ns ≈ 1764 s`). Починаючи з 2025-12-08 функція сама примусово конвертує такі `float64/int64` серії (через `pd.to_numeric(..., unit=auto)`) у UTC-час із автодобором одиниць (`s/ms/us/ns`), але QA все одно повинні стежити, щоб у кадрі була валідна часова колонка — інакше SMC отримає «епоху» замість реального 2025 року.

**Споживачі після інжестора**:

- Stage1 (`AssetMonitorStage1`) та Screening Producer зчитують історію виключно через `UnifiedDataStore` (наприклад, `store_to_dataframe`).
- UI бере дані з Redis снапшоту, який формує `publish_full_state` (через той же `UnifiedDataStore`).

## 3. Контракт `fxcm:heartbeat`

Модель: `FxcmHeartbeat` у `data/fxcm_models.py`.

| Поле            | Тип / приклад                                      | Опис |
|-----------------|----------------------------------------------------|------|
| `type`          | `"heartbeat"`                                     | Постійне значення для валідації. |
| `state`         | `"warmup" | "warmup_cache" | "stream" | "idle"` | Процесний стан конектора. `stream` означає активне надходження барів. |
| `last_bar_close_ms` | `1733030400000`                             | Час закриття останнього опрацьованого бара. |
| `ts`            | `"2025-12-01T11:54:00+00:00"`                    | Мітка часу конектора (UTC ISO), тепер логуються у `FxcmFeedState`. |
| `context`       | Обʼєкт `FxcmHeartbeatContext` (опційний)            | Додаткові поля нижче. |

`FxcmHeartbeatContext`:

| Поле                 | Тип / приклад | Призначення |
|----------------------|---------------|-------------|
| `lag_seconds`        | `1.37`        | Затримка між ринком і конектором; використовується напряму. |
| `market_pause`       | `false`       | Прапорець технічної паузи FXCM. |
| `market_pause_reason`| `"maintenance"` | Людський опис паузи. |
| `seconds_to_open`    | `5400`        | Скільки секунд до наступного відкриття (коли ринок закритий). |
| `stream_targets`     | `list[dict]` або dict | Внутрішня діагностика (які символи/ТF стрімляться, lag/staleness). |
| `bars_published`     | int           | Кількість барів у поточному батчі/періоді (`published_bars` у новому контракті). |
| `next_open_utc`      | `"2025-12-01 22:00:00Z"` | Розклад відкриття (дублює market_status, але приходить частіше). |
| `next_open_ms`       | `1764626400000` | Те ж саме у мс. |
| `idle_reason`        | str           | Пояснення для стану `idle` (наприклад, `maintenance`). |
| `cache_enabled` / `cache_source` | bool / str | Статус файлового кешу (read/write, fallback). |
| `session`            | обʼєкт        | Новий блок із тегом сесії, таймзоною, weekly open/close, перервами та наступним відкриттям (UTC/ms/seconds). |

> Примітка: у поточній реалізації `smc_v1` ми **не** споживаємо `fxcm:heartbeat` напряму — основним джерелом стану для UI/Stage1 є агрегований канал `fxcm:status`.
>
> Heartbeat лишається корисним як «детальна телеметрія» конектора, але його інтерпретація й FSM віднесені на бік конектора/операторських інструментів.

## 4. Контракт `fxcm:market_status`

Модель: `FxcmMarketStatus` у `data/fxcm_models.py`.

| Поле                   | Тип / приклад                 | Опис |
|------------------------|-------------------------------|------|
| `type`                 | `"market_status"`            | Для валідації. |
| `state`                | `"open"` або `"closed"`     | Поточний глобальний стан FXCM. |
| `next_open_ms`         | `1764626400000`               | Час наступного відкриття ринку (мс). |
| `next_open_utc`        | `"2025-12-01 22:00:00Z"`     | Людське представлення. |
| `next_open_in_seconds` / `next_open_seconds` | `5400` | Обидва ключі підтримуються; мапляться до `seconds_to_open`. |
| `ts`                   | `"2025-12-01T11:52:04+00:00"` | Позначка часу події, зберігається у `FxcmFeedState`. |
| `session`              | обʼєкт                         | Та ж структура, що й у heartbeat, синхронізована з календарем. |

**Канал**: публікується конектором як подія календаря/ринкового стану.

> Примітка: цей опис наведено як довідка по контракту конектора. У `smc_v1` стан ринку/фіду для UI/Stage1 читається з `fxcm:status`.

## 5. Лістенер `run_fxcm_status_listener`

- **Де запускається:** у `app/main.py` → `run_pipeline()` створює `asyncio.create_task(run_fxcm_status_listener(...))` одразу після старту інжестора.
- **Підписка:** одна `Redis.pubsub()` сесія на канал `fxcm:status`. Дані обробляються в одному циклі `async for message in pubsub.listen():`.
- **Обробник:** перевіряє `message["type"] == "message"`, декодує JSON, та на основі `channel` викликає відповідний парсер. Некоректні payload логуються з попередженням; лістенер не падає.
- **Стан:** `_FXCM_FEED_STATE` — потокобезпечний `dataclass` під `threading.Lock`. Будь-який консюмер може викликати `get_fxcm_feed_state()` і отримати snapshot.
- **Метрики:** `_update_metrics()` оновлює Prometheus gauges `ai_one_fxcm_feed_lag_seconds` та `ai_one_fxcm_feed_state` (з лейблами `market_state`, `process_state`).

## 6. Публікація у UI / подальше використання

1. `UnifiedDataStore.metrics_snapshot()` вбудовує `FxcmFeedState.to_metrics_dict()` у ключ `fxcm` (див. `data/unified_store.py`). Цей зріз викликається:
   - у `app/main.py` (функція `ui_metrics_publisher`) кожні 5 секунд → Redis канал `ui.metrics` (через `redis_pub.publish`).
   - у `UI.publish_full_state.publish_full_state()` під час формування повного snapshot для UI (викликається при запуску та за запитом Stage1).
2. UI (клас `SmcExperimentalViewer` / `SmcExperimentalViewerExtended`) читає ці значення та відображає: `Market`, `Process`, `Lag`, `Last close`, `Next open` тощо. Починаючи з оновлення 2025-12, поле `Next open` показує фактичний timestamp лише коли ринок закритий; при `market_state="open"` відображається дефіс, навіть якщо календар повертає наступний слот. Extended viewer дублює цю поведінку в рядку «Наступне відкриття».
3. Stage1/Stage2 користуються самими даними (`UnifiedDataStore`) для аналізу барів; логіка ризик-менеджменту також може використовувати `fxcm` метрики для перевірки затримок та стану ринку.
4. Після оновлення 2025-12 `fxcm`-блок додатково містить `heartbeat_ts`, `market_status_ts`, `published_bars`, `stream_targets` та `session`, що дозволяє UI диференціювати джерело затримки (немає heartbeat ≠ немає OHLCV) і бачити актуальний профіль торгової сесії.

## 7. Контрольні точки та рекомендації

- **Синхронізація контрактів:** будь-яка зміна структури payload у конекторі потребує оновлення відповідних Pydantic-моделей у `data/fxcm_models.py` (зокрема `FxcmAggregatedStatus`, а також за потреби `FxcmHeartbeat`/`FxcmMarketStatus`). Інакше Pydantic кине ValidationError, який ми бачимо у логах `[FXCM_STATUS] Некоректний ... payload`.
- **HMAC:** якщо вимкнути `FXCM_HMAC_REQUIRED`, пакети з підписом все одно приймаються, але логують попередження один раз (`_log_unexpected_sig_once`). Важливо синхронізувати налаштування на обох сторонах.
- **Lag fallback:** навіть при втраті heartbeat `lag_seconds` розраховується як `now - last_bar_close_ms` (див. `FxcmFeedState.to_metrics_dict()`), тому важливо щоб `note_fxcm_bar_close()` продовжував отримувати час із `fxcm:ohlcv`.
- **UI TTL:** UI орієнтується лише на свіжість `ai_one:ui:snapshot` та `fxcm` метрики; спеціальний keepalive для cold-start більше не використовується.
- **Моніторинг інжестора:** якщо у логах довго немає `[FXCM_INGEST]` або `rows` дорівнює 0, спершу перевіряємо `log_every_n` (можливо, повідомлення зашумлюють) і статус Redis Pub/Sub. Для діагностики можна тимчасово підняти `log_every_n` до 10–20, щоб бачити агреговані підтвердження без спаму.

Таким чином, канали даних і статусу формують замкнений цикл: OHLCV → UDS/Stage1/SMC, `fxcm:price_tik` → live price cache, `fxcm:status` → телеметрія/health/IDLE policy, а `fxcm:commands` → best-effort запити на догрузку історії. Доки ці контракти зберігають описану структуру, система підтримує прозору діагностику затримок, календарних пауз та якості даних.
