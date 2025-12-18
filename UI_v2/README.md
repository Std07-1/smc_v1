## UI_v2 - базовий шар для SMC viewer

Мета `UI_v2` — дати стабільний, типізований контракт між:

- SMC рантаймом, який публікує агрегований стан у Redis (`UiSmcStatePayload`);
- службою трансляції viewer-стану (broadcaster);
- тонкими клієнтами (веб-фронтенд або інші клієнти).

### Шари UI_v2

0. **Контракти (SSOT)**

Контракти UI_v2 є частиною SSOT і живуть у `core/contracts/*`:

- `core/contracts/viewer_state.py`:
  - `UiSmcStatePayload` / `UiSmcAssetPayload` — вхідний агрегований стан у Redis;
  - `SmcViewerState` — агрегований стан для рендера (консоль/браузер/інші клієнти);
  - `VIEWER_STATE_SCHEMA_VERSION`.
- `docs/smc_hint_contract.md` — plain JSON контракт `SmcHintPlain` (що саме лежить у `asset["smc_hint"]`).

1. **viewer_state_builder**
   Чиста функція `build_viewer_state(...)`:

   - вхід: один `UiSmcAssetPayload`, мета `UiSmcMeta`, опційний `fxcm_block`;
   - вихід: один `SmcViewerState` із полями:
     - `symbol`, `price`, `session`, `payload_ts`, `payload_seq`, `schema`;
     - `structure.swings / legs / ranges / events / ote_zones`;
     - `liquidity.pools / amd_phase / magnets`;
     - `zones.raw.zones`;
     - `fxcm` + `meta.fxcm` (як є).
   - `schema` завжди дорівнює `smc_viewer_v1` (`VIEWER_STATE_SCHEMA_VERSION`), а `meta.schema_version`
     зберігає версію вихідного `UiSmcStatePayload` (наприклад, `smc_state_v1`).

### Джерела даних

Очікуваний потік даних:

1. SMC пайплайн формує plain hint (`SmcHintPlain`) згідно `smc_hint_contract.md`.
2. Паблішер `UI/publish_smc_state.py` упаковує активи в `UiSmcStatePayload` і публікує:

   - канал: `REDIS_CHANNEL_SMC_STATE`;
   - snapshot-ключ: `REDIS_SNAPSHOT_KEY_SMC`.

3. Служба **smc_viewer_broadcaster** читає `UiSmcStatePayload`, перетворює кожен asset →
  `SmcViewerState` через `build_viewer_state` і:

   - зберігає останній `SmcViewerState` по кожному символу у `REDIS_SNAPSHOT_KEY_SMC_VIEWER`;
   - публікує у `REDIS_CHANNEL_SMC_VIEWER_EXTENDED` payload `{"symbol": ..., "viewer_state": ...}`.

4. HTTP + WS шар (`UI_v2.viewer_state_server`, `UI_v2.viewer_state_ws_server`) надають REST і
  live-стрім поверх Redis snapshot/каналу:

   - `GET /smc-viewer/snapshot` → повний словник symbol → `SmcViewerState`;
   - `GET /smc-viewer/snapshot?symbol=SYM` → один `SmcViewerState` або 404 з
     `{ "error": "symbol_not_found", "symbol": SYM }`;
   - `WS /smc-viewer/stream?symbol=SYM` → відправляє `{"type": "snapshot"}` із поточним
     `SmcViewerState`, далі ретранслює `{"type": "update"}` з каналу
     `REDIS_CHANNEL_SMC_VIEWER_EXTENDED` тільки для обраного символу.

### Контракт `SmcViewerState` (мінімальний стабільний шар)

`SmcViewerState` - це агрегований стан для одного символу:

```jsonc
{
  "symbol": "XAUUSD",
  "price": 2412.5,
  "session": "London",
  "payload_ts": "2025-12-08T08:05:00+00:00",
  "payload_seq": 123,
  "schema": "smc_viewer_v1",
  "meta": {
    "ts": "2025-12-08T08:05:01+00:00",
    "seq": 123,
    "schema_version": "smc_state_v1",
    "fxcm": { "...": "див. FxcmMeta" }
  },
  "structure": {
    "trend": "up",
    "bias": "long",
    "range_state": "dev_up",
    "legs": [...],
    "swings": [...],
    "ranges": [...],
    "events": [...],
    "ote_zones": [...]
  },\n  "liquidity": {
    "amd_phase": "MANIP",
    "pools": [...],
    "magnets": [...]
  },
  "zones": {
    "raw": {
      "zones": [...]
    }
  },
  "fxcm": {
    "market_state": "open",
    "process_state": "streaming",
    "lag_seconds": 0.3,
    "next_open_utc": "2025-12-09T00:00:00+00:00",
    "session": {
      "name": "London",
      "seconds_to_close": 7200
    }
  }
}
```

Усі додаткові поля допускаються, але перелічені вище мають вважатися стабільним
мінімумом для веб-фронтенду й інших клієнтів.

### Приклад використання HTTP

```bash
# повний snapshot
curl http://127.0.0.1:8080/smc-viewer/snapshot

# конкретний символ
curl "http://127.0.0.1:8080/smc-viewer/snapshot?symbol=XAUUSD"
```

Тепер live-оновлення можна читати напряму через WebSocket (`ws://HOST:PORT/smc-viewer/stream?symbol=XAUUSD`).
Якщо потрібен сирий стрім, залишився відкритим канал `REDIS_CHANNEL_SMC_VIEWER_EXTENDED`.

### OHLCV API

Для фронтенду доступний окремий endpoint:

```text
GET /smc-viewer/ohlcv?symbol=xauusd&tf=1m&limit=500
```

- `symbol` — обов'язково, нижній регістр, як у UnifiedDataStore.
- `tf` — обов'язково, інтервал (`1m`, `5m`, `1h` тощо).
- `limit` — опційно, 1..2000 (дефолт 500).

Відповідь `200 OK`:

```json
{
  "symbol": "xauusd",
  "timeframe": "1m",
  "limit": 200,
  "bars": [
    {
      "time": 1765282800000,
      "open": 4201.15,
      "high": 4203.22,
      "low": 4199.96,
      "close": 4202.28,
      "volume": 123.45
    }
  ]
}
```

`time` передається у мілісекундах UNIX (close_time, якщо він є, інакше open_time). Приклад запиту:

```bash
curl "http://127.0.0.1:8080/smc-viewer/ohlcv?symbol=xauusd&tf=1m&limit=200"
```

### Інтеграція в `app/main.py`

- Якщо `UI_V2_ENABLED` увімкнено, під час запуску SMC рантайму (`app/main.py`) створюються три таски:
  broadcaster (`SmcViewerBroadcaster.run_forever`), HTTP-сервер (`ViewerStateHttpServer`) та
  WebSocket-сервер (`ViewerStateWsServer`).
- Увімкнення/вимкнення стеку контролюється ENV `UI_V2_ENABLED` (0/false — вимкнути).
- HTTP параметри можна перевизначити через `SMC_VIEWER_HTTP_HOST` / `SMC_VIEWER_HTTP_PORT`.
- WebSocket параметри керуються `SMC_VIEWER_WS_ENABLED`, `SMC_VIEWER_WS_HOST`, `SMC_VIEWER_WS_PORT`.
- Snapshot-ключ, з якого читає HTTP шар, можна задати через `SMC_VIEWER_SNAPSHOT_KEY`
  (за замовчуванням `REDIS_SNAPSHOT_KEY_SMC_VIEWER`).
