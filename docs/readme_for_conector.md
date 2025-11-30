# Увага! цей опис про FXCM Connector, який є окремим проєктом і репозиторієм та працює незалежно від SMC в іншому Python 3.7 середовищі

## Статичні перевірки

Для lint/type-check запусти:

```powershell
python -m pip install -r dev-requirements.txt
python -m mypy connector.py config.py sessions.py cache_utils.py
ruff check .
```

Ці команди бажано додати до CI/CD, щоб будь-який MR у `fxcm_connector` мав базову гарантію типів і стилю.

# FXCM Connector (Українська версія)

Легковаговий конектор для отримання OHLCV-барів від FXCM через ForexConnect API
з публікацією у Redis та локальним кешем. Репозиторій побудований для продакшн-
використання в AiOne_t: усі логі повідомляються українською, а логіка враховує
реальний торговий календар (24/5 + паузи + свята).

## Можливості

- Warmup та стрім хвилинних/п'ятихвилинних барів у Redis-канал `fxcm:ohlcv`.
- Окремий статус-канал `fxcm:market_status` з подіями `open/closed` і `next_open`.
- Локальний файловий кеш + метадані для швидкого старту й повторного запуску.
- Тонкий торговий календар (`sessions.py`) з перервами та святами.
- Health-check Redis та зрозумілі повідомлення, коли ринок закритий.
- Модульні тести, які емулюють ForexConnect/Redis без зовнішніх залежностей.

## Системні вимоги

- Python 3.7.x для бойового запуску (відповідає офіційним збіркам ForexConnect SDK).
- Python 3.10+ можна використовувати лише для локальних тестів (без реального SDK).
- ForexConnect SDK (Windows, офіційний дистрибутив FXCM) з доступним `pyfxconnect`.
- Redis 6.x+ (локально або віддалено).
- Pipenv/venv для ізоляції залежностей.

## Перший запуск

1. Скопіюй `.env` з прикладу (додай власні креденшали FXCM):

   ```bash

  cp .env.template .env  # шаблон вже у репо і не містить секретів

   ```

2. Створи віртуальне середовище під Python 3.7 та встанови залежності:

   ```powershell

  py -3.7 -m venv .venv_fxcm37
  .\.venv_fxcm37\Scripts\Activate.ps1
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt

   ```

3. Переконайся, що встановлено ForexConnect (DLL/pyfxconnect).
4. Запусти warmup у POC-режимі:

   ```powershell

  python connector.py  # або python -m connector

   ```

  ## Runbook для серверного деплою

  1. **Сервісний користувач і каталоги.** Створи окремого юзера (наприклад, `fxcm`) без shell-доступу. Код розмістити в `/opt/fxcm_connector`, `.env` + `runtime_settings.json` — у `/etc/fxcm_connector/<env>/`, кеш — у `/var/lib/fxcm_connector/<env>_cache`. Вкажи шлях до кешу в `config/runtime_settings.json` (`"cache": { "dir": "/var/lib/..." }`).
  2. **Залежності.** Збирання роби на тій же ОС, де стоїть ForexConnect. У CI перевір `pytest tests/test_stream.py tests/test_ingestor.py` + `python -m mypy ...`, на сервері виконай `pip install -r requirements.txt` у venv під Python 3.7.
  3. **Systemd-сервіс.** Рекомендуємо systemd unit із `Restart=on-failure`, окремим environment-файлом і вимкненим `TimeoutStopSec`, щоби конектор встиг коректно вийти. Приклад (`/etc/systemd/system/fxcm-connector.service`):

    ```ini
    [Unit]
    Description=FXCM Connector
    After=network-online.target redis.service

    [Service]
    Type=simple
    User=fxcm
    WorkingDirectory=/opt/fxcm_connector
    EnvironmentFile=/etc/fxcm_connector/prod/.env
    ExecStart=/opt/fxcm_connector/.venv_fxcm37/bin/python connector.py
    Restart=on-failure
    RestartSec=5s
    TimeoutStopSec=30s

    [Install]
    WantedBy=multi-user.target
    ```

  4. **Післярелізні перевірки.**
    - `journalctl -u fxcm-connector -f` — стеж, щоб були записи `warmup_cache` → `stream`.
    - `redis-cli SUBSCRIBE fxcm:heartbeat` — переконайся, що `last_bar_close_ms` рухається.
    - `curl http://127.0.0.1:9200/metrics | grep fxcm_stream_lag_seconds` — лаг <120 секунд під час торгів.
  5. **Оновлення без простою.** Розкачай нову версію поруч (наприклад, `/opt/fxcm_connector/releases/<sha>`), прогрій віртуальне середовище, переключи symlink і перезапусти systemd. Кеш знаходиться у `/var/lib/...`, тому warmup не виконується з нуля.

