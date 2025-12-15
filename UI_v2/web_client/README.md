# Web-клієнт UI_v2

Простий браузерний viewer, який читає `SmcViewerState` через HTTP+WebSocket інтерфейси.

Цей README написаний як короткий “контекст-дамп” для архітектора: як піднімається UI_v2, які порти/ендпойнти, звідки беруться дані, які Redis-ключі/канали використовуються, CORS/безпека та runbook.

## Архітектура (високий рівень)

UI_v2 піднімається в рамках основного процесу пайплайна `python -m app.main` і складається з 3 незалежних сервісів + 1 конвертера стану:

- **Broadcaster SMC → viewer_state**: читає SMC state (snapshot + live) з Redis і будує `SmcViewerState`. Джерело: `ai_one:ui:smc_snapshot` + `ai_one:ui:smc_state`; вихід: `ai_one:ui:smc_viewer_snapshot` + `ai_one:ui:smc_viewer_extended`.
- **HTTP сервер (8080)**: роздає статику (цей web_client) та REST API: snapshot + OHLCV.
- **WebSocket сервер viewer_state (8081)**: live-стрім `SmcViewerState` (з Redis pub/sub каналу viewer_state).
- **WebSocket міст FXCM (8082)**: dev-стріми з Redis каналів FXCM (`fxcm:ohlcv`, `fxcm:price_tik`) у браузер.

Точка входу: `app/main.py::_launch_ui_v2_tasks()`.

## API

### HTTP (порт 8080)

Сервер також віддає статику з `UI_v2/web_client/` (корінь `/` → `index.html`).

- `GET /smc-viewer/snapshot` — повна мапа `symbol -> SmcViewerState`.
- `GET /smc-viewer/snapshot?symbol=SYM` — один `SmcViewerState` (404, якщо символ відсутній).

Додатково (якщо пайплайн передав `UnifiedDataStore`, тобто запущено стандартний runtime):

- `GET /smc-viewer/ohlcv?symbol=xauusd&tf=1m&limit=500` — історичні OHLCV з `UnifiedDataStore`.
  - `limit`: 1..2000 (за замовчуванням 500).
  - Відповідь: `{symbol, timeframe, limit, bars:[{time,open,high,low,close,volume}]}`.

Примітка: `GET /smc-viewer/stream` по HTTP не підтримується (WS живе на окремому порту; HTTP-роут повертає 501).

### WebSocket viewer_state (порт 8081)

- `WS /smc-viewer/stream?symbol=SYM` — одразу надсилає `{"type":"snapshot"}` з початковим станом, далі шле `{"type":"update"}` при кожному новому `SmcViewerState` із Redis-каналу.

### WebSocket FXCM міст (порт 8082)

Це dev-інтерфейс, щоб бачити live-бар `complete=false`/тіки без доступу до Redis:

- `WS /fxcm/ohlcv?symbol=XAUUSD&tf=1m` — прокидує повідомлення з Redis каналу `fxcm:ohlcv` (відфільтровані за `symbol`+`tf`).
- `WS /fxcm/ticks?symbol=XAUUSD` — прокидує повідомлення з Redis каналу `fxcm:price_tik` (відфільтровані за `symbol`).

Важлива примітка про volume та live-бар:

- `chart_adapter.js` підтримує volume-серію (histogram) і окрему live-volume серію.
- **Основний UI** (`index.html` + `app.js`) при обробці FXCM OHLCV WS-пакетів (`/fxcm/ohlcv`) прокидає `volume` у `setLiveBar(...)` (якщо `bar.volume` присутній у повідомленні) — тоді live-volume серія малюється.
- **Dev стенд** (`chart_demo.js`) навпаки передає `volume` і для history, і для live-бару, тому демонструє live-volume коректно.
- Історична volume-серія у прод-шляху береться з `GET /smc-viewer/ohlcv` (дані з `UnifiedDataStore`), а live-бар `complete=false` у `UnifiedDataStore` не зберігається.

