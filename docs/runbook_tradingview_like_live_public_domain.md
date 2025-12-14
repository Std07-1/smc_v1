# RUNBOOK: TradingView-like live на публічному домені (Cloudflared → nginx Docker)

Ціль: на публічному URL (через Cloudflared → nginx Docker) графік працює “як TradingView”:

- всередині хвилини свічка рухається через `complete=false` (приблизно кожні ~250 мс або рідше через throttling),
- обсяг “росте” (liveVolume),
- на закритті хвилини `complete=true` замінює live без дубля,
- `complete=false` **не пишеться в UDS** (UDS/SMC не чіпаємо).

---

## A. Передумови (має бути увімкнено)

### A1) FXCM WS міст у бекенді (порт 8082)

ENV для запуску бекенду:

- `FXCM_OHLCV_WS_ENABLED=1`
- `FXCM_OHLCV_WS_HOST=0.0.0.0`
- `FXCM_OHLCV_WS_PORT=8082` (якщо не дефолт)

Пояснення: `0.0.0.0` потрібен, щоб контейнер `nginx` міг під’єднатись до `host.docker.internal:8082`.

### A2) nginx WS proxy (same-origin)

У [deploy/viewer_public/nginx.conf](../deploy/viewer_public/nginx.conf) має бути WS reverse-proxy для `/fxcm/*` на `host.docker.internal:8082`:

- `location /fxcm/ { proxy_pass http://host.docker.internal:8082; ... }`

Обов’язково (мінімум):

- `proxy_http_version 1.1`
- `proxy_set_header Upgrade $http_upgrade;`
- `proxy_set_header Connection "upgrade";`
- `proxy_read_timeout 3600s`
- `proxy_buffering off`

Перезапуск nginx контейнера:

- `cd C:\Aione_projects\smc_v1\deploy\viewer_public`
- `docker compose up -d --force-recreate nginx`

### A3) UI перемикачі

- `?fxcm_ws=1` — вмикає FXCM WS на non-local домені.
- `?fxcm_ws_same_origin=1` — база WS = `wss://<домен>` (тобто `wss://<домен>/fxcm/...`, а не прямий `:8082`).

---

## B. Smoke test (що дивитись у браузері)

Відкрити:

- `https://<домен>/?symbol=xauusd&fxcm_ws=1&fxcm_ws_same_origin=1`

DevTools → Network → WS → відкрити `.../fxcm/ohlcv?...` → Frames:

- є `bars[0].complete === false`
- є `bars[0].volume > 0` (або хоча б не `0` стабільно)

Візуально (на графіку):

- свічка рухається всередині хвилини
- live-обсяг “росте”
- на close хвилини `complete=true` замінює live без дубля

---

## C. Якщо “не як TradingView” — швидка діагностика (дерево причин)

### C1) WS не підключається / 404 / handshake fail

- nginx: неправильний `location` або `proxy_pass` (часто проблема зі слешем `/`)
- немає `Upgrade/Connection: upgrade`
- бекенд 8082 слухає тільки `127.0.0.1`, а не `0.0.0.0`

### C2) WS підключився, але Frames порожні

- WS міст не підписався на Redis канали (дивись логи бекенду)
- невірні query params `symbol/tf` або UI відфільтровує не той символ/таймфрейм

### C3) Frames є, але тільки `complete=true`

- конектор ще не публікує live `complete=false` (перевір логи tick-agg)
- tick-agg не активний/не там, де очікуєш (символ/TF)

### C4) `complete=false` є, але `volume = 0`

- live-бар з конектора не містить `volume` (або воно нульове)
- у UI не підхопився новий `app.js` (кеш Cloudflare/браузера). Перевір у Sources, що в `handleOhlcvWsPayload()` є `volume: Number(bar.volume ?? 0)`.

### C5) Frames коректні, але графік не рухається

- UI throttling/рендер ок, але фільтр `symbol/TF` у `handleOhlcvWsPayload()` відсікає (не співпадає `currentSymbol/currentTf`)
- приходить не `1m/5m`, а інший TF

---

## D. Маленькі “правильні” покращення (не обов’язково, але корисно)

- `LIVE: ON/OFF` індикатор (ON якщо бачили `complete=false` за останні ~5s).
- throttle інтервал у конекторі зробити конфігурованим (0.2–0.5s, дефолт 0.25s).