## Змінні середовища

| Назва | Значення за замовчуванням | Опис |
| --- | --- | --- |
| `FXCM_USERNAME` / `FXCM_PASSWORD` | – | Обов'язкові креденшали FXCM. |
| `FXCM_CONNECTION` | `Demo` | Тип з'єднання (Demo/Real). |
| `FXCM_HOST_URL` | `http://www.fxcorporate.com/Hosts.jsp` | Endpoint для ForexConnect. |
| `FXCM_REDIS_HOST` / `FXCM_REDIS_PORT` | `127.0.0.1` / `6379` | Налаштування Redis. |
| `FXCM_CACHE_ENABLED` | `1` | Вмикає файловий кеш (`cache/`). |
| `FXCM_METRICS_ENABLED` | `1` | Вмикає Prometheus-метрики `/metrics`. |
| `FXCM_METRICS_PORT` | `9200` | Порт HTTP-сервера метрик. |
| `FXCM_HEARTBEAT_CHANNEL` | `fxcm:heartbeat` | Redis-канал heartbeat-повідомлень. |
| `FXCM_HMAC_SECRET` | – | Якщо задано — додає `sig` (HMAC) до `fxcm:ohlcv`; пусте значення вимикає підпис. |
| `FXCM_HMAC_ALGO` | `sha256` | Алгоритм HMAC (`sha256`, `sha512`, тощо). |

Усі інші параметри (warmup/stream/кеш-шлях/POC) зберігаються в `config/runtime_settings.json`
і не повинні керуватися через ENV.

> ℹ️ **Секрети:** `.env.template` призначений лише для плейсхолдерів. Реальні значення `FXCM_USERNAME/FXCM_PASSWORD` зберігай у секрет-сторі (наприклад, Azure Key Vault, GitHub Actions secrets, Jenkins credentials). У CI/CD підставляй їх як змінні середовища і **ніколи** не коміть бойові дані в репозиторій.

## Runtime-конфіг (`config/runtime_settings.json`)

Нефатальні параметри (warmup, кеш, стрім, POC) тепер описані в JSON-файлі. Приклад:

```json
{
  "cache": {
    "dir": "cache",
    "max_bars": 3000,
    "warmup_bars": 1000
  },
  "stream": {
    "mode": 1,
    "poll_seconds": 5,
    "fetch_interval_seconds": 5,
    "publish_interval_seconds": 5,
    "lookback_minutes": 5,
    "config": "XAU/USD:m1,XAU/USD:m5"
  },
  "sample_request": {
    "symbol": "EUR/USD",
    "timeframe": "m1",
    "hours": 24
  },
  "backoff": {
    "fxcm_login": { "base_delay": 2, "factor": 2, "max_delay": 60 },
    "fxcm_stream": { "base_delay": 5, "factor": 2, "max_delay": 300 },
    "redis_stream": { "base_delay": 1, "factor": 2, "max_delay": 60 }
  }
}
```

- `stream.mode=0` → лише warmup, `1` → нескінченний стрім.
- `stream.config` може бути рядком (`"XAU/USD:m1,GBP/USD:m5"`) або масивом об'єктів `{ "symbol": "XAU/USD", "tf": "m1" }`.
- Для staging/prod роби копію файлу під час деплою (IaC/Ansible/Helm) — не потрібно плодити ENV.
- `poll_seconds` (або `fetch_interval_seconds`) визначає, як часто звертатися до FXCM.
- `publish_interval_seconds` задає мінімальний інтервал між публікаціями OHLCV у Redis. Обидва значення за замовчуванням — 5 секунд.
- `backoff` описує експоненційний backoff у секундах для логіну FXCM, стрім-циклу та реконекту Redis. `base_delay` — стартова пауза, `factor` — множник після кожної невдачі, `max_delay` — верхня межа. Якщо секцію пропустити, застосуються значення з прикладу вище.