Примітка про “як TradingView” у прод-режимі:

- Щоб у браузер приходили live-апдейти `complete=false` (і live-volume), потрібен канал доставки. За замовчуванням FXCM WS міст живе на `8082` і в публічному режимі не використовується.
- Мінімальний шлях без змін UDS/SMC: **reverse-proxy WebSocket `/fxcm/ohlcv` і `/fxcm/ticks` у same-origin** (nginx/Cloudflare tunnel) → тоді клієнт може підключатися до FXCM WS через основний домен.
- Для цього на фронті достатньо відкрити сторінку з параметрами: `?fxcm_ws=1&fxcm_ws_same_origin=1` (вмикає FXCM WS та примушує same-origin замість порту `8082`).

Окремий runbook (публічний домен через Cloudflared → nginx Docker):

- docs/runbook_tradingview_like_live_public_domain.md

HTTP використовується лише для початкового snapshot та OHLCV-запитів. Усі подальші оновлення приходять через WebSocket.

## Порти та змінні середовища

Дефолти (якщо змінні не задані):

- `UI_V2_ENABLED=0|1` — вмикає UI_v2 стек (інакше запускається legacy viewer).
- `SMC_VIEWER_HTTP_HOST=127.0.0.1`, `SMC_VIEWER_HTTP_PORT=8080` — HTTP (статика + REST).
- `SMC_VIEWER_WS_HOST=$SMC_VIEWER_HTTP_HOST`, `SMC_VIEWER_WS_PORT=8081`, `SMC_VIEWER_WS_ENABLED=0|1` — WS viewer_state.
- `FXCM_OHLCV_WS_HOST=$SMC_VIEWER_HTTP_HOST`, `FXCM_OHLCV_WS_PORT=8082`, `FXCM_OHLCV_WS_ENABLED=0|1` — WS міст для FXCM.
- `SMC_VIEWER_SNAPSHOT_KEY` — ключ snapshot для viewer_state (дефолт з `config.config.REDIS_SNAPSHOT_KEY_SMC_VIEWER`).

Redis-параметри беруться з `app/settings.py` (типово `REDIS_HOST`, `REDIS_PORT`).

## Redis: ключі та канали

У namespace `ai_one` (див. `config/config.py`):

- `ai_one:ui:smc_state` — pub/sub канал SMC state (джерело для broadcaster-а).
- `ai_one:ui:smc_snapshot` — ключ snapshot SMC state (cold-start для broadcaster-а).
- `ai_one:ui:smc_viewer_extended` — pub/sub канал viewer_state (вихід broadcaster-а; джерело WS 8081).
- `ai_one:ui:smc_viewer_snapshot` — ключ snapshot viewer_state (джерело HTTP /smc-viewer/snapshot).

FXCM (dev-міст, namespace тут не використовується):

- `fxcm:ohlcv` — pub/sub OHLCV (включно з `complete=false`).
- `fxcm:price_tik` — pub/sub тики (bid/ask/mid snapshot).
- `fxcm:status` — агрегований статус конектора (показується в UI через поля `meta.fxcm.*` у `SmcViewerState`, а не напряму з Redis).

## CORS та безпека

- HTTP відповіді додають `Access-Control-Allow-Origin: *` (дозволено будь-яке походження), а також `Allow-Headers: Content-Type`, `Allow-Methods: GET, OPTIONS`.
- Немає аутентифікації/авторизації, немає TLS.

Рекомендація для прод/демо:

- Тримати `SMC_VIEWER_HTTP_HOST=127.0.0.1` (локально) або закривати доступ мережевими правилами.
- Якщо потрібен віддалений доступ — виносити за reverse proxy з TLS і базовим доступом (auth), та обмежувати CORS під конкретний origin.

## Запуск

1. Запусти пайплайн:

   ```powershell
   # у корені репозиторію
   $env:UI_V2_ENABLED = "1"
   $env:SMC_VIEWER_HTTP_PORT = "8080"   # за бажанням
   $env:SMC_VIEWER_WS_PORT = "8081"     # за бажанням
   python -m app.main
   ```

