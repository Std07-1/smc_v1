# Quality Gates для SMC/Stage3 (Wave F0)

Документ фіксує **матрицю гейтів якості** для SMC-core та майбутньої інтеграції Stage3.
Це **рейки + інваріанти + тести**, без зміни runtime-поведінки у хвилях F0–F2.

## Принципи

- **F0–F2:** дозволено лише `accept` + `warn` (без `drop`).
- Будь-який `drop` можливий лише у пізніших хвилях (після окремого GO/NO-GO).
- Де можливо, інваріанти мають бути **SSOT** (наприклад, FXCM bars валідуються через `core/contracts/*`).
- **F1 тести — CI-ворота:** будь-який майбутній рефактор SMC/UDS, який ламає DATA/TF_TRUTH/PRIMARY_ONLY, має падати в CI одразу.

## Матриця гейтів

| Gate | Що захищає | Інваріанти (SSOT) | Де перевіряємо | Дії (F0–F2) |
|---|---|---|---|---|
| **DATA** | Якість OHLCV барів | `open_time/close_time` у **ms**, `close_time >= open_time`, базові поля `open/high/low/close/volume` присутні, `open_time` **монотонно** зростає, дублікати `open_time` — **детермінована** політика | `core/contracts/fxcm_validate.py` + нормалізація в `data/unified_store.py` | `accept` валідні, `warn` про часткову фільтрацію; `drop` зарезервовано |
| **TF_TRUTH** | “Правда TF” та узгодженість `tf_primary` | `tf_primary` заданий; при відсутності даних по `tf_primary` — **не падати** (повертати стабільний shape) | `smc_structure`/`smc_core.engine` + тести пайплайна | `accept` (best-effort), `warn` через мету/телеметрію |
| **PRIMARY_ONLY** | Заборона “випадкового” використання COUNTERTREND | В Stage2/Stage3-bridge використовуються лише об’єкти з `role == "PRIMARY"` | `smc_core/liquidity_bridge.py` (та майбутні bridge) | `accept`, `warn` при наявності тільки COUNTERTREND |
| **SERDE** | JSON-friendly для UI/viewer/state | Всі payload-и, що йдуть у UI/Redis, серіалізуються через `core.serialization` + SMC serializers не протікають dataclass/Enum/Timestamp | `smc_core/serializers.py`, `core/serialization.py` | `accept`, `warn` (аудит/тести) |
| **LATENCY** | Бюджети часу | Смоук-метрика p95 циклу ≤ 800ms (поки без CI-гейту) | Інструмент у `tools/` | `accept` (спостереження), `warn` при деградації |
| **RISK** | Контур ризику (зарезервовано) | Інваріанти/контракти ризику додаються окремою хвилею (без прихованих важелів) | Док/контракт | `accept` (зарезервовано), `warn` при невідповідності |

## Детермінована політика для дублікатів `open_time`

Поточна поведінка UnifiedDataStore:

- якщо немає `is_closed`, дублікати `open_time` дедупляться з `keep="first"` і далі сортуються за `open_time`.
- якщо є `is_closed`, рядок з `is_closed=True` має пріоритет.

Це **фіксуємо тестом**, без зміни логіки у F1.

## Scope хвиль

- **F0:** лише цей документ.
- **F1:** tests-only: DATA + TF_TRUTH + PRIMARY_ONLY.
- **F2:** рейки імпортів (pre-commit) — заборона “розповзання” SMC імпортів у випадкові модулі.
- **F3:** latency smoke tool (без CI-гейту): `tools/smc_latency_smoke.py`.

## F3: запуск latency smoke

Приклад (Windows/PowerShell):

`C:/Aione_projects/smc_v1/.venv/Scripts/python.exe tools/smc_latency_smoke.py --symbols xauusd --tf 5m --extra 1m,15m,1h --limit 500 --cycles 200`