> **Практика:**
>
> - Prod-запуски утримуй у діапазоні 2–5 секунд для `fxcm_login`, щоб не створювати зайве навантаження при помилках автентифікації.
> - `fxcm_stream.max_delay` ≥ 300 секунд дозволяє перечекати планові вікна простою без ручного рестарту.
> - `redis_stream` тримай коротшим (1–60 секунд), щоби публікації відновлювалися одразу після повернення Redis.

## Робочі режими

1. **Warmup-only:**

- `config/runtime_settings.json` → `"stream": { "mode": 0, ... }`
- Конектор логіниться, прогріває кеш, публікує warmup-пакет у Redis і завершується.

2. **Стрім:**

- `config/runtime_settings.json` → `"stream": { "mode": 1, ... }`
- Після warmup запускається `stream_fx_data`, який кожні `poll_seconds`
    (або `fetch_interval_seconds`) виконує `get_history` лише в межах торгових вікон
    й гарантує мінімальний інтервал `publish_interval_seconds` між публікаціями барів.

## Архітектура

- `config.py` — централізований парсер `.env`, який повертає `FXCMConfig` (включно з `SampleRequestSettings` для warmup ПOC).
- `connector.py` — CLI та вся бізнес-логіка. Головні сутності:
  - `HistoryCache`: керує CSV+JSON кешем.
  - `load_config`: читає ENV через `config.py` і повертає dataclass.
  - `_fetch_and_publish_recent`: поважає календар, м'яко обробляє «ринок закритий».
  - `publish_ohlcv_to_redis`: TypedDict-представлення OHLCV + JSON.
- `cache_utils.py` — серіалізація/cache merge.
- `sessions.py` — календар 24/5, технічні паузи, generate_request_windows.
- `tests/test_stream.py` — smoke-тест warmup + стрім через фейкові FXCM/Redis.

## Масштабування (Wave B)

### Горизонтальне шардінгування воркерів (B1a)

- **Що масштабуємо.** Основний тиск дають паралельні `get_history` та JSON-публікації в Redis. Найпростіше масштабувати кількістю *процесів*, кожен з яких обробляє підмножину `SYMBOL:TF`.
- **Профіль воркера.** Один процес = один логін у ForexConnect + власний Redis heartbeat. Для розподілу символів змінюй `config/runtime_settings.json` (секція `stream.config`) у кожному сервісі. Наприклад:

  ```jsonc
  // worker-a/runtime_settings.json
  {
    "stream": {
      "mode": 1,
      "config": "XAU/USD:m1,XAU/USD:m5"
    }
  }

  // worker-b/runtime_settings.json
  {
    "stream": {
      "mode": 1,
      "config": [
        { "symbol": "EUR/USD", "tf": "m1" },
        { "symbol": "GBP/USD", "tf": "m1" }
      ]
    }
  }
  ```

- **Координація.** Оркестратор (systemd, Supervisor, Nomad, K8s) тримає n процесів із власними `.env` або префіксами секретів. Для хаотичного рестарту достатньо стежити за heartbeat-каналом: якщо `last_bar_close_ms` стає «старішим» за 2 хвилини, перезапускаємо відповідний воркер.
- **Вимоги до кешу.** Кожен воркер отримує власний `cache.dir` у `runtime_settings.json` (наприклад, `cache/xau`), щоб уникнути гонок при оновленні CSV. Якщо диск спільний, задавай унікальний шлях через деплой-скрипт.
- **Бекоф і метрики.** Поточний backoff/Prometheus стек нічого не знає про сусідів, тому можна зшивати метрики через `job`/`instance` лейбл у Prometheus. Для процесного масштабування не потрібно міняти код конектора.
- **Перевірка, що шардинг працює.** Підпишись на Redis-канал і переконайся, що кожний воркер публікує лише свої символи. Якщо треба дублювання (актив/пасив), другий воркер має бути «гарячим» але без публікації (ввімкнути лише warmup) або публікувати в окремий канал.

