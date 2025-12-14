# UDS ↔ SMC (S2/S3) оновлення • 2025-12-13

Цей документ фіксує мінімальний контракт між SMC (цей репозиторій) та майбутнім `fxcm_connector` (інший репозиторій) для warmup/backfill історії.

## 1) Контекст і правило complete-bar

- `UnifiedDataStore` (UDS) зберігає **лише complete OHLCV**.
- SMC-core/SMC-producer опирається на complete-бари для структурних перевірок.
- UI може показувати live/incomplete, але це окрема площина й не впливає на S2/S3.

## 2) S2: класифікація історії (локально в SMC)

S2 — це діагностика «чи достатня історія в UDS» для кожної пари `(symbol, tf)`.

### Пороги

- `insufficient`: `bars_count < min_history_bars`
- `stale_tail`: `now_ms - last_open_time_ms > stale_k * tf_ms`

Реалізація: `app.fxcm_history_state.classify_history()`.

## 3) S3: requester команд у Redis

S3 — best-effort воркер, який **не модифікує UDS напряму**, а лише публікує команди до конектора.

### Redis канал

- Дефолтний канал команд: `fxcm:commands`
- Канал фіксується в `config.config.SMC_S3_COMMANDS_CHANNEL` (дефолт: `fxcm:commands`).

### Типи команд

Контракт конектора підтримує три типи:

- `fxcm_warmup` — коли історії недостатньо
- `fxcm_backfill` — коли хвіст історії застарілий
- `fxcm_set_universe` — оновлення universe (списку символів/таймфреймів) на стороні конектора

У цьому репо S3 requester надсилає лише `fxcm_warmup`/`fxcm_backfill`. Команда `fxcm_set_universe` зарезервована для координатора/оператора та не є обов’язковою для Stage1/SMC-core.

### JSON payload (стабільна схема)

Команда завжди є JSON-рядком і має такі ключі:

- `type`: `fxcm_warmup` | `fxcm_backfill`
- `symbol`: string (uppercase, напр. `XAUUSD`)
- `tf`: string (lowercase, напр. `1m`, `5m`, `1h`)
- `min_history_bars`: int
- `lookback_minutes`: int (оцінка з `min_history_bars` та `tf`)
- `reason`: `insufficient_history` | `stale_tail`
- `s2`: об’єкт
  - `history_state`: `ok` | `insufficient` | `stale_tail` | `unknown`
  - `bars_count`: int
  - `last_open_time_ms`: int | null
- `fxcm_status`: об’єкт (діагностичний, конектор може ігнорувати)
  - `market`: `open` | `closed` | `unknown`
  - `price`: `ok` | `lag` | `down`
  - `ohlcv`: `ok` | `delayed` | `down`

Для `fxcm_set_universe` структура може бути простішою (залежить від конектора). Мінімально очікується:

- `type`: `fxcm_set_universe`
- `symbols`: список символів (uppercase) або обʼєкти з тегами
- (опц.) `tfs`: список таймфреймів (`1m`, `5m`, `1h`)

## 4) Rate-limit і reset поведінка

- Rate-limit застосовується **окремо** для ключа `(symbol, tf, type)`.
- Якщо `history_state` повернувся в `ok`, S3 скидає «active issue» (внутрішні лічильники), щоб при наступному погіршенні можна було одразу знову відправити команду без очікування cooldown.

### Конфіг (S3)

Усі ці параметри є бізнес-логікою та живуть у `config/config.py` (не в ENV):

- `SMC_S3_REQUESTER_ENABLED` — вмикає requester (дефолт вимкнено)
- `SMC_S3_POLL_SEC` (дефолт 60)
- `SMC_S3_COOLDOWN_SEC` (дефолт 900)
- `SMC_S2_STALE_K` (дефолт 3.0) — використовується як поріг stale_tail

## 5) Приклади

### warmup

```json
{
  "type": "fxcm_warmup",
  "symbol": "XAUUSD",
  "tf": "1m",
  "min_history_bars": 2000,
  "lookback_minutes": 2000,
  "reason": "insufficient_history",
  "s2": {
    "history_state": "insufficient",
    "bars_count": 0,
    "last_open_time_ms": null
  },
  "fxcm_status": {"market": "closed", "price": "ok", "ohlcv": "ok"}
}
```

### backfill

```json
{
  "type": "fxcm_backfill",
  "symbol": "XAUUSD",
  "tf": "1m",
  "min_history_bars": 2000,
  "lookback_minutes": 2000,
  "reason": "stale_tail",
  "s2": {
    "history_state": "stale_tail",
    "bars_count": 2000,
    "last_open_time_ms": 1700000000000
  },
  "fxcm_status": {"market": "open", "price": "ok", "ohlcv": "ok"}
}
```
