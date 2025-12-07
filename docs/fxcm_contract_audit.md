# Аудит Redis-контрактів FXCM (`fxcm:ohlcv`, `fxcm:heartbeat`, `fxcm:market_status`)

Цей документ описує актуальні (станом на 2025-12-01) контракти, що надходять із зовнішнього FXCM конектора, а також увесь шлях даних усередині `smc_v1`. Джерелом істини слугують модулі `data/fxcm_ingestor.py`, `data/fxcm_status_listener.py`, `data/fxcm_models.py`, `data/unified_store.py` та `app/main.py`.

## 1. Загальний ланцюжок

1. **FXCM Connector (Python 3.7, поза репозиторієм)** отримує тики від FXCM/ForexConnect, формує OHLCV та телеметрію й публікує у Redis Pub/Sub канали.
2. **Redis канали**:
   - `fxcm:ohlcv` — основний потік барів (JSON із батчем барів, додатково `sig` для HMAC).
   - `fxcm:heartbeat` — службовий пінг процесу інжестора з затримкою, останнім close та коротким контекстом.
   - `fxcm:market_status` — календарна подія «ринок відкрито/закрито», наступне відкриття тощо.
3. **Внутрішні консюмери**:
   - `run_fxcm_ingestor` (`data/fxcm_ingestor.py`) підписується на `fxcm:ohlcv`, валідує HMAC, перетворює на `DataFrame` та викликає `UnifiedDataStore.put_bars()`. Додатково він викликає `note_fxcm_bar_close()` щоб передати час останнього бара в `FxcmFeedState`.
   - `run_fxcm_status_listener` (`data/fxcm_status_listener.py`) підписується одночасно на `fxcm:heartbeat` та `fxcm:market_status`, парсить payload через `parse_fxcm_*` і оновлює глобальну структуру `FxcmFeedState`.
4. **Оркестрація**: `app/main.py` створює обидва лістенери під час `run_pipeline()` і зберігає стан у `UnifiedDataStore`. Цей стан далі публікується у канал `ui.metrics` (див. `ui_metrics_publisher`).
5. **UI / Stage1** отримують метрики через `UnifiedDataStore.metrics_snapshot()` → `publish_full_state()` / `UIConsumer`, а Stage1 використовує самі OHLCV-бари з цього ж сховища.

## 2. Контракт `fxcm:ohlcv`

| Поле           | Тип / приклад                    | Опис |
|----------------|----------------------------------|------|
| `symbol`       | `"EURUSD"`                       | Символ у верхньому регістрі (нормалізується до lower у інжесторі). |
| `tf`           | `"1m"`                           | Таймфрейм; конектор вже переводить `m1→1m`, `h1→1h` тощо. |
| `bars`         | Список обʼєктів                   | Кожен бар має `open_time`, `close_time`, `open`, `high`, `low`, `close`, `volume`. Час у мілісекундах Unix. |
| `sig` (опц.)   | hex-рядок                         | HMAC підпис. Перевіряється, якщо увімкнено `FXCM_HMAC_SECRET`. |

**Слухає**: `data/fxcm_ingestor.run_fxcm_ingestor()` створює `Redis.pubsub()`, виконує `await pubsub.subscribe("fxcm:ohlcv")` і обробляє всі повідомлення з `type="message"`.

**Обробка**:

- `_process_payload()` робить базову валідацію, опційно перевіряє HMAC (`_verify_hmac_signature`).
- `_bars_payload_to_df()` конвертує список барів у `pandas.DataFrame`, сортує за `open_time` та приводить типи.
- `UnifiedDataStore.put_bars(symbol, interval, df)` записує дані в RAM+Redis.
- `note_fxcm_bar_close()` отримує останній `close_time` і оновлює `FxcmFeedState.last_bar_close_ms`, що дозволяє метрикам розрахувати `lag_seconds`, навіть якщо heartbeat тимчасово не містить цієї інформації.
- Кожні `log_every_n` валідних пакетів інжестор логує `[FXCM_INGEST] Інгестовано барів: <total> (останній пакет: <symbol> <tf>, rows=<n>)`. Якщо конектор шле одразу закритий бар, `rows=1` є нормою й означає почасову доставку без батчів (не помилка).
- Перед передачею в Stage1/SMC всі DataFrame проходять через `ensure_timestamp_column`. Якщо бачимо мітку `1970-01-01T00:29:24.549000`, це означає, що `timestamp` лишився як `float64` й був інтерпретований як наносекунди (`1.7646e+12 ns ≈ 1764 s`). Використовуйте `pd.to_numeric(...).astype("Int64")` або допрацьовуйте `ensure_timestamp_column`, щоб визначити `unit` для `float`, інакше QA/SMC отримають «епоху» замість реального 2025 року.

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