### Тредовий варіант (B1b)

- ForexConnect офіційно підтримує кілька потоків читання, але SDK використовує глобальні структури, тому тред-пул в одному процесі — лише для POC.
- Якщо все ж потрібно, оберни `_fetch_and_publish_recent` у `ThreadPoolExecutor(max_workers=2-3)` та шард будь-які `stream_config` між чергами. Контролюй, щоб `get_history` викликався послідовно в межах одного символу (можна тримати `Lock` «per symbol»).
- Навіть із тредами залишай єдиний heartbeat/Prometheus екземпляр, але додавай лейбл `thread` в payload, щоб спростити діагностику.
- За замовчуванням рекомендуємо **процеси**, бо так простіше ізолювати збої ForexConnect та уникнути взаємного блокування GIL під час JSON-серіалізації.

## Торговий календар

`sessions.is_trading_time` фільтрує всі звернення до FXCM. Якщо ринок закритий або
FXCM повертає `PriceHistoryCommunicator is not ready`, стрім просто не публікує
нових барів, публікує `market_status` з `state=closed` + найближчим `next_open`
і логічно повідомляє, коли очікується наступне вікно торгів. Після появи нових
барів автоматично вирушає `state=open`, щоб UI прибрав банер.

### Зовнішні оверрайди

- Весь календар тепер живе у файлі `config/calendar_overrides.json`. Там можна задати
  масив `holidays`, список `daily_breaks`, а також `weekly_open_utc` / `weekly_close_utc`.

  ```json
  {
    "holidays": ["2025-12-31", "2026-01-02"],
    "daily_breaks": ["21:59-22:05", ["22:00", "22:15"]],
    "weekly_open_utc": "21:00",
    "weekly_close_utc": "22:30"
  }
  ```

- Усі середовища читають той самий JSON (без ENV). Якщо файл потрібно кастомізувати під конкретний контур,
  створюй його через deployment tooling (копія шаблону + зміни).
- Будь-які оверрайди логуються при старті: легше дебажити, який календар активний.

## Логування

- RichHandler з кольорами; всі повідомлення українською.
- Ключові журнали: логін/логоут, warmup, health-check Redis, попередження про
  закритий ринок, кількість барів у стрімі.

## Валідація та моніторинг

1. **Юніт-тести (без SDK):**

  ```powershell
  python -m unittest tests.test_config tests.test_stream
  ```

  `tests.test_config` гарантує, що `config/runtime_settings.json` коректно парсить секцію `backoff`, а `tests.test_stream` використовує заглушку `ForexConnect`, тож достатньо будь-якого Python 3.10+.

2. **Prometheus-метрики та heartbeat:**

- Якщо `FXCM_METRICS_ENABLED=1`, Prometheus-сервер доступний за `http://<host>:FXCM_METRICS_PORT/metrics` (за замовчуванням `9200`). Там можна моніторити кількість барів, лаг, статус ринку, лічильники помилок і timestamp останнього heartbeat.
- Гейдж `fxcm_stream_staleness_seconds{symbol,tf}` показує, скільки секунд минуло з моменту останнього `close_time` — якщо значення зростає під час відкритого ринку, значить стрім не отримує нові бари.
- Redis-heartbeat летить у канал `FXCM_HEARTBEAT_CHANNEL` (типово `fxcm:heartbeat`) з payload:

  ```json
  {
    "type": "heartbeat",
    "state": "warmup" | "warmup_cache" | "stream" | "idle",
    "ts": "2025-05-01T12:00:00+00:00",
    "last_bar_close_ms": 1746187200000
  }
  ```

  Якщо нових барів немає, `last_bar_close_ms` просто повторює попереднє значення — оркестратор бачить живий процес без нових даних. Стани означають: `warmup` — разовий POC/warmup через `fetch_history_sample`, `warmup_cache` — прогрів кешу перед стрімом, `stream` — основний цикл, `idle` — ринок закритий або FXCM тимчасово не готовий (демон просто чекає). Юніт-тести (`HeartbeatContractTest`) фіксують ці контракти.

