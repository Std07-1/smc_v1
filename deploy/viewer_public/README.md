# Public Viewer (UI_v2) на ПК через Docker + Cloudflare Quick Tunnel

Мета: дати 1 користувачу публічний **read-only** доступ до UI_v2 з інтернету **без VPS**.

Архітектура:

- Бекенд запускається **на хості (Windows)**: `python -m app.main` + UI_v2 HTTP/WS.
- Docker піднімає:
  - `nginx` (локально слухає `127.0.0.1:8088`) — allowlist + rate-limit + WS proxy.
  - `cloudflared` — піднімає Quick Tunnel і публікує `http://nginx:80` назовні.

Зовні відкритий **лише tunnel**. Жодних Redis/портів напряму назовні.

TL;DR “TradingView-like live” (жива свічка + live-volume):

- Бекенд: `FXCM_OHLCV_WS_ENABLED=1`, `FXCM_OHLCV_WS_HOST=0.0.0.0`.
- nginx: має проксити `/fxcm/*` (WS) → `http://host.docker.internal:8082`.
- Відкрити: `https://<random>.trycloudflare.com/?symbol=xauusd&fxcm_ws=1&fxcm_ws_same_origin=1`.
- Детально: `docs/runbook_tradingview_like_live_public_domain.md`.

## 1) Запуск бекенду (Windows PowerShell)

Важливо: щоб Docker-контейнер `nginx` міг ходити на `host.docker.internal:8080/8081`, процеси на Windows мають слухати `0.0.0.0`.

```powershell
# у корені репозиторію
$env:UI_V2_ENABLED = "1"
$env:SMC_VIEWER_HTTP_HOST = "0.0.0.0"
$env:SMC_VIEWER_WS_HOST = "0.0.0.0"
$env:SMC_VIEWER_WS_ENABLED = "1"

# DEV FXCM міст у публічному режимі не потрібен
$env:FXCM_OHLCV_WS_ENABLED = "0"

python -m app.main
```

Локальна перевірка (на хості):

- `http://127.0.0.1:8080/`
- `http://127.0.0.1:8080/smc-viewer/snapshot`

## 2) Запуск публікації (Docker + Quick Tunnel)

```powershell
cd deploy\viewer_public
docker compose up -d

# Переконайся, що контейнери не в restart-loop
docker compose ps
```

Отримати публічний URL (видається автоматично як `https://*.trycloudflare.com`):

```powershell
docker logs -f smc_viewer_public_cloudflared
```

Приклад: `https://<random>.trycloudflare.com/?symbol=xauusd`

Застереження: URL **тимчасовий** і змінюється при рестарті `cloudflared`. Доступ **публічний, без auth**.

Якщо щойно міняв(ла) `nginx.conf`/`docker-compose.yml`, перезапусти контейнери:

```powershell
docker compose down
docker compose up -d
docker compose ps
```

## 3) Smoke-check

### 3.1 Локально через nginx

- Відкрити: `http://127.0.0.1:8088/`
- Перевірити JSON:
  - `http://127.0.0.1:8088/smc-viewer/snapshot`
  - `http://127.0.0.1:8088/smc-viewer/ohlcv?symbol=xauusd&tf=1m&limit=50`

Швидкий smoke через curl:

```powershell
curl -I http://127.0.0.1:8088/
curl http://127.0.0.1:8088/smc-viewer/snapshot?symbol=xauusd
```

### 3.2 WebSocket upgrade

- Відкрити DevTools → Console і виконати:

```javascript
const ws = new WebSocket("ws://127.0.0.1:8088/smc-viewer/stream?symbol=xauusd");
ws.onopen = () => console.log("WS open");
ws.onmessage = (e) => console.log("WS msg", e.data.slice(0, 200));
ws.onclose = () => console.log("WS close");
```

Очікування: `WS open`, далі прилітають повідомлення `snapshot/update`.

### 3.3 Публічний URL

URL дає Cloudflare (через ваш tunnel). Відкрий його в браузері — UI_v2 має:

- підтягнути snapshot/ohlcv по same-origin
- відкрити WS стрім по тому ж домену

## Політики allowlist/rate-limit

`nginx` проксить **лише**:

- `GET /` → `http://host.docker.internal:8080/`
- `GET *.(js|css|ico|png|svg|map|woff2?)` → `http://host.docker.internal:8080`
- `GET /smc-viewer/snapshot` → `http://host.docker.internal:8080/smc-viewer/snapshot` (rate-limit)
- `GET /smc-viewer/ohlcv` → `http://host.docker.internal:8080/smc-viewer/ohlcv` (rate-limit)
- `GET /smc-viewer/stream` (WS upgrade) → `http://host.docker.internal:8081/smc-viewer/stream` (timeout 3600s)
- `/fxcm/*` (WS upgrade) → `http://host.docker.internal:8082/fxcm/*` (timeout 3600s; proxy_buffering off)

Все інше повертає `404`.

Якщо потрібна “жива” свічка (complete=false) і live-volume у браузері (поведінка “як TradingView”), мінімальний шлях без змін UDS/SMC:

- Увімкнути FXCM WS міст у бекенді: `FXCM_OHLCV_WS_ENABLED=1`.
- Виставити `FXCM_OHLCV_WS_HOST=0.0.0.0`, щоб контейнер `nginx` міг під'єднатись до хоста через `host.docker.internal:8082`.
- Переконатися, що в [deploy/viewer_public/nginx.conf](deploy/viewer_public/nginx.conf) є `location /fxcm/ { ... proxy_pass ...:8082; }`.
- У браузері відкривати UI з прапорами: `?symbol=xauusd&fxcm_ws=1&fxcm_ws_same_origin=1`.

Smoke URL:

- `https://<ваш-домен>/?symbol=xauusd&fxcm_ws=1&fxcm_ws_same_origin=1`

## Troubleshooting (швидко)

Окремий runbook для режиму “як TradingView” (live свічка + live-volume через `/fxcm/*`):

- docs/runbook_tradingview_like_live_public_domain.md

- `connection refused` з nginx → перевір, що бекенд слухає `0.0.0.0` (а не `127.0.0.1`) і що UI_v2 реально стартував (HTTP:8080, WS:8081).
- UI відкрився, але “порожньо”/без стилів → це майже завжди 404 на статику; перевір у Network, що `app.js/styles.css/chart_adapter.js` віддаються через `http://127.0.0.1:8088/`.
- `/smc-viewer/snapshot` 404 через nginx → allowlist блокує зайві шляхи; перевір точний шлях `/smc-viewer/snapshot` (без слеша в кінці).
- WS не апгрейдиться → перевір `ws://127.0.0.1:8088/smc-viewer/stream?symbol=xauusd` і що `SMC_VIEWER_WS_ENABLED=1`.
- Cloudflared не стартує/немає URL → перевір `CF_TUNNEL_TOKEN` у `.env` та логи: `docker compose logs -f cloudflared`.
