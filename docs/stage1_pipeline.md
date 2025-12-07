# Stage1 data pipeline та холодний старт

Короткий конспект про те, як `app.main` отримує дані, чого очікувати під час холодного старту і де шукати налаштування.

## 1. Порядок запуску `app.main`

1. **Bootstrap**
   - `bootstrap()` читає `datastore.yaml`, створює `StoreConfig` і єдиний інстанс `UnifiedDataStore`.
   - Якщо `DATASTORE_WARMUP_ENABLED=True`, викликається `_warmup_datastore_from_snapshots()` та підтягуються локальні JSONL снапшоти.
2. **FXCM ingest та статус**
   - `run_fxcm_ingestor()` підписується на Redis-канал `fxcm:ohlcv` і кожен пакет від зовнішнього FXCM конектора пише у `UnifiedDataStore.put_bars()`.
   - `run_fxcm_status_listener()` слухає `fxcm:heartbeat`, `fxcm:market_status` **та `fxcm:status`** (агрегований процес/market/price/ohlcv), формуючи `FxcmFeedState` для UI/Stage1.
3. **Stage1 моніторинг**
   - `AssetMonitorStage1` отримує дані з `UnifiedDataStore`, готує сирі сигнали й делегує їх `AssetStateManager`.
   - `screening_producer` обходить `FXCM_FAST_SYMBOLS`, збирає стани та передає їх у `UI.publish_full_state`.
4. **UI/споживачі**
   - `publish_full_state` кладе snapshot у `ai_one:ui:snapshot` та публікує його в `ai_one:ui:asset_state`.
   - `UI.ui_consumer_entry` або experimental viewer читають той самий payload.

## 2. Єдине джерело даних

- Уся жива історія приходить **лише** з зовнішнього FXCM конектора (Python 3.7) через канали `fxcm:ohlcv`, `fxcm:heartbeat`, `fxcm:market_status` та агрегований статус `fxcm:status`.
- Локальні warmup-скрипти й прямі виклики біржових API видалені, щоб не дублювати функціонал конектора.
- Будь-який календар, warmup чи дедуплікація реалізується саме у зовнішньому сервісі; Stage1 тільки читає Redis.

> Канал `fxcm:status` публікує компактний JSON (`process/market/price/ohlcv/session/note`). `FxcmFeedState` зберігає ці поля (`price_state`, `ohlcv_state`, countdown до сесій) і додає їх в `stats` кожного активу та мета-блок UI.

## 3. Холодний старт

| Крок | Що відбувається | Що контролює |
| --- | --- | --- |
| Snapshot warmup | `_warmup_datastore_from_snapshots()` прогріває RAM+Redis останніми файлами `datastore/*_bars_<tf>_snapshot.jsonl`. | `DATASTORE_WARMUP_ENABLED`, `DATASTORE_WARMUP_INTERVALS` |
| Очікування стріму | `_await_fxcm_history()` чекає, поки `fxcm:ohlcv` заповнить мінімум `SCREENING_LOOKBACK` барів (`1m`) для whitelisted символів. | `SCREENING_LOOKBACK` |

> Якщо стрім ще не вийшов на потрібний обсяг, Stage1 продовжує слухати канал і логувати `[FXCM Stream]` попередження до появи необхідної історії.

Повний опис послідовності warmup → ingest → UI зібрано нижче, щоб уникнути повторного читання `app/main.py` при кожному аудиті.

## 4. Основні налаштування

| Файл | Поле | Призначення |
| --- | --- | --- |
| `config/config.py` | `FXCM_FAST_SYMBOLS` | whitelist Stage1 / список символів для інжесту |
| `config/config.py` | `DATASTORE_WARMUP_*`, `SCREENING_LOOKBACK` | параметри warmup поведінки |
| `app/settings.py` | `Settings.fxcm_*` | HMAC / канали / host Redis для інжестора |
| `data/datastore.yaml` | `base_dir`, `namespace`, `write_behind` | структура файлів для snapshot warmup |

## 5. Діагностика

- **Логи `app.main`**: шукай `[Warmup]`, `[FXCM Stream]`, `[Pipeline]`.
- **Redis**: `ai_one:ui:snapshot`, `ai_one:ui:metrics`, Pub/Sub `fxcm:ohlcv` (для перевірки сирих пакетів).
- **Prometheus**: якщо увімкнено, корисні `ai_one_fxcm_feed_lag_seconds`, `ai_one_fxcm_feed_state`.

Ці нотатки достатні, щоб не поринати в код при перевірці запуску пайплайна.
