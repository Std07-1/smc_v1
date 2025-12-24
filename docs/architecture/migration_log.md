# Журнал міграцій

Формат запису: дата → хвиля → файли → що зроблено → примітки/ризики.

## 2025-12-23

- UI_v2: додано офлайн E2E smoke (Playwright) для захисту фіксів першого wheel/tooltip;
  HTTP сервер отримав `start()/stop()/get_listen_url()` для тестів;
  у конфігу виправлено kill-switch default (`SMC_LIVE_GAP_BACKFILL_ENABLED=False`) та парсинг ENV false-values.
  Команди на warmup/backfill — це payload у Redis-канал FXCM-конектора, не прямі FXCM виклики.

## 2025-12-24

- QA/SMC Journal: виконано фактологічний прогін `tools/smc_journal_report.py` для XAUUSD (5m)
  з офлайн-аудитом (`--ohlcv-path`) і сформовано SSOT-артефакти в `reports/smc_journal_p0_run5/`:
  `report_XAUUSD.md`, `audit_todo.md`, `touch_rate.csv`, `zone_overlap_examples.csv`,
  `case_b_removed_then_late_touch_examples.csv`, `case_c_short_lifetime_examples.csv`,
  `case_d_widest_zone_examples.csv`.
  Змін коду/контрактів у цій хвилі не робили.

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

## 2025-12-22 — D1 (SMC Zones): «Зона надто широка»

- Файли: `smc_core/config.py`, `smc_zones/__init__.py`, `smc_zones/poi_fta.py`, `tools/smc_journal_report.py`, `docs/smc_mass_audit_A-I.md`, `tests/test_case_d_wide_zone_span_atr.py`.
- Суть:
  - додано `SmcCoreConfig.max_zone_span_atr` (default=2.0) як guardrail проти надшироких зон (span_atr = |price_max-price_min|/atr_last);
  - надширокі зони прибираємо з `active_zones` (щоб не забивали top‑K UI) і не беремо в POI/FTA;
  - у `tools.smc_journal_report` додано новий зріз `span_atr_vs_outcomes(touched/mitigated)` для кореляції/бінінгу.
- Ризики/примітки:
  - зменшиться кількість `active_zones/poi_zones`; це очікувано і має покращити читабельність та FP, але може прибрати рідкісні корисні широкі зони;
  - поріг `max_zone_span_atr` можна підбирати QA-прогонами через `report_XAUUSD` (wide_zone_rate + span_atr_vs_outcomes).

## 2025-12-22 — E/F (SMC Zones + Journal): дублі/перекриття + missed touch

- Файли: `smc_core/config.py`, `smc_zones/__init__.py`, `smc_core/lifecycle_journal.py`, `tools/smc_journal_report.py`, `tests/test_smc_lifecycle_journal.py`, `tests/test_case_e_zone_overlap_merge.py`.
- Суть:
  - Case E: додано merge-by-overlap для зон одного типу/ролі/напрямку/TF (IoU поріг `SmcCoreConfig.zone_merge_iou_threshold`). Winner отримує `meta.merged_from`, а journal класифікує `removed_reason=replaced_by_merge`.
  - Case E: у frames додано `zone_overlap_active` (статистика IoU перекриттів серед активних зон), у репорті — секція `zone_overlap_matrix_active` і метрика `merge_rate`.
  - Case F: touch-логіку зроблено детермінованою через перетин high/low зі смугою `[min-eps, max+eps]` (eps = `SmcCoreConfig.touch_epsilon`), у репорті — офлайн аудит `missed_touch_rate(offline)` з параметром `--ohlcv-path`.

## 2025-12-22 — H (Journal QA): outcome після touch (LONG vs SHORT)

- Файли: `tools/smc_journal_report.py`, `tests/test_smc_journal_report_case_h.py`.
- Суть: додано офлайн аудит `touch_outcomes_after_touch(offline)` — після `touched` події рахуємо чи була `reversal` та/або `continuation` на горизонтах $K=1..N$ барів із порогом $X\cdot ATR$.
- Примітки/ризики: це **тільки QA/репорт**; runtime compute не змінюється. Якість оцінки залежить від коректності `atr_last` у контексті рядка та від відповідності `--ohlcv-path` символу/TF.

- 2025-12-18 — S0: TF-правда для SMC (tf_structure=5m) + чесні гейти compute.
  - Файли: `config/config.py`, `app/smc_producer.py`, `smc_core/input_adapter.py`, `tests/test_smc_tf_truth_primary_present.py`.
  - Суть: зафіксовано SSOT TF-план і додано gating у runtime: без 5m (або при insufficient/stale) SMC-core compute не викликається, але `smc_hint.meta` містить `gates/tf_plan/telemetry`.

- 2025-12-19 — S0.1: `tf_health` у `smc_hint.meta` для TF з плану.
  - Файли: `app/smc_producer.py`, `tests/test_smc_tf_truth_primary_present.py`.
  - Суть: додаємо `tf_health{tf: has_data/bars/last_ts/lag_ms}` для 1m/5m/1h/4h, щоб UI/логи показували «який TF реально живий».
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

- 2025-12-18 — Deploy/VPS: origin HTTPS (443) для Cloudflare + same-origin proxy
  - Файли: `deploy/nginx/smc_ui_v2.conf`.
  - Суть: додано `server { listen 443 ssl http2; ... }` під Cloudflare Origin CA, маршрути `/`, `/smc-viewer/stream`, `/fxcm/*` без змін.
  - Ризики: потребує коректного DNS (не tunnel-CNAME) та наявності сертифікату/ключа на VPS; інакше Cloudflare дає 502.

