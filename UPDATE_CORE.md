<!-- markdownlint-disable MD036 -->

# UPDATE_CORE.md

Журнал змін **core-логіки** (SMC core / liquidity / structure / zones / пайплайни, що впливають на сигнали).

- Для Web/UI змін використовуємо `UPDATE.md`.
- Конвенція: **зміна → тест → UPDATE_CORE.md → відповідь у чаті**.

## Формат запису (конвенція)

Кожен запис має містити:

- **Дата/час** (локально) + коротка назва зміни.
- **Що змінено**: 3–10 пунктів по суті.
- **Де**: ключові файли/модулі.
- **Тести/перевірка**: що саме запускалось і результат.
- **Примітки/ризики** (за потреби): що може вплинути на рантайм.

---

## 2025-12-16 — Dev process: введено `UPDATE_CORE.md` для змін core-логіки

**Що змінено**

- Додано окремий журнал `UPDATE_CORE.md` для змін у core-логіці (SMC core / liquidity / structure / zones / core-пайплайни).
- Уточнено правило: **зміна → тест → відповідний UPDATE → відповідь у чаті**, де для Web/UI використовується `UPDATE.md`.

**Де**

- UPDATE_CORE.md
- .github/copilot-memory.md

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_smc_core_contracts.py` → `1 passed`.

## 2025-12-16 — Docs(core): зафіксовано повний план “SMC без шуму” (етапи 0–8)

**Що змінено**

- Додано в пам’ять репо поетапний план (0–8) для “трейдерського” SMC-рендера без шуму.
- Окремо підсвічено Етап 0: TF-правда (`tf_exec=1m`, `tf_structure=5m`, `tf_context=[1h,4h]`), телеметрія (`tf_primary/tf_exec/tf_context/bars_used/last_ts/lag_ms`) та gate `NO_5M_DATA`.

**Де**

- .github/copilot-memory.md

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_smc_core_contracts.py` → `1 passed`.

## 2025-12-18 — Wave F (F0/F1/F2/F3): quality gates для SMC/Stage3 (рейки + тести + latency smoke)

**Що змінено**

- Додано документ з матрицею quality-gates (DATA/TF_TRUTH/PRIMARY_ONLY/SERDE/LATENCY/RISK) з режимами `accept/warn/drop` (у F0–F2 лише accept+warn).
- Додано tests-only гейти для DATA + TF_TRUTH + PRIMARY_ONLY, щоб ловити регреси без зміни runtime.
- Додано рейки імпортів (pre-commit boundary): заборонено тягнути SMC пакети у випадкові модулі поза контрольованими винятками.
- Додано latency smoke інструмент для локального виміру p50/p75/p95 та лічильників `no_data/exceptions` (без CI-гейту).
- Додано мінімальний bootstrap `sys.path` у smoke tool, щоб `python tools/smc_latency_smoke.py ...` працював без `-m`.

**Де**

- docs/quality_gates_smc_stage3.md
- tests/test_smc_data_gate_open_close_ms.py
- tests/test_smc_tf_truth_primary_present.py
- tests/test_smc_primary_only_gate.py
- tools/import_rules.toml
- tools/smc_latency_smoke.py

**Тести/перевірка**

- `python -m pre_commit run --all-files` → Passed
- `python tools/audit_repo_report.py` (production surface) → OK (0 findings)
- `python -m pytest -q` → `220 passed`

## 2025-12-18 — Docs(core): уточнено SSOT-план мультитаймфреймового SMC “без шуму” (3 шари + гейти + explain)

**Що змінено**

- Оновлено “план, якому можна довіряти”: SMC як технічний розбір (без “сигналів”), з 3 шарами рендера (Context 1h/4h, Structure 5m, Execution 1m).
- Зафіксовано принципи анти-шуму: ролі TF, ліміти об’єктів, `why[]` + `score`, та “чесні гейти” `NO_*` замість фантазій.
- Розширено опис етапів 0–8: TF-правда/телеметрія/гейти → реальні TF у store → структура/ліквідність/POI → execution → сценарій 4.2/4.3 → UI → QA.

**Де**

- .github/copilot-memory.md

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_smc_core_contracts.py` → passed.
