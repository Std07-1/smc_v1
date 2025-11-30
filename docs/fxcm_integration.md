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

### 2.2 `FxcmFeedState`

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

## 3. ENV / конфігурація

| Змінна | Опис |
| --- | --- |
| `FXCM_HMAC_SECRET` | Ключ для перевірки HMAC (None → вимкнено). |
| `FXCM_HMAC_ALGO` | Алгоритм digest (типово `sha256`). |
| `FXCM_HMAC_REQUIRED` | `true/false`, чи відкидати пакети без підпису. |
| `FXCM_HEARTBEAT_CHANNEL` | Канал heartbeat (типово `fxcm:heartbeat`). |
| `FXCM_MARKET_STATUS_CHANNEL` | Канал статусу (типово `fxcm:market_status`). |
| `FXCM_STALE_LAG_SECONDS` | Поріг лагу (типово `120` с). |
| `FXCM_DUKA_WARMUP_ENABLED` | Чи підвантажувати історію з Dukascopy перед стартом (default `false`). |
| `REDIS_HOST/PORT` | Повинні збігатися у конектора й AiOne_t. |

## 4. Пайплайн AiOne_t

1. `app/main.py` запускає `run_fxcm_ingestor(...)` і `run_fxcm_status_listener(...)` в одному event loop.
2. `screening_producer`:
   - Отримує `FxcmFeedState` через `get_fxcm_feed_state()`.
   - Якщо `market_state="closed"` → `FX_MARKET_CLOSED` сигнал (без помилки).
   - Якщо `lag_seconds > FXCM_STALE_LAG_SECONDS` → `FX_FEED_STALE`.
3. `UnifiedDataStore.metrics_snapshot()` містить блок `"fxcm": {...}`.
4. `UI/publish_full_state` додає той же блок у payload, щоб viewer показував банер стану.

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
   > Примітка: холодний старт через Dukascopy виконується лише якщо `FXCM_DUKA_WARMUP_ENABLED=true`. За замовчуванням покладаємося на прогрів самого конектора FXCM.

4. **Моніторинг**:
   - Prometheus: `ai_one_fxcm_feed_lag_seconds` та `ai_one_fxcm_feed_state`.
   - UI viewer: банер «FXCM ринок закритий» / індикатор лагу.

## 6. Заборони

- Не викликати ForexConnect/OHLCV REST у цьому репо.
- Не дублювати календар FX; довіряємо `market_status`.
- Не слухати `fxcm:ohlcv` поза `run_fxcm_ingestor`.
- Символи/таймфрейми лише у вигляді `xauusd`, `1m`, `5m` і т.д.

Дотримання цих правил гарантує, що конектор та пайплайн працюють як єдиний FX data-layer із прозорою телеметрією.