- 2025-12-18 — Runtime/VPS: стійкість до тимчасового падіння Redis (reconnect + backoff)
  - Файли: `data/fxcm_ingestor.py`, `data/fxcm_price_stream.py`, `data/fxcm_status_listener.py`, `app/main.py`.
  - Суть: якщо Redis/мережа пропадає (router/redis restart), лістенери не валять процес; роблять перепідключення з exponential backoff (1s..60s).
  - Тести: `pytest tests/test_redis_reconnect_loops.py` (імітація дисконекту → повторна підписка).

- 2025-12-20 — UI_v2/Web: стабілізація price-scale взаємодій (wheel/drag) + `debug_chart=1` дамп аномалій (без зміни бекенду/контрактів).

- 2025-12-20 — UI_v2/Web: zone-label markers (видимість/діагностика)
  - Файли: `UI_v2/web_client/chart_adapter.js`.
  - Суть: `zone_labels=1` читається також із hash-роутингу; якщо у зони немає `origin_time`, marker ставиться по fallback (invalidated_time або останній бар); zone labels ставимо `belowBar`, щоб не конфліктували з BOS/CHOCH.
  - Ризики: мінімальні; може додати трохи візуального шуму при `zone_labels=1`, але за замовчуванням вимкнено.

## 2025-12-21 — S6: Stage6 довіра (4.2 vs 4.3) — `UNCLEAR reason` + симетричний анти-фліп

- Файли: `smc_core/stage6_scenario.py`, `app/smc_state_manager.py`, `core/contracts/viewer_state.py`, `UI_v2/viewer_state_builder.py`, `UI/publish_smc_state.py`, `tools/qa_stage6_scenario_stats.py`, `reports/stage6_stats_xauusd_h60_v2.md`.
- Суть:
  - Stage6 повертає `UNCLEAR` з явною причиною (`NO_*`, `LOW_SCORE`, `CONFLICT`) як частину телеметрії.
  - Анти-фліп винесено/залишено поза core (в `SmcStateManager`) і зроблено симетричним: є decay до `UNCLEAR`, сильний override, адаптація порога для `MIXED` HTF bias.
  - UI отримує одночасно stable/raw/pending + top-3 `why`, щоб трейдер бачив “мапу” і “що зараз” без прихованої липкості.
- Примітки/ризики:
  - Поведінка `stable` змінилась: може повертатись до `UNCLEAR` (це свідомий компроміс заради довіри).
  - Flips можуть зрости на шумних ділянках; QA репорт є SSOT для базового контролю.

## 2025-12-21 — S6.1: Stage6 довіра — P1 асиметричний anti-flip + hard_invalidation

- Файли: `app/smc_state_manager.py`, `tests/test_smc_stage6_hysteresis.py`.
- Суть:
  - Додано асиметрію стабілізації: `4_2 → 4_3` може пробивати TTL через hard-факти з core (`hold_above_up`) або strong micro-confirm.
  - `4_3 → 4_2` зроблено жорсткішим: без явного `failed_hold_up` switch не виконується; при BOS_DOWN після sweep без `failed_hold_up` робимо швидку інвалідацію у `UNCLEAR`.
  - У `scenario_flip.reason` додано явні причини формату `hard_invalidation:*`.
- Примітки/ризики:
  - Поведінка `stable` стала менш симетричною (це свідомо), щоб прибрати «погані фліпи» і не губити справжню інвалідацію.
  - Для контролю наслідків використовувати `tools/qa_stage6_scenario_stats` (порівнювати flip-rate та розподіл `UNCLEAR` причин).

## 2025-12-21 — S6.2: Stage6 довіра — P0b/P0c (анти-конфліктні факти) + QA лічильники

- Файли: `smc_core/stage6_scenario.py`, `tools/qa_stage6_scenario_stats.py`, `tests/test_smc_stage6_scenario.py`.
- Суть:
  - P0b: якщо після sweep одночасно бачимо `BOS_UP` і `BOS_DOWN`, трактуємо як chop/шум: не додаємо обидва внески в скоринг; у телеметрії додаємо `events_after_sweep.chop=true`.
  - P0c: прибрано подвійний bias у скорингу: `HTF‑Lite bias` більше не додається як окремий внесок, щоб не множити `UNCLEAR(CONFLICT)` при наявному HTF bias з контексту/фреймів.
  - Рівні: `hold_level_up` тепер відображає інвалідаційний рівень (max з 5m/HTF), але `failed_hold_up` рахується на 5m `range_high`, щоб не ламати логіку sweep→failed_hold коли HTF рівень далеко.
  - QA: у звіті додаються лічильники `hard_invalidation_count` та розподіл `flip_pairs_by_reason`.
- Примітки/ризики:
  - Це навмисно робить `stable 4_3` ще рідшим без жорсткої інвалідації (довіра трейдера > частота сигналів).
  - Якщо `UNCLEAR(CONFLICT)` не падає на інших символах, наступний крок — ізоляція конфліктних внесків через exemplars (без тюнінгу порогів).

- 2025-12-20 — UI_v2/Web: zone-label markers (видимість/діагностика)
  - Файли: `UI_v2/web_client/chart_adapter.js`.
  - Суть: `zone_labels=1` читається також із hash-роутингу; якщо у зони немає `origin_time`, marker ставиться по fallback (invalidated_time або останній бар); zone labels ставимо `belowBar`, щоб не конфліктували з BOS/CHOCH.
  - Ризики: мінімальні; може додати трохи візуального шуму при `zone_labels=1`, але за замовчуванням вимкнено.
