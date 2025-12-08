# smc_v1 (AiOne_t Stage1 + SMC core)

`smc_v1` — це оперативний стек Stage1 для моніторингу FX (FXCM feed) із вбудованим
SMC-core (structure + liquidity + AMD). Проєкт фокусується на стабільній доставці
джерельних даних (UnifiedDataStore), детермінованій структурній аналітиці та
телеметрії для наступних шарів (Stage2/UI/Fusion).

---

## Архітектура

- **data/** — `UnifiedDataStore`, WS-стрімер (`WSWorker`) та допоміжні утиліти.
- **stage1/** — моніторинг активів (AssetMonitorStage1 + FX гейти), генерація сирих
  сигналів і станів для UI.
- **smc_core/** + **smc_structure/** + **smc_liquidity/** — детермінований pipeline,
  що повертає `SmcHint` (structure/liquidity/zones/signals/meta).
- **UI/** — публікація агрегованого стану в Redis та консольний consumer.
- **tools/** — `smc_snapshot_runner` і дослідницькі скрипти для QA.
- **tests/** — pytest-набір для SMC (structure, liquidity, AMD, bridge, input adapter).

> ⚠️ Stage1 моніторинг і тригери вважаються закритим шаром: не додаємо нові фічі, не змінюємо пороги чи payload без окремого наказу. Поточна робота концентрується на SMC-core (structure/liquidity/zones).

Документацію по SMC знайдеш у `docs/smc_core_overview.md`, `docs/smc_structure.md`,
`docs/smc_liquidity.md`. Детальний контракт інтеграції FXCM описано в
`docs/fxcm_integration.md`.

---

## Ключові можливості

- Єдине джерело правди (Redis + JSONL snapshots) через `UnifiedDataStore`.
- FXCM стрім + Stage1 тригери (vol spike, RSI, VWAP, breakout, volatility) **заморожені**: не розвиваємо їх без окремої директиви, фокус лише на SMC-core.
- SMC-core з зафіксованими контрактами (structure/liquidity/zones/meta + bridge до
  Stage2).
- Довготривала пам'ять BOS/CHOCH із діагностичним логуванням (StructureEventHistory),
  яку використовують OB_v1 та майбутні зони.
- Нативний UI канал (Redis pub/sub) для моніторингу ліквідності та стадій AMD.
- QA-утиліти для локального прогону SMC на історії (без запуску Stage1).

## Потік даних

- `app.main` виконує `bootstrap()` → `UnifiedDataStore` → `run_fxcm_ingestor` / `run_fxcm_status_listener` → `screening_producer` → `UI.publish_full_state`.
- `_warmup_datastore_from_snapshots()` підтягує останні JSONL-файли з `datastore/`, щоб прискорити прогрів RAM перед запуском.
- Уся жива історія надходить **лише** через Redis-канали зовнішнього FXCM конектора (`fxcm:ohlcv`, `fxcm:heartbeat`, `fxcm:market_status`).
- `_await_fxcm_history()` очікує поки стрім заповнить мінімальний `SCREENING_LOOKBACK` на `1m`; за потреби логи підказують, які символи ще не отримали дані.
- Детальний конспект дивись у `docs/stage1_pipeline.md`, щоб не перечитувати `app/main.py` під час аудиту пайплайна.

---

## Системні вимоги

- Python **3.11.9** (див. `runtime.txt`).
- Redis 6+ (локально чи віддалено) з правами на читання/запис.
- Доступ до FXCM (token/username/password) + опційний HMAC секрет.
- Залежності з `requirements.txt` (рекомендується окреме віртуальне середовище).

---

## Швидкий старт

```powershell
git clone https://github.com/Std07-1/smc_v1.git
cd smc_v1

python -m venv .venv
.\.venv\Scripts\activate

pip install --upgrade pip
pip install -r requirements.txt
```

---

## Налаштування середовища

1. Скопіюй `.env.example` (якщо є) або створи `.env` у корені:

  ```dotenv
  FXCM_ACCESS_TOKEN=...
  FXCM_USERNAME=...
  FXCM_PASSWORD=...
  FXCM_HMAC_SECRET=
  REDIS_HOST=127.0.0.1
  REDIS_PORT=6379
  REDIS_PASSWORD=
  LOG_LEVEL=INFO
  ```

2. Відредагуй `config/datastore.yaml` для директорій snapshot'ів, namespace та TTL.
3. Бізнес-параметри Stage1/SMC живуть у `config/config.py` та `app/thresholds.py` —
   не зберігай їх у змінних оточення.

---

## Запуск сервісів

- **Повний Stage1 pipeline** (FXCM ingest → Stage1 монітор → SMC → UI):

  ```powershell
  python -m app.main
  ```

- **SMC viewer (extended)** — автоматично стартує разом із `app.main`, але можна
  запускати вручну:

  ```powershell
  python -m UI.ui_consumer_experimental_entry
  ```

  Viewer спирається на `publish_smc_state`, читає payload напряму з Redis та
  відображає тренд, рендж, AMD, ліквідність і FXCM статус для одного символу
  (за замовчуванням береться перший із `FXCM_FAST_SYMBOLS`). Канал Stage1
  (`ai_one:ui:asset_state`) видалено як легасі.

- **QA/SMC snapshot runner** — детермінований прогон SMC на історичній вибірці без
  Stage1:

  ```powershell
  python -m tools.smc_snapshot_runner XAUUSD --tf 5m --extra 15m 1h --limit 500
  ```

---

## Тестування

Використовуємо pytest без зовнішніх сервісів (дані мокаються локально):

```powershell
python -m pytest tests -q
```

Таргетні тести:

- `tests/test_smc_structure_basic.py`, `tests/test_smc_ote_basic.py` — структура.
- `tests/test_smc_liquidity_basic.py`, `tests/test_smc_sfp_wick.py`,
  `tests/test_smc_amd_phase.py` — ліквідність та AMD FSM.
- `tests/test_smc_liquidity_bridge.py`, `tests/test_smc_core_contracts.py` — API/bridge.

---

## Структура директорій (скорочено)

| Шлях | Призначення |
| --- | --- |
| `app/` | Точка входу (`main.py`), bootstrap, screening producer, helpers |
| `config/` | Конфіг Stage1/SMC, datastore.yaml |
| `data/` | UnifiedDataStore, WS worker, raw data утиліти |
| `stage1/` | Моніторинг активів, тригери, індикатори |
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

Оновлено: 23.11.2025