3. **Траст до warmup/стріму:**

- Підпиши окремий термінал на `fxcm:ohlcv` (`redis-cli SUBSCRIBE fxcm:ohlcv`) і перевір, що `open_time` барів зростає без пропусків.
- Payload `fxcm:ohlcv` стабільний: `{"symbol":"XAUUSD","tf":"1m","bars":[{"open_time":...,"close_time":...,"open":...}]}` (або з додатковим `"sig"`, якщо ввімкнено HMAC).
- Відкат таймстемпів фільтрується на рівні publish data gate: усі бари з `open_time` ≤ останнього опублікованого значення безшумно відкидаються, тож дублікати не проходять навіть якщо FXCM повернув накладні вікна.
- Підпиши ще один на `fxcm:market_status`: у паузах приходить `state=closed` + `next_open_utc`, під час торгів — `state=open`.
- Контракт `fxcm:market_status`:

  ```json
  {
    "type": "market_status",
    "state": "open" | "closed",
    "ts": "2025-05-01T12:00:00+00:00",
    "next_open_utc": "2025-05-01T22:00:00+00:00" // тільки коли state=closed
  }
  ```

- У головному пайплайні порівняй warmup-пакет із еталонним джерелом (наприклад, TradingView) раз на зміну.
- Стеж за лагом між `_now_utc()` та `close_time` останнього бару; якщо він >2 хвилин поза вихідними — тригер для підтримки.

4. **Перевірка кешу:** після warmup у каталозі `cache/` з’являються CSV+META файли з максимальною кількістю рядків (див. `cache.warmup_bars` у `runtime_settings.json`).

## Підпис повідомлень (HMAC)

- `FXCM_HMAC_SECRET` порожній → нічого не змінюється, `sig` відсутній.
- Якщо секрет задано, конектор додає `"sig"` до кожного повідомлення в `fxcm:ohlcv`. Значення — hex HMAC, що рахується по основному тілу `{"symbol","tf","bars"}` (без `sig`) через `json.dumps(..., sort_keys=True, separators=(",", ":"))`, тож однакові дані завжди дають однаковий підпис.
- `FXCM_HMAC_ALGO` дозволяє вибрати алгоритм (`sha256` за замовчуванням). Непідтримувані назви автоматично фолбекнуться на `sha256`.
- Основний пайплайн поки ігнорує `sig`, але в наступному оновленні `fxcm_ingestor` зможе валідувати підпис і дропати сторонні повідомлення.

## Статичні перевірки

Для lint/type-check запусти:

```powershell
python -m pip install -r dev-requirements.txt
python -m mypy connector.py config.py sessions.py cache_utils.py
ruff check .
```

Ці команди бажано додати до CI/CD, щоб будь-який MR у `fxcm_connector` мав базову гарантію типів і стилю.

## Траблшутинг

- **`ForexConnect SDK не встановлено`:** переконайся, що конектор запущено під Python 3.7 і встановлено офіційні DLL. В іншому випадку `ForexConnect()` одразу підніме `RuntimeError`.
- **`PriceHistoryCommunicator is not ready`:** конектор автоматично переходить у
  режим очікування; див. лог «ринок закритий …» — там буде час наступного відкриття.
- **Немає публікацій у Redis:** перевір health-check у логах; за потреби вимкни
  Redis для локальної відладки (конектор все одно збереже warmup у кеш).
- **Файловий кеш не пишеться:** починаючи з версії 0.5, будь-яка помилка IO переводить кеш у режим read-only. У логах з’явиться `"Файловий кеш вимкнено через помилку IO"`, а метрика `fxcm_connector_errors_total{type="cache_io"}` збільшиться. Потік барів і Prometheus/Redis продовжать працювати з даними в пам’яті, але CSV треба відновити вручну (звільнити диск, змінити права та перезапустити сервіс).

## Подальший розвиток

- Додати heartbeat-пакет для підтвердження live-з'єднання.
- Підтримати adaptive backoff для повільних мереж.
- Поглибити тестове покриття (pytest + mypy/ruff у CI).