**`context.session`** структурується так:

| Поле | Опис |
|------|------|
| `tag` | Ідентифікатор профілю (наприклад, `NY_METALS`). |
| `timezone` | Таймзона календаря (string, наприклад `America/New_York`). |
| `weekly_open` / `weekly_close` | Рядки з часом і TZ (`18:00@America/New_York`). |
| `daily_breaks` | Масив рядків або обʼєктів `{start,end,tz}`; описує паузи. |
| `next_open_utc` / `next_open_ms` / `next_open_seconds` | Наступний слот відкриття з already-конвертованими значеннями. |

**Слухає**: `run_fxcm_status_listener` (див. нижче). Лістенер використовує `parse_fxcm_heartbeat()` для суворої валідації та перетворення у Pydantic-модель.

**Оновлення стану** (`_apply_heartbeat`):

- Оновлює `FxcmFeedState.process_state`, `last_bar_close_ms`, `lag_seconds`, `next_open_{utc,ms}`, `seconds_to_open`, `market_pause`, `market_pause_reason`.
- Якщо `market_state` ще `unknown`, але `process_state` ∈ {`stream`, `warmup`, `warmup_cache`}, стан вважається `open` (це захищає від коротких розсинхронів із каналом `market_status`).
- Коли `process_state == "idle"` і вже відомий `next_open_utc`, лістенер переводить `market_state` у `closed`.
- Після кожного оновлення викликається `_update_metrics()`, який пробиває два Prometheus gauge.

**Використання даних**:

- `UnifiedDataStore.metrics_snapshot()` у полі `fxcm` віддає `lag_seconds`, `market`/`process` та часові позначки, що UI відображає у телеметрії.
- Stage1 не читає heartbeat напряму, але залежить від нього через `FxcmFeedState`, що визначає коли стрім достатньо прогрітий для live-режиму.
- Якщо `next_open_utc` у heartbeat дорівнює плейсхолдеру (`"-"`, `None`) і ринок **закритий**, UI автоматично підтягує значення з `session.next_open_*`; коли ж `market_state == "open"`, обидва viewer-и примусово показують `"-"`, щоб не вводити користувача в оману майбутнім слотом поки торги активні.

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

**Слухає**: той самий `run_fxcm_status_listener`. Канал передається аргументом `market_status_channel` (за замовчуванням `fxcm:market_status`).

**Оновлення стану** (`_apply_market_status`):

- Перевіряє `state` на належність множині `_MARKET_STATES` і зберігає у `FxcmFeedState.market_state`.
- Оновлює `next_open_{utc, ms}`, `seconds_to_open`, `last_market_status_ts`.
- Якщо ринок `closed`, `next_open_utc` зберігається, і UI може показувати countdown. Коли `open`, `next_open_utc` очищується, щоб не плутати користувача.

**Використання**:

- UI показує тригер «Ринок відкрито/закрито», а також `next_open` у telemetry панелі (через `metrics_snapshot().fxcm`).
- Cold-start keep-alive (`_cold_status_keepalive`) переписує Redis-ключ, тільки якщо `_LATEST_COLD_STATUS.state != "unknown"`, тому коректний ринковий стан запобігає поверненню UI в UNKNOWN.

## 5. Лістенер `run_fxcm_status_listener`

