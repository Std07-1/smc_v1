# Журнал міграцій

Формат запису: дата → хвиля → файли → що зроблено → примітки/ризики.

## 2025-12-16 — Хвиля 1

- Файли: додано `core/serialization.py`, `core/formatters.py`, `core/contracts/*`,
  а також docs у `docs/architecture/*` і `docs/style_guide.md`.
- Суть: створено SSOT-ядро та правила/контракти.
- Важливо: **існуючий код не змінювався**.

## 2025-12-16 — Docs D1

- Файли: додано `docs/README.md`, `docs/_inventory.md`, `docs/fxcm/status.md`, `docs/ui/*`.
- Суть: додано навігацію (TOC), інвентаризацію та виконано безпечну реорганізацію в межах `docs/`.
- Важливо: видалень немає; старі сторінки перетворені на stubs із посиланням на канон.

## 2025-12-16 — Contracts C1

- Файли: додано `reports/contracts_audit.md`, `core/contracts/compat.py`,
  `docs/architecture/contracts_migration.md`; оновлено `core/contracts/__init__.py`.
- Суть: зроблено compat-шар (alias'и) для контрактів без зміни існуючих схем/продюсерів.
- Важливо: це тимчасовий шар для міграції (C2/C3).

## 2025-12-16 — Contracts C2 (SMC schema_version)

- Файли: додано `core/contracts/smc_state.py`; оновлено `core/contracts/__init__.py`,
    `UI_v2/smc_viewer_broadcaster.py`, `UI/experimental_viewer.py` та docs.
- Суть: зафіксовано канон `smc_state_v1` + legacy alias `1.2` і додано
    безпечну нормалізацію в консюмерах.
- Важливо: форма payload не змінювалась; емісія не мігрувалася масово.

## 2025-12-16 — Contracts C3 (SMC schema_version emission)

- Файли: оновлено `config/config.py`.
- Суть: емісія `meta.schema_version` для SMC-state переведена на `smc_state_v1`.
- Важливо: legacy-значення `1.2` все ще приймається консюмерами; форма payload не змінена.

## 2025-12-16 — S1 (I/O serialization через core.serialization)

- Файли: оновлено `UI/publish_smc_state.py`, `UI_v2/viewer_state_ws_server.py`.
- Суть: серіалізацію JSON та формування ISO-Z часу на межах I/O переведено на `core.serialization`.
- Важливо: структура payload (поля/ключі/вкладеність) не змінювалась.

- 2025-12-16 — S2: FXCM OHLCV WS I/O переведено на `core.serialization` (`json_loads/json_dumps`), shape без змін.

- 2025-12-16 — B1: додано локальний boundary-check (без CI).

- 2025-12-16 — R1: boundary-check підключено як pre-commit hook (локально, без CI).

- 2025-12-16 — R2: стабілізовано pre-commit boundary-check (language: python + tomli fallback) для Windows/venv.

- 2025-12-16 — C4.1: додано канонічні FXCM контракти (channels/telemetry) у `core/contracts/*` + audit report, без зміни runtime shape.

- 2025-12-16 — C4.2: `data/fxcm_status_listener.py` переведено на канон `core.contracts.fxcm_telemetry` (без зміни поведінки/shape).

- 2025-12-16 — T1: застарілі тести `tests/test_fxcm_status_listener.py` (heartbeat/market_status) карантинено; додано актуальні asserts під реальний listener (fxcm:status), без змін runtime.

- 2025-12-17 — C4.3a: додано `core.contracts.fxcm_validate` (канон validate_fxcm_*), без зміни runtime.

- 2025-12-17 — C4.3b: додано soft-validate FXCM WS boundary через `core.contracts.fxcm_validate` (без зміни поведінки/shape).

- 2025-12-17 — C4.4: strict-validate FXCM WS за флагом `FXCM_WS_STRICT_VALIDATE` (default off); soft-validate лишається.

- 2025-12-17 — R3: додано forbidden-string guard проти `fxcm:price_tick` у boundary-check (pre-commit).

- 2025-12-17 — C5.2: `UI_v2/fxcm_ohlcv_ws_server.py` переведено на SSOT-константи `FXCM_CH_*` (без alias/dual-subscribe).

- 2025-12-17 — B0: `core/contracts/*` очищено від прямих `json.loads/json.dumps` (тільки `core.serialization`):
    `core/contracts/fxcm_telemetry.py`, `core/contracts/fxcm_validate.py`.

- 2025-12-17 — B1: UI_v2 I/O переведено на `core.serialization` (json_* + без `default=str`).

- 2025-12-17 — B2: data layer I/O переведено на `core.serialization` (без `json.loads/json.dumps` у data/* I/O):
    `data/unified_store.py`, `data/fxcm_ingestor.py`, `data/fxcm_price_stream.py`, `data/fxcm_status_listener.py`, `data/utils.py`.

- 2025-12-17 — B3: SSOT-час/UTC у app/* + UI: прибрано `datetime.utcnow()/isoformat()+"Z"` та ручні UTC tz-hacks на користь `core.serialization`.
  - Файли: `UI/publish_smc_state.py`, `app/smc_producer.py`, `app/smc_state_manager.py`, `app/console_status_bar.py`, `app/fxcm_warmup_requester.py`, `core/serialization.py`.

- 2025-12-17 — T3: tests-only стабілізація після B3 (pytest green).
  - Файли: `tests/test_fxcm_telemetry.py`, `tests/test_ingestor.py`, `tests/test_s3_warmup_requester.py`.
  - Суть: оновлено очікування часу/UTC (порівняння через парсинг у datetime), виправлено monkeypatch на `utc_now_ms` (мілісекунди), та стабілізовано тести ingest (рахуємо rows за фактичними записами у store + ізоляція через monkeypatch feed-state).
  - Важливо: **лише `tests/*`**; runtime/shape payload не змінювались.

- 2025-12-17 — D1: видалено legacy Rich-консоль (status-bar + wrappers).
  - Файли: видалено `app/console_status_bar.py`, `app/rich_console.py`, `utils/rich_console.py`.
  - Суть: механічно прибрано legacy-консольний UI; залежності/тести зачищено.
  - Важливо: протоколи/payload не змінювались; `pytest -q` має бути green.

- 2025-12-17 — A0: додано audit-репорт для інвентаризації залишків міграції (SSOT/контракти/рейки).
  - Файли: додано `tools/audit_repo_report.py`.
  - Суть: автоматично рахує і показує місця з `json.dumps/json.loads/default=str/isoformat()+"Z"/ручним UTC`, локальні `TypedDict/*SCHEMA_VERSION` поза `core/contracts/*`, "utils"-сліди, а також перевіряє межі імпортів `core/` за `tools/import_rules.toml`.
  - Режими: `--only-core` (швидкий скан тільки `core/`) та `--top N` (top offenders: файл → кількість збігів).
  - Важливо: **тільки інструмент аудиту** — runtime/shape payload/контракти не змінювались.

- 2025-12-17 — B5: production I/O + час переведено на `core.serialization` (json_* / UTC), без зміни shape/outgoing.

- 2025-12-17 — C0: audit підтримує явні UI-local схеми (TypedDict) без фальшивих "порушень".
  - Файли: `tools/audit_repo_report.py`, `UI_v2/schemas.py`.
  - Суть: маркер `audit: local-schema` прибирає шум по UI-local TypedDict із секції C.
  - Важливо: runtime/shape payload не змінювались.

- 2025-12-17 — C1: FXCM TypedDict/validate переведено на SSOT `core.contracts` (thin compat для data).
  - Файли: `data/fxcm_schema.py`, `core/contracts/fxcm_channels.py`.
  - Суть: `data/fxcm_schema.py` тепер re-export контрактів/validate з `core.contracts.fxcm_channels/fxcm_validate`.
  - Важливо: runtime/shape payload не змінювались.

- 2025-12-17 — C2: viewer/SMC канонічні контракти винесено в `core/contracts/viewer_state.py`, `UI_v2/schemas.py` лишився compat+UI-local.

- 2025-12-17 — C3: внутрішні імпорти UI_v2 переведено з `UI_v2.schemas` на `core.contracts.viewer_state` (compat façade лишається для історичних імпортів).

- 2025-12-17 — C4: `UI_v2/viewer_state_builder.py` переведено на `core.contracts.viewer_state` + додано явний виняток `NO_SYMBOL` при відсутньому symbol.

- 2025-12-17 — C5: `UI_v2/viewer_state_ws_server.py` і `UI_v2/viewer_state_store.py` переведено на `core.contracts.viewer_state` (мінімальна заміна імпортів).

- 2025-12-17 — B6: додано SSOT `core.serialization.utc_ms_to_iso_offset` (UTC `+00:00` для UI) + `UI_v2/viewer_state_builder.py` переведено на SSOT; audit (B) посилено для `datetime.fromtimestamp(..., tz=UTC)`.

- 2025-12-17 — B6.1: audit зроблено блокуючим для критичних B-порушень (SSOT/час + заборонені локальні хелпери).
  - Файли: `tools/audit_repo_report.py`.
  - Суть: exit code != 0 при `json.dumps/json.loads/default=str`, ручних UTC/ISO патернах, та `def _safe_int/_safe_float/_as_dict` поза SSOT.
  - Важливо: це **B-хвиля** (enforcement), не зміна контрактів (C).

- 2025-12-17 — C-DONE: Contracts хвилі C формально закрито (checklist).
  - Audit: секція C = OK (TypedDict/SCHEMA_VERSION поза `core/contracts/*` не знайдено).
  - Compat: `UI_v2/schemas.py` = thin re-export з `core.contracts.viewer_state`; `data/fxcm_schema.py` = thin re-export з `core.contracts.fxcm_*`.
  - Межі: `core/contracts/compat.py` не тягне `core → UI_v2` / `core → data`.
  - Hot-модулі: `smc_viewer_broadcaster.py`, `viewer_state_server.py`, `viewer_state_ws_server.py`, `viewer_state_store.py`, `viewer_state_builder.py` імпортують типи з `core.contracts.viewer_state`.
  - Гейти: `python -m pre_commit run --all-files`, `python tools/audit_repo_report.py` (production surface), `python -m pytest -q` = green.

- 2025-12-18 — E3: видалено `core/contracts/compat.py` (0 usages підтверджено), `core/contracts/__init__.py` звужено під прямі канонічні імпорти.

- 2025-12-18 — E4: видалено thin-compat модулі `UI_v2/schemas.py` та `data/fxcm_schema.py` (0 usages у repo).

- 2025-12-18 — E5: документацію оновлено під нову реальність (жодних compat-шляхів у "як імпортувати"; compat згадується лише як історичний етап).
