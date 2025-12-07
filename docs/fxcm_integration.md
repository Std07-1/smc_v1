# FXCM Integration Guide

> **Призначення:** описати контракт між FXCM конектором (Python 3.7) та головним пайплайном AiOne_t (Python 3.11) і визначити порядок запуску двох процесів.

## 1. Складові системи

| Компонент | Середовище | Обов'язки |
| --- | --- | --- |
| `fxcm_connector` | Python 3.7 + ForexConnect | Підключення до FXCM, нормалізація OHLCV, дедуплікація, календар, опціональний HMAC, публікація в Redis. |
| `smc_v1` (цей репо) | Python 3.11 | Прийом OHLCV через `run_fxcm_ingestor`, ведення `FxcmFeedState`, Stage1/Stage2 логіка, UI та метрики. |

## 2. Redis-канали

| Канал | Постачальник | Опис |
| --- | --- | --- |
| `fxcm:ohlcv` | конектор | Потік OHLCV свічок. |
| `fxcm:heartbeat` | конектор | Технічний стан стріму (`state`, `last_bar_close_ms`, `ts_utc`). |
| `fxcm:market_status` | конектор | Статус ринку (`open/closed`, `next_open_utc`). |
| `fxcm:price_tik` | конектор | Живі снепшоти bid/ask/mid із OfferTable (оновлення ~3 с). |

### 2.1 OHLCV payload

```json
{
  "symbol": "XAUUSD",
  "tf": "1m",
  "bars": [
    {
      "open_time": 1764002100000,
      "close_time": 1764002159999,
      "open": 1.152495,
      "high": 1.152640,
      "low": 1.152450,
      "close": 1.152530,
      "volume": 149.0
    }
  ],
  "sig": "..." // опційно, якщо HMAC увімкнено
}
```

Інжестор приводить `symbol`/`tf` до lower-case та викликає `UnifiedDataStore.put_bars`.

### 2.2 Heartbeat payload (оновлений контракт)

Heartbeat — єдине джерело правди про життєвий цикл стріму. Конектор публікує його у форматі `json.dumps(payload, separators=(",", ":"))`, забезпечуючи стабільні назви полів і повну back-compat.

```json
{
   "type": "heartbeat",
   "state": "warmup",                 // "warmup"|"warmup_cache"|"stream"|"idle"
   "last_bar_close_ms": 1764002159999,
   "context": {
      "lag_seconds": 4.3,
      "market_pause": false,
      "market_pause_reason": null,      // "calendar"|"fxcm_unavailable"|...
      "seconds_to_open": 0,
      "next_open_utc": "2025-11-30T22:15:00Z",
      "next_open_ms": 1764022500000,
      "stream_targets": ["xauusd"],
      "bars_published": 128
   }
}
```

Ключові поля для smc_v1:

- `state` — FSM стріму: `warmup` (ручне дозавантаження), `warmup_cache` (bulk history), `stream` (бойовий режим), `idle` (ринок закритий або конектор чекає).
- `last_bar_close_ms` — абсолютний timestamp останнього бару; використовується для cold-start/QA.
- `context.lag_seconds` — миттєвий лаг між heartbeat та останнім баром.
- `context.market_pause` + `context.market_pause_reason` — пояснюють, чому стрім стоїть (календар/аварія) без локальних евристик.
- `context.seconds_to_open`/`next_open_*` — дають ETA відкриття без зовнішніх календарів.

### 2.3 `FxcmFeedState`

`data/fxcm_status_listener.py` підтримує єдиний стан:

```python
@dataclass
class FxcmFeedState:
    market_state: str = "unknown"      # open|closed|unknown
    process_state: str = "unknown"     # warmup|stream|idle|...
    next_open_utc: str | None = None
    last_bar_close_ms: int | None = None
    last_heartbeat_ts: float | None = None
    last_status_ts: float | None = None
    lag_seconds: float | None = None
```

Stage1 та UI спираються на ці значення, а не на локальний календар.

### 2.4 Market status payload

Окремий канал `fxcm:market_status` дублює календарну інформацію для легких консюмерів:

```json
{
   "type": "market_status",
   "state": "open",                    // "open"|"closed"
   "next_open_utc": "2025-11-30T22:15:00Z",
   "next_open_ms": 1764022500000,
   "next_open_in_seconds": 5400
}
```

AiOne_t має зберігати це у тій же `FxcmFeedState`, щоб координатор міг відрізнити «закрито за календарем» від «дані не рухаються під час open».

### 2.5 Price stream payload (`fxcm:price_tik`)

Price stream публікує максимум один снапшот на символ за цикл (орієнтовно кожні 3 секунди) та містить вже нормалізовані bid/ask/mid:

```json
{
   "symbol": "XAUUSD",
   "bid": 4209.62,
   "ask": 4210.02,
   "mid": 4209.82,
   "tick_ts": 1764866660.0,
   "snap_ts": 1764866661.0
}
```

`run_fxcm_price_stream_listener` підписується на канал, нормалізує payload і оновлює кеш у `UnifiedDataStore.update_price_tick`. Stage1/UI використовують ці поля для живих цін у таблицях, оцінки спреду (`ask - bid`) та моніторингу тиші (`tick_age_sec = now - tick_ts`).

## 3. ENV / конфігурація