- **Де запускається:** у `app/main.py` → `run_pipeline()` створює `asyncio.create_task(run_fxcm_status_listener(...))` одразу після старту інжестора.
- **Підписка:** одна `Redis.pubsub()` сесія на обидва канали (`fxcm:heartbeat`, `fxcm:market_status`). Дані обробляються в одному циклі `async for message in pubsub.listen():`.
- **Обробник:** перевіряє `message["type"] == "message"`, декодує JSON, та на основі `channel` викликає відповідний парсер. Некоректні payload логуються з попередженням; лістенер не падає.
- **Стан:** `_FXCM_FEED_STATE` — потокобезпечний `dataclass` під `threading.Lock`. Будь-який консюмер може викликати `get_fxcm_feed_state()` і отримати snapshot.
- **Метрики:** `_update_metrics()` оновлює Prometheus gauges `ai_one_fxcm_feed_lag_seconds` та `ai_one_fxcm_feed_state` (з лейблами `market_state`, `process_state`).

## 6. Публікація у UI / подальше використання

1. `UnifiedDataStore.metrics_snapshot()` вбудовує `FxcmFeedState.to_metrics_dict()` у ключ `fxcm` (див. `data/unified_store.py`). Цей зріз викликається:
   - у `app/main.py` (функція `ui_metrics_publisher`) кожні 5 секунд → Redis канал `ui.metrics` (через `redis_pub.publish`).
   - у `UI.publish_full_state.publish_full_state()` під час формування повного snapshot для UI (викликається при запуску та за запитом Stage1).
2. UI (клас `SmcExperimentalViewer` або стандартний `UIConsumer`) читає ці значення та відображає: `Market`, `Process`, `Lag`, `Last close`, `Next open` та ін. Починаючи з оновлення 2025-12, поле `Next open` показує фактичний timestamp лише коли ринок закритий; при `market_state="open"` відображається дефіс, навіть якщо календар повертає наступний слот. Extended viewer дублює цю поведінку в рядку «Наступне відкриття».
3. Stage1/Stage2 користуються самими даними (`UnifiedDataStore`) для аналізу барів; логіка ризик-менеджменту також може використовувати `fxcm` метрики для перевірки затримок та стану ринку.
4. Після оновлення 2025-12 `fxcm`-блок додатково містить `heartbeat_ts`, `market_status_ts`, `published_bars`, `stream_targets` та `session`, що дозволяє UI диференціювати джерело затримки (немає heartbeat ≠ немає OHLCV) і бачити актуальний профіль торгової сесії.

## 7. Контрольні точки та рекомендації

- **Синхронізація контрактів:** будь-яка зміна структури payload у конекторі потребує оновлення `FxcmHeartbeat`, `FxcmHeartbeatContext`, `FxcmMarketStatus` у `data/fxcm_models.py`. Інакше Pydantic кине ValidationError, який ми бачимо у логах `[FXCM_STATUS] Некоректний ... payload`.
- **HMAC:** якщо вимкнути `FXCM_HMAC_REQUIRED`, пакети з підписом все одно приймаються, але логують попередження один раз (`_log_unexpected_sig_once`). Важливо синхронізувати налаштування на обох сторонах.
- **Lag fallback:** навіть при втраті heartbeat `lag_seconds` розраховується як `now - last_bar_close_ms` (див. `FxcmFeedState.to_metrics_dict()`), тому важливо щоб `note_fxcm_bar_close()` продовжував отримувати час із `fxcm:ohlcv`.
- **UI TTL:** UI орієнтується лише на свіжість `ai_one:ui:snapshot` та `fxcm` метрики; спеціальний keepalive для cold-start більше не використовується.
- **Моніторинг інжестора:** якщо у логах довго немає `[FXCM_INGEST]` або `rows` дорівнює 0, спершу перевіряємо `log_every_n` (можливо, повідомлення зашумлюють) і статус Redis Pub/Sub. Для діагностики можна тимчасово підняти `log_every_n` до 10–20, щоб бачити агреговані підтвердження без спаму.

Таким чином, усі три канали формують замкнений цикл: OHLCV → Stage1, Heartbeat/MarketStatus → телеметрія та health. Доки ці контракти зберігають описану структуру, система підтримує прозору діагностику затримок, календарних пауз та якості даних.
