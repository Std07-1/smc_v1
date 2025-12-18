# RUNBOOK (Windows): Cloudflare Named Tunnel + швидкий дебаг 502 (SMC UI)

Ціль: стабільний прод-домен `https://aione-smc.com` (apex + www) через **named tunnel** → `http://127.0.0.1:80` (nginx) → UI_v2 (8080/8081).

## 3 команди (швидка діагностика)

1) Чи живий tunnel-сервіс

```powershell
Get-Service *cloudflared*
```

Очікування: `Status = Running`.

2) Чи живий origin (nginx на 80)

```powershell
curl -I http://127.0.0.1:80/
```

Очікування: `200` (або інший 2xx).

3) Чи живий бекенд за reverse-proxy (JSON)

```powershell
curl http://127.0.0.1:80/smc-viewer/snapshot?symbol=XAUUSD
```

Очікування: валідний JSON (не HTML з 502/404).

## Якщо 502 на домені (дуже коротко)

- Якщо **curl на 127.0.0.1:80** теж падає → проблема локально (nginx/Docker/бекенд), Cloudflare тут ні до чого.
- Якщо **127.0.0.1:80 OK**, але `https://aione-smc.com` дає 502 → перевір в Cloudflare Zero Trust:
  - Tunnel існує і активний (Connected).
  - Public Hostnames налаштовані:
    - `aione-smc.com` → `http://127.0.0.1:80`
    - `www.aione-smc.com` → `http://127.0.0.1:80`
  - DNS записи створені/проксійовані (Cloudflare зазвичай додає їх автоматично, залежить від UI).

## WS smoke (на 1 хв)

У браузері DevTools → Network → WS:

- `wss://aione-smc.com/smc-viewer/stream?symbol=XAUUSD` має тримати з'єднання.
- (якщо live увімкнено) `wss://aione-smc.com/fxcm/ohlcv?symbol=XAUUSD&tf=1m` має слати бари (включно `complete=false`).
