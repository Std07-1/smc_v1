# smc_v1 (SMC pipeline + UI_v2, FXCM через Redis)

`smc_v1` — це рантайм SMC-пайплайна (structure/liquidity/zones) з read-only UI_v2.
Дані FXCM надходять **через Redis** від зовнішнього конектора (`fxcm_connector`, Python 3.7).

Поточний main-потік запуску `python -m app.main` — **SMC-only** (legacy-шар у цьому репо не є основним шляхом).

---

## Швидкі посилання (SSOT)

- Документація (TOC): `docs/README.md`
- SMC-core overview: `docs/smc_core_overview.md`
- FXCM інтеграція/канали: `docs/fxcm_integration.md`, `docs/fxcm_contract_audit.md`
- Legacy/пояснення рантайм-потоку `app.main`: `docs/stage1_pipeline.md` (історичний документ)

---

## Архітектура (скорочено)

- **app/** — entrypoint (`app/main.py`) + runtime orchestration (SMC цикл, UI_v2, телеметрія).
- **data/** — `UnifiedDataStore` + Redis listeners:
  - `fxcm:ohlcv` → `UnifiedDataStore.put_bars()`
  - `fxcm:price_tik` → live bid/ask/mid кеш
  - `fxcm:status` → `FxcmFeedState` для UI/SMC
- **smc_core/** + **smc_structure/** + **smc_liquidity/** + **smc_zones/** — детермінований SMC pipeline, вихід: `SmcHint`.
- **core/** — SSOT для I/O (серіалізація/час) + контракти (`core/contracts/*`).
- **UI_v2/** — HTTP+WS сервери для read-only перегляду (same-origin paths для фронту).
- **UI/** — експериментальні/консольні клієнти (не обовʼязкові для прод-UI_v2).
- **deploy/** — systemd/nginx/Cloudflare runbooks, Docker-периметр для Windows.

---

## Ключові можливості

- Єдине джерело даних (RAM ↔ Redis ↔ JSONL snapshots) через `UnifiedDataStore`.
- SMC-core з контрактами (Contract-first): `SmcHint` + `schema_version`.
- Stage6 (4_2 vs 4_3): детермінована класифікація «сценарію після sweep» з чесним `UNCLEAR` + explain (`why[]`), та окремим stable-станом після анти-фліпу (stable/raw/pending для UI).
- UI_v2: same-origin HTTP+WS (зручний для Cloudflare/nginx).
- Стійкість до тимчасового падіння Redis (reconnect + backoff) у FXCM listeners та UI_v2 runners.
- QA-утиліти для локального прогону SMC на історії: `tools/smc_snapshot_runner.py`.
- QA Stage6 (довіра до 4_2/4_3): `tools/qa_stage6_scenario_stats.py` (Markdown звіт у `reports/`).

## Потік даних (рантайм)

- `app.main`:
  - робить bootstrap `UnifiedDataStore` з `config/datastore.yaml`;
  - warmup з `datastore/*.jsonl` (best-effort, щоб швидше стартувати);
  - запускає Redis listeners (`fxcm:ohlcv`, `fxcm:price_tik`, `fxcm:status`);
  - крутить SMC цикл (`smc_producer`) і публікує агрегований стан у Redis для UI;
  - (опційно) піднімає UI_v2 HTTP/WS + FXCM WS bridge.

Деталі з контрактами/каналами: `docs/fxcm_integration.md`, `docs/fxcm_contract_audit.md`.

---

## Системні вимоги

- Python **3.11.9** (див. `runtime.txt`).
- Redis 6+ (локально чи віддалено) з правами на читання/запис.
- Зовнішній FXCM конектор (окремий процес, Python 3.7) має публікувати канали в Redis.
- Залежності з `requirements.txt` (рекомендується окреме віртуальне середовище).

---

## Швидкий старт

```powershell
cd smc_v1

python -m venv .venv
.\.venv\Scripts\activate

pip install --upgrade pip
pip install -r requirements.txt
```

---

## Налаштування середовища

1) Налаштуй `config/datastore.yaml` (base_dir/namespace, політики snapshot'ів).

2) ENV для рантайму задавай явними змінними:

- **Linux/VPS (systemd):** через `/etc/smc/smc.env` (див. шаблон `deploy/systemd/smc.env.example`).
- **Windows/dev:** через `$env:...` у PowerShell.

Мінімум (SMC + UI_v2):

- `REDIS_HOST`, `REDIS_PORT`
- `UI_V2_ENABLED=1`
- `SMC_VIEWER_HTTP_HOST/PORT` (типово `127.0.0.1:8080`)
- `SMC_VIEWER_WS_HOST/PORT`, `SMC_VIEWER_WS_ENABLED=1` (типово `127.0.0.1:8081`)
- `FXCM_OHLCV_WS_HOST/PORT`, `FXCM_OHLCV_WS_ENABLED` (типово `127.0.0.1:8082`)

3) Бізнес-константи/параметри пайплайна не ховай в ENV — вони мають бути в `config/config.py` або в явних параметрах (див. docs/architecture/principles.md).

---

## Запуск сервісів

- **Основний рантайм** (FXCM ingest → SMC → UI_v2):

  ```powershell
  python -m app.main
  ```

- **Консольний/експериментальний viewer (опційно)**:

  ```powershell
  python -m UI.ui_consumer_experimental_entry
  ```

  Це legacy-інструмент для діагностики. Для прод-перегляду використовуємо UI_v2 (HTTP/WS).

- **QA/SMC snapshot runner** — детермінований прогон SMC на історичній вибірці без
  legacy-шару:

  ```powershell
  python -m tools.smc_snapshot_runner XAUUSD --tf 5m --extra 15m 1h --limit 500
  ```

---

## Режими деплою (SSOT)

Мета: швидко розуміти, **які саме deploy-файли є джерелом істини** для кожного сценарію.

- **VPS (prod), Cloudflare DNS (A-record) → nginx → SMC**
  - SSOT: `deploy/systemd/smc.service`, `deploy/systemd/smc.env.example`, `deploy/nginx/smc_ui_v2.conf`
  - Суть: Cloudflare (edge) ходить до origin напряму (HTTP:80 або HTTPS:443 з Origin CA).

- **VPS (prod), Cloudflare Tunnel → nginx → SMC**
  - SSOT: `deploy/cloudflare_tunnel/README.md`, `deploy/cloudflare_tunnel/cloudflared.ingress.example.yml`, `deploy/nginx/smc_ui_v2.conf`
  - Суть: тунель підключається до nginx на VPS (origin), зовнішній доступ тільки через Tunnel.

- **Windows (без VPS), Tunnel → nginx (Docker) → UI_v2 на хості**
  - SSOT: `deploy/viewer_public/README.md`, `deploy/viewer_public/docker-compose.yml`, `deploy/viewer_public/nginx.conf`
  - Суть: бекенд крутиться на Windows, nginx у Docker робить allowlist/rate-limit і same-origin для домену.

---

## VPS quickstart (Ubuntu, systemd + nginx + Redis)

Ціль: один VPS, два локальні сервіси (Redis + SMC), публічний доступ через Cloudflare → nginx → UI_v2.

- SSOT файли: `deploy/systemd/smc.service`, `deploy/systemd/smc.env.example`, `deploy/nginx/smc_ui_v2.conf`.

Мінімальний план (вручну, як чекліст):

```bash
# 1) Пакети
sudo apt-get update
sudo apt-get install -y redis-server nginx

# 2) ENV для сервісу
sudo mkdir -p /etc/smc
sudo cp deploy/systemd/smc.env.example /etc/smc/smc.env

# 3) systemd unit
sudo cp deploy/systemd/smc.service /etc/systemd/system/smc.service
sudo systemctl daemon-reload
sudo systemctl enable --now redis-server smc

# 4) nginx same-origin proxy
sudo cp deploy/nginx/smc_ui_v2.conf /etc/nginx/sites-available/smc_ui_v2.conf
sudo ln -sf /etc/nginx/sites-available/smc_ui_v2.conf /etc/nginx/sites-enabled/smc_ui_v2.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Примітки:

- Якщо Cloudflare ходить до origin по HTTPS (Full/Strict) — використовуй `listen 443` блок у `deploy/nginx/smc_ui_v2.conf` і встанови Cloudflare Origin CA сертифікат на VPS.
- Якщо замість A-record використовується Tunnel — див. `deploy/cloudflare_tunnel/README.md`.

## Прод: Cloudflare Tunnel (Windows) → nginx (Docker) → UI_v2 (коротко)

Ціль: same-origin доставка під **основним доменом** `https://aione-smc.com` (альтернатива: `https://www.aione-smc.com`), щоб і HTTP, і WS працювали "як TradingView".

Детальний runbook:

- `deploy/viewer_public/README.md`
- `docs/runbook_cloudflare_named_tunnel_windows.md`
- `docs/runbook_tradingview_like_live_public_domain.md`

Швидкий шлях:

1) Запусти UI_v2 локально (бекенд на хості)

```powershell
$env:UI_V2_ENABLED = "1"
$env:SMC_VIEWER_HTTP_HOST = "0.0.0.0"
$env:SMC_VIEWER_HTTP_PORT = "8080"
$env:SMC_VIEWER_WS_HOST = "0.0.0.0"
$env:SMC_VIEWER_WS_PORT = "8081"
$env:SMC_VIEWER_WS_ENABLED = "1"

python -m app.main
```

2) Запусти nginx (Docker Desktop) як same-origin reverse-proxy на `80`

```powershell
cd deploy\viewer_public
docker compose up -d
docker compose ps
```

3) Smoke-check локально (щоб не ловити 502 наосліп)

```powershell
cd ..\..
.\tools\smoke_same_origin.ps1
```

4) Cloudflare Zero Trust → Tunnel → Public Hostname

- `aione-smc.com` → `http://127.0.0.1:80`
- `www.aione-smc.com` → `http://127.0.0.1:80`

Очікування:

- `https://aione-smc.com/` відкриває UI
- `https://aione-smc.com/smc-viewer/snapshot?symbol=XAUUSD` повертає JSON
- `wss://aione-smc.com/smc-viewer/stream?symbol=XAUUSD` тримає з'єднання (timeouts виставлені в nginx)

Live (FXCM OHLCV/ticks) через same-origin (prod-дефолт):

- Форс-URL (якщо треба явно): `https://aione-smc.com/?symbol=xauusd&tf=1m&fxcm_ws=1&fxcm_ws_same_origin=1`
- Ручне вимкнення live: `https://aione-smc.com/?fxcm_ws=0`

Smoke-check live у DevTools:

- Network → WS має з'явитися `wss://aione-smc.com/fxcm/ohlcv?symbol=XAUUSD&tf=1m`
- У повідомленнях мають прилітати бари, включно з `complete=false` (жива свічка всередині хвилини)

---

## Тестування

Використовуємо pytest без зовнішніх сервісів (дані мокаються локально):

```powershell
python -m pytest tests -q
```

Audit рейок/SSOT (дефолтно лише production surface): `python tools/audit_repo_report.py` (повний інвентар: `--include-tests --include-tools`).

Таргетні тести:

- `tests/test_smc_structure_basic.py`, `tests/test_smc_ote_basic.py` — структура.
- `tests/test_smc_liquidity_basic.py`, `tests/test_smc_sfp_wick.py`,
  `tests/test_smc_amd_phase.py` — ліквідність та AMD FSM.
- `tests/test_smc_liquidity_bridge.py`, `tests/test_smc_core_contracts.py` — API/bridge.
- `tests/test_smc_stage6_scenario.py`, `tests/test_smc_stage6_hysteresis.py` — Stage6 рішення + анти-фліп.

QA Stage6 (приклад, PowerShell):

```powershell
; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" -m tools.qa_stage6_scenario_stats \
  --path datastore/xauusd_bars_5m_snapshot.jsonl --steps 500 --warmup 220 \
  --horizon-bars 60 --tp-atr 1.0 --sl-atr 1.0 --out reports/stage6_stats_xauusd_h60_v2.md
```

---

## Структура директорій (скорочено)

| Шлях | Призначення |
| --- | --- |
| `app/` | Точка входу (`main.py`), bootstrap, SMC runtime orchestration, helpers |
| `config/` | Конфіг SMC/runtime, datastore.yaml |
| `data/` | UnifiedDataStore, WS worker, raw data утиліти |
| `UI_v2/` | Read-only UI (HTTP + WS + FXCM WS bridge) |
| `smc_core/`, `smc_structure/`, `smc_liquidity/` | SMC pipeline + типи |
| `UI/` | Публікація стану та консольний клієнт |
| `docs/` | Актуальна SMC документація |
| `tools/` | Snapshot runner, дослідницькі скрипти |
| `tests/` | Pytest-набір для верифікації контрактів |

---

## Ліцензія

**Proprietary License.** Будь-яке використання чи розповсюдження можливе лише за
попередньою письмовою згодою власника (див. `LICENSE.md`).

---

## Контакти

- **Власник:** Stanislav (Std07-1)
- **Email:** [Viktoriakievstd1@gmail.com](mailto:Viktoriakievstd1@gmail.com),
  [Stdst07.1@gmail.com](mailto:Stdst07.1@gmail.com)
- **GitHub:** [Std07-1](https://github.com/Std07-1)
- **Telegram:** [@Std07_1](https://t.me/Std07_1)

Оновлено: 21.12.2025
