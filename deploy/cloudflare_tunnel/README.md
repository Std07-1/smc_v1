# Cloudflare Tunnel → nginx → UI_v2 (same-origin)

Мета: щоб браузер бачив **один origin** `https://aione-smc.com` для HTTP і WS.

## Компоненти

- UI_v2 HTTP (статичний UI + REST): `127.0.0.1:8080`
- UI_v2 WS (viewer_state): `127.0.0.1:8081`
- nginx reverse-proxy (origin для Cloudflare): `127.0.0.1:80`

## Варіант A (рекомендовано): Cloudflare Tunnel → nginx

1) Підніміть nginx на origin і підключіть конфіг

- Використайте конфіг: `deploy/nginx/aione-smc.conf`
- Переконайтесь, що nginx слухає `80`.

2) Cloudflare Zero Trust → Tunnels

- Створіть/виберіть Tunnel.
- Додайте **Public Hostname**:
  - `aione-smc.com` → service `http://127.0.0.1:80`
  - (альтернатива) `www.aione-smc.com` → service `http://127.0.0.1:80`

3) Перевірка (smoke-check)

- Відкрийте `https://aione-smc.com/` (або `https://www.aione-smc.com/`)
- DevTools → Network:
  - `GET /smc-viewer/snapshot?symbol=XAUUSD` має повертати JSON
  - `WS wss://aione-smc.com/smc-viewer/stream?symbol=XAUUSD` має підключитись і слати `snapshot/update`

## Один тунель (apex + www) “поставив і забув”

Якщо не хочете Quick Tunnel і потрібен стабільний прод-домен:

- Приклад `cloudflared` ingress-конфіга: `deploy/cloudflare_tunnel/cloudflared.ingress.example.yml`
- Сенс: `aione-smc.com` і `www.aione-smc.com` → `http://127.0.0.1:80`

## Примітка про FXCM live (OHLCV/ticks)

FXCM WS міст (порт `8082`) задуманий як dev-інтерфейс.

Якщо ви все ж хочете live-свічки через same-origin:

- Увімкніть на бекенді: `FXCM_OHLCV_WS_ENABLED=1`
- Додайте в nginx `location /fxcm/ { ... }` (у конфігу вже є готовий блок, але закоментований)
- Відкрийте UI з параметрами: `?fxcm_ws=1&fxcm_ws_same_origin=1`

Якщо `FXCM_OHLCV_WS_ENABLED=0`, то `/fxcm/*` WS підключення приречені падати — це очікувана поведінка.