2. Статичний сервер для фронтенду:

   Тепер UI роздається прямо з бекенду на `8080`, тому окремий `http.server` не потрібен.

   Відкрий браузер: `http://127.0.0.1:8080/?symbol=xauusd`.

   Якщо все ж хочеш запускати фронтенд окремо (dev/ізольовано), можна підняти простий статичний сервер:

   ```powershell
   cd C:\Aione_projects\smc_v1\UI_v2\web_client
   python -m http.server 9000
   ```

3. Якщо запустив окремий сервер, відкрий: `http://127.0.0.1:9000/?symbol=xauusd`.
   За замовчуванням клієнт працює в same-origin режимі (HTTP береться з `window.location.origin`, WS — з `ws://|wss://` + `window.location.host`).
   Dev FXCM WS міст (порт 8082) вимкнений у публічному режимі; дозволяється лише на `localhost/127.0.0.1` або з явним прапором `?fxcm_ws=1`.
   Якщо FXCM WS міст прокситься у same-origin (наприклад через nginx/Cloudflare tunnel), додай `?fxcm_ws=1&fxcm_ws_same_origin=1`.

4. На шапці є статусний банер: `Підключено / Перепідключення / Без стріму / Помилка` та останні `payload_ts` і `lag_seconds` із бекенду.

## Відображувані поля

- **Summary:** `symbol`, `price`, `session`, `structure.trend`, `structure.bias`, `structure.range_state`, `liquidity.amd_phase`, `meta.fxcm.market_state`, `meta.fxcm.process_state`, `meta.fxcm.lag_seconds`.
- **Structure events:** останні BOS/CHOCH (час, тип, напрям, ціна).
- **OTE zones:** `direction`, `role`, `ote_min`, `ote_max`.
- **Liquidity pools:** `type`, `role`, `price`, `strength`/`touch_count`.
- **Zones:** `zones.raw.zones` (тип, роль, межі ціни).

## Smoke-чекліст

1. `python -m app.main` → переконатися, що HTTP/WS сервери стартували без помилок.
2. `python -m http.server 9000` у `UI_v2/web_client/` → відкрити сторінку.
3. Переконатися, що після завантаження сторінка показує snapshot (ціни/тренд/FXCM статус).
4. Змінити символ у `<select>` — таблиці мають оновитись одразу.
5. Зупинити бекенд (`Ctrl+C` у `app.main`) — статус WebSocket зміниться на `Без стріму`, але останній snapshot лишається на екрані; кнопка «Перепідключити» відновить стрім після запуску бекенду.

## Dev chart playground

## Fullscreen графік: нотатки

Якщо знову з’являється проблема "у fullscreen графік пливе вниз" — дивись окремий документ:

- docs/ui_v2_fullscreen_chart_layout.md

## Mobile chart: «пливе вниз»

Якщо на мобільному в режимі **«Графік»** чарт «дрейфує вниз», це майже завжди пов’язано з нестабільною висотою viewport
(address bar/toolbar) та/або некоректним flex-контуром слота, куди переноситься `.card-chart`.

Канонічний фікс описано тут:

- docs/ui_v2_mobile_chart_drift_fix.md

- У каталозі лежить [dev_chart_playground.html](dev_chart_playground.html) — це ізольований стенд для швидких експериментів із `chart_adapter.js`.
- Playground не використовується у продакшн-шляху (`index.html` його не імпортує); запуск той самий, але відкриваємо `http://127.0.0.1:9000/dev_chart_playground.html?symbol=xauusd`.
- Playground бере історію через `GET /smc-viewer/ohlcv` та може слухати live через FXCM WS міст (локальний dev-інтерфейс на порту 8082; у публічному режимі не доступний і не прокситься).
- Нові фічі можна обкатувати тут, але перед релізом їх треба перенести в основний UI (`index.html + app.js + chart_adapter.js`).