| Змінна | Опис |
| --- | --- |
| `FXCM_HMAC_SECRET` | Ключ для перевірки HMAC (None → вимкнено). |
| `FXCM_HMAC_ALGO` | Алгоритм digest (типово `sha256`). |
| `FXCM_HMAC_REQUIRED` | `true/false`, чи відкидати пакети без підпису. |
| `FXCM_HEARTBEAT_CHANNEL` | Канал heartbeat (типово `fxcm:heartbeat`). |
| `FXCM_MARKET_STATUS_CHANNEL` | Канал статусу (типово `fxcm:market_status`). |
| `FXCM_PRICE_TICK_CHANNEL` | Канал живих mid/bid/ask (типово `fxcm:price_tik`). |
| `FXCM_STALE_LAG_SECONDS` | Поріг лагу (типово `120` с). |
| `REDIS_HOST/PORT` | Повинні збігатися у конектора й AiOne_t. |

## 4. Пайплайн AiOne_t

1. `app/main.py` запускає `run_fxcm_ingestor(...)` і `run_fxcm_status_listener(...)` в одному event loop.
2. `run_fxcm_price_stream_listener` слухає `fxcm:price_tik` і оновлює кеш `UnifiedDataStore.update_price_tick`, щоб Stage1 бачила останній bid/ask/mid між закриттями барів.
3. `screening_producer`:
   - Отримує `FxcmFeedState` через `get_fxcm_feed_state()`.
   - Якщо `market_state="closed"` → `FX_MARKET_CLOSED` сигнал (без помилки).
   - Якщо `lag_seconds > FXCM_STALE_LAG_SECONDS` → `FX_FEED_STALE`.
4. `UnifiedDataStore.metrics_snapshot()` містить блок `"fxcm": {...}` та коротку телеметрію `price_stream`.
5. `UI/publish_full_state` додає ті самі блоки у payload, щоб viewer показував банер стану й live tick-метрики.

## 5. Runbook

1. **Запускаємо конектор** (окреме середовище Python 3.7):

   ```powershell
   cd <fxcm_connector_repo>
   python fxcm_connector.py --symbols XAUUSD,EURUSD
   ```

   Переконатись через `redis-cli SUBSCRIBE fxcm:ohlcv` та `fxcm:heartbeat`, що йде активний потік.
2. **Запускаємо AiOne_t**:

   ```powershell
   cd C:\Aione_projects\smc_v1
   .\.venv\Scripts\python.exe -m app.main
   ```

3. **Перевірка**:
   - `redis-cli GET ai_one:ui:snapshot` → поле `fxcm` з актуальним станом.
   - `python -m tools.smc_snapshot_runner xauusd --tf 1m` (опційно) читає ті самі бари з `UnifiedDataStore`.
   > Примітка: будь-який warmup/bar caching виконує саме зовнішній конектор; Stage1 не підтягує історію напряму з бірж.

4. **Моніторинг**:
   - Prometheus: `ai_one_fxcm_feed_lag_seconds` та `ai_one_fxcm_feed_state`.
   - UI viewer: банер «FXCM ринок закритий» / індикатор лагу.

## 6. Заборони

- Не викликати ForexConnect/OHLCV REST у цьому репо.
- Не дублювати календар FX; довіряємо `market_status`.
- Не слухати `fxcm:ohlcv` поза `run_fxcm_ingestor`.
- Символи/таймфрейми лише у вигляді `xauusd`, `1m`, `5m` і т.д.

Дотримання цих правил гарантує, що конектор та пайплайн працюють як єдиний FX data-layer із прозорою телеметрією.

## 7. Використання телеметрії у smc_v1

### 7.1 `fxcm_ingestor` / `fxcm_status_listener`

- Визначити dataclass/Pydantic-модель heartbeat і оновлювати `FxcmFeedState` з полів: `last_bar_close_ms`, `context.lag_seconds`, `context.market_pause`, `context.market_pause_reason`, `context.seconds_to_open`, `context.stream_targets`, `context.bars_published`.
- Для `fxcm:market_status` зберігати `state`, `next_open_ms`, `next_open_in_seconds` у тому ж стані, щоб coordinator мав одну точку доступу.
- Це дозволяє відрізнити «lag через вихідні» (market_pause=true, reason="calendar") від реальної деградації (market_pause=false, `lag_seconds` росте) без додаткових евристик.

### 7.2 Historical diagnostics

- Комбінуйте `last_bar_close_ms` + `lag_seconds` з heartbeat та `state` + `next_open_ms` з `fxcm:market_status`, щоб розрізняти календарні паузи та реальні деградації стріму.
- Якщо `market_status.state="closed"` і `market_pause_reason="calendar"`, stale-історія вважається нормою — достатньо попередження в логах.
- Якщо `market_status.state="open"`, але `lag_seconds` росте, Stage1 має лише логувати та підсвічувати проблему у телеметрії, без додаткового Redis FSM.

### 7.3 UI / viewer

- Показувати в банері `state=warmup|stream|idle`, `lag_seconds`, а також ознаку `market_pause` з причиною.
- Використовувати `next_open_in_seconds` / `next_open_utc`, щоби рендерити зрозуміле повідомлення типу «ринок спить до 22:15 UTC».

### 7.4 Канал warmup історії

- Окремий `fxcm:ohlcv_warmup` зараз не потрібен: heartbeat вже містить `state="warmup"|"warmup_cache"`.
- Stage1 може обмежувати production-сигнали доти, доки `state != "stream"`, використовуючи існуючі ключі Redis.
