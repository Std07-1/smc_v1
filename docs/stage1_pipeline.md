# SMC pipeline та холодний старт (Stage1 legacy)

> ⚠️ Stage1 пайплайн і тригери **заморожені** і в поточному `smc_v1` не є основним рантайм-шляхом. Вхідна точка `python -m app.main` запускає **SMC-only** пайплайн.

Короткий конспект: як `app.main` отримує FXCM дані, що відбувається на холодному старті та де шукати актуальні контракти/налаштування, щоб не перечитувати код.

## 1. Порядок запуску `app.main`

1. **Bootstrap**
   - `bootstrap()` читає `datastore.yaml`, створює `StoreConfig` і єдиний інстанс `UnifiedDataStore`.
   - `_warmup_datastore_from_snapshots()` виконується одразу після старту, прогріваючи RAM+Redis локальними JSONL снапшотами.
2. **FXCM ingest та статус**
   - `run_fxcm_ingestor()` підписується на Redis-канал `fxcm:ohlcv` і кожен пакет від зовнішнього FXCM конектора пише у `UnifiedDataStore.put_bars()`.
   - `run_fxcm_status_listener()` слухає агрегований канал `fxcm:status` (process/market/price/ohlcv/session/note), формуючи `FxcmFeedState` для UI/SMC. Детальні канали `fxcm:heartbeat`/`fxcm:market_status` у цьому репо більше не є обов'язковими.
   - `run_fxcm_price_stream_listener()` слухає `fxcm:price_tik` і оновлює кеш живих цін у `UnifiedDataStore` (останній bid/ask/mid).
3. **SMC цикл**
   - `smc_producer` читає історичні бари з `UnifiedDataStore` (для whitelisted символів), рахує SMC-core та публікує агрегований стан у Redis для UI.
4. **UI/споживачі**
   - UI_v2 піднімає HTTP+WS інтерфейси для read-only перегляду (див. `deploy/viewer_public/README.md`).
   - Метрики для UI публікуються окремим таском у канал `ui.metrics`.

## 2. Єдине джерело даних

- Уся жива історія приходить **лише** з зовнішнього FXCM конектора (Python 3.7): OHLCV-бари через `fxcm:ohlcv`, price snapshots через `fxcm:price_tik`, а телеметрія/health — через агрегований статус `fxcm:status`.
- Локальні warmup-скрипти й прямі виклики біржових API видалені, щоб не дублювати функціонал конектора.
- Будь-який календар, warmup чи дедуплікація реалізується саме у зовнішньому сервісі; Stage1 тільки читає Redis.

Джерело істини для FXCM-контрактів у цьому репо:

- TypedDict контракти + назви каналів: `core/contracts/fxcm_channels.py`;
- soft-валидація (drop некоректних барів без падіння процесу): `core/contracts/fxcm_validate.py`;
- інваріанти інжесту (наприклад, skip live-барів `complete=false`): `tests/test_fxcm_schema_and_ingestor_contract.py`;
- огляд каналів/ланцюжка даних: `docs/fxcm_contract_audit.md` та `docs/fxcm_integration.md`.

> Канал `fxcm:status` публікує компактний JSON (`process/market/price/ohlcv/session/note`). `FxcmFeedState` зберігає ці поля (`price_state`, `ohlcv_state`, countdown до сесій) і додає їх в `stats` кожного активу та мета-блок UI.

## 3. Холодний старт

| Крок | Що відбувається | Що контролює |
| --- | --- | --- |
| Snapshot warmup | `_warmup_datastore_from_snapshots()` прогріває RAM+Redis останніми файлами `datastore/*_bars_<tf>_snapshot.jsonl`. | Bootstrap (`datastore.yaml`) |
| Очікування даних | Після старту інжестора історія в `UnifiedDataStore` доповнюється живим стрімом `fxcm:ohlcv` (лише `complete=true`). До появи достатньої історії SMC цикл може працювати з обмеженнями або пропускати ітерації (залежить від конфігу/контракту). | `SCREENING_LOOKBACK` (lookback для SMC), fxcm_contract (якщо заданий) |

Повний опис послідовності warmup → ingest → UI зібрано нижче, щоб уникнути повторного читання `app/main.py` при кожному аудиті.

## 4. Основні налаштування

| Файл | Поле | Призначення |
| --- | --- | --- |
| `config/config.py` | `FXCM_FAST_SYMBOLS` | whitelist символів для пайплайна (SMC/UI) |
| `config/config.py` | `SCREENING_LOOKBACK` | lookback барів для `smc_producer` |
| `app/settings.py` | `Settings.fxcm_*` | HMAC / канали / host Redis для інжестора |
| `data/datastore.yaml` | `base_dir`, `namespace`, `write_behind` | структура файлів для snapshot warmup |

## 5. Діагностика

- **Логи `app.main`**: шукай `[Warmup]`, `[FXCM_INGEST]`, `[FXCM_STATUS]`, `[FXCM_PRICE]`, `[Pipeline]`.
- **Redis**: Pub/Sub `fxcm:ohlcv`, `fxcm:price_tik`, `fxcm:status`; метрики для UI публікуються в `ui.metrics`.
- **Prometheus**: якщо увімкнено, корисні `ai_one_fxcm_feed_lag_seconds`, `ai_one_fxcm_feed_state`.

Ці нотатки достатні, щоб не поринати в код при перевірці запуску пайплайна.
