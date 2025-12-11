# Web-клієнт UI_v2

Простий браузерний viewer, який читає `SmcViewerState` через HTTP+WebSocket інтерфейси.

## API

- `GET /smc-viewer/snapshot` — повна мапа `symbol -> SmcViewerState`.
- `GET /smc-viewer/snapshot?symbol=SYM` — один `SmcViewerState` (404, якщо символ відсутній).
- `WS /smc-viewer/stream?symbol=SYM` — одразу повертає `{"type": "snapshot"}` з початковим станом, далі шле `{"type": "update"}` при кожному новому `SmcViewerState` із Redis-каналу.

HTTP використовується лише для початкового snapshot та OHLCV-запитів. Усі подальші оновлення приходять через WebSocket.

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

   ```powershell
   cd C:\Aione_projects\smc_v1\UI_v2\web_client
   python -m http.server 9000
   ```

3. Відкрий браузер: `http://127.0.0.1:9000?symbol=xauusd`. За потреби змінюй `HTTP_BASE_URL` / `WS_BASE_URL` у `app.js`.

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

- У каталозі лежить [dev_chart_playground.html](dev_chart_playground.html) — це ізольований стенд для швидких експериментів із `chart_adapter.js`.
- Playground не використовується у продакшн-шляху (`index.html` його не імпортує); запуск той самий, але відкриваємо `http://127.0.0.1:9000/dev_chart_playground.html?symbol=xauusd`.
- Нові фічі можна обкатувати тут, але перед релізом їх треба перенести в основний UI (`index.html + app.js + chart_adapter.js`).
