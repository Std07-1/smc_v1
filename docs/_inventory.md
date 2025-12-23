# Інвентаризація docs/ (SSOT для документації)

Цей файл — **інвентаризація** і карта канонічних сторінок.
Принцип: визначаємо SSOT-сторінки; дублікати не видаляємо одразу — спочатку перетворюємо на *deprecated stub* із посиланням на канон.

## Дерево файлів і короткий опис

- docs/README.md — (план D1) навігація по канонічних сторінках.
- docs/_inventory.md — інвентаризація + карта канон/дублікати.
- docs/roadmap.md — загальний roadmap SMC-core (архітектура, сценарії інтеграції, impact).
- docs/stage1_pipeline.md — Stage1 legacy пайплайн і запуск `app.main` (SMC-only), джерела істини FXCM.
- docs/smc_core_overview.md — канонічний огляд SMC-core: ролі, межі, стабільні API, data-flow.
- docs/smc_core_stage1.md — історичний опис «Етап 1» (каркас + API), статус/обмеження.
- docs/smc_structure.md — канонічний опис `smc_structure`: конвеєр, структури, meta.
- docs/smc_structure_stage2.md — історичний опис «Етап 2» для структури, QA-гайди, freeze-пункти.
- docs/smc_liquidity.md — канонічний опис `smc_liquidity`: конвеєр, структури, meta.
- docs/smc_liquidity_stage3.md — план/етап «3» для ліквідності (історичний документ-план).
- docs/smc_zones_stage4.md — Stage4.x нотатки по зонам (breaker/fvg), опис правил/полів.
- docs/smc_hint_contract.md — канонічний plain JSON контракт SmcHint для Stage1→UI.
- docs/breaker_v1_status.md — статус/freeze breaker_v1, QA результати й правила оновлення.
- docs/ob_v1_event_history_status.md — статус OB_v1 + event_history, QA і зафіксовані конфіги.
- docs/fxcm_contract_audit.md — канонічний аудит контрактів FXCM у Redis + шлях даних у репо.
- docs/fxcm_integration.md — гайд інтеграції FXCM-конектора і smc_v1 (канали, payload приклади).
- docs/fxcm_status_of_conector.md — опис формату агрегованого каналу `fxcm:status`.
- docs/fxcm_tick_agg_update_2025-12-13.md — оновлення контракту OHLCV щодо `complete=false` (tick-agg).
- docs/uds_smc_update_2025-12-13.md — контракт UDS↔SMC для warmup/backfill (S2/S3 requester).
- docs/ui_v2_fullscreen_chart_layout.md — UI_v2: причини «дрейфу вниз» у fullscreen та канон фіксу.
- docs/ui_v2_mobile_chart_drift_fix.md — UI_v2 mobile: канон фіксу дрейфу вниз (visualViewport).
- docs/ui/ui_v2_chart_invariants_and_boundaries.md — UI_v2: інваріанти та межі відповідальності графіка (wheel/drag/scale), SSOT.
- docs/runbook_tradingview_like_live_public_domain.md — runbook публічного домену (Cloudflare Tunnel/nginx) для live-барів.
- docs/runbook_cloudflare_named_tunnel_windows.md — Windows: named tunnel + “3 команди” для швидкого дебагу 502.
- docs/uds_smc_update_2025-12-13.md — контракт warmup/backfill + S2/S3 правила.

- docs/architecture/principles.md — принципи DRY/SSOT/SoC/Contract-first/Canonical + заборони.
- docs/architecture/module_boundaries.md — межі пакетів і правила імпортів.
- docs/architecture/contracts.md — базові правила контрактів (Envelope/schema_version).
- docs/architecture/migration_log.md — журнал хвиль міграцій.
- docs/style_guide.md — короткий style guide (типізація, мова, секції, заборона utils.py).

## Legacy / Update (історичні місця)

Ці файли можуть містити важливі рішення/контекст, але **не є SSOT** і можуть містити застарілі назви/версії.

- UPDATE.md (repo root): [../UPDATE.md](../UPDATE.md)
- UPDATE_CORE.md (repo root): [../UPDATE_CORE.md](../UPDATE_CORE.md)

## Duplicates / перекриття тем

### 1) SMC Structure

- **Canonical:** docs/smc_structure.md
- **Duplicates:**
  - docs/smc_structure_stage2.md (історичний етап/QA/freeze)
- **Дія:**
  - D2: лишити `smc_structure_stage2.md` як *deprecated stub* → посилання на `smc_structure.md`, а унікальні QA/freeze-частини перенести у `docs/smc_structure.md` або окрему `docs/smc/qa/structure_stage2_freeze.md`.

### 2) SMC Liquidity

- **Canonical:** docs/smc_liquidity.md
- **Duplicates:**
  - docs/smc_liquidity_stage3.md (план/етап)
- **Дія:**
  - D2: перетворити stage3-док на stub з посиланням; унікальний план/етапи перенести в `docs/roadmap.md` або `docs/smc/liquidity_stage3-plan.md` (як окрему історичну сторінку).

### 3) SMC-core «overview vs stage1 vs roadmap»

- **Canonical:** docs/smc_core_overview.md
- **Duplicates/перекриття:**
  - docs/smc_core_stage1.md (історичний статус етапу)
  - docs/roadmap.md (план і інтеграційні сценарії)
- **Дія:**
  - D2: `smc_core_stage1.md` → stub (з лінком на overview + roadmap).
  - D2: `roadmap.md` лишається як канон для плану, але не дублює стабільний API (посилання на overview).

### 4) FXCM контракти/канали

- **Canonical:** docs/fxcm_contract_audit.md (контракти і шлях даних у цьому репо)
- **Доповнюючий canonical:** docs/fxcm_integration.md (операційний гайд запуску)
- **Duplicates/перекриття:**
  - docs/fxcm_status_of_conector.md (вузька тема `fxcm:status`)
  - docs/fxcm_tick_agg_update_2025-12-13.md (вузьке оновлення контракту)
  - docs/uds_smc_update_2025-12-13.md (частково FXCM/UDS/команди)
  - docs/stage1_pipeline.md (містить посилання/витяги)
- **Дія:**
  - D1: зробити канонічні лінки через docs/README.md (без злиття текстів).
  - D2: винести `fxcm:status` у `docs/fxcm/status.md` (канон), а старий файл → stub.
  - D2: винести tick-agg update в `docs/fxcm/updates/2025-12-13_tick_agg.md` (канон), старий → stub.
  - D2: `uds_smc_update_2025-12-13.md` лишити як canonical для UDS↔SMC/S2/S3 (але додати явні посилання на `fxcm_contract_audit.md`).

### 5) UI_v2 «дрейф вниз» (fullscreen vs mobile)

- **Canonical (пропозиція):** docs/ui/ui_v2_chart_drift.md (одна сторінка: root-cause + два розділи: fullscreen/mobile)
- **Duplicates:**
  - docs/ui_v2_fullscreen_chart_layout.md
  - docs/ui_v2_mobile_chart_drift_fix.md
- **Дія:**
  - D2: створити канонічний об'єднаний документ і перетворити обидва на stubs.
  - D1: поки лише TOC у docs/README.md (без злиття).

### 6) Zones: breaker/OB статуси

- **Canonical (за змістом):**
  - docs/breaker_v1_status.md — канон статусу/QA/freeze breaker_v1
  - docs/ob_v1_event_history_status.md — канон статусу/QA/freeze OB_v1
- **Перекриття:**
  - docs/smc_zones_stage4.md частково дублює правила/поля
- **Дія:**
  - D2: у `smc_zones_stage4.md` лишити тільки загальні правила Stage4.x, а специфіку breaker/OB — посиланнями на status-доки.

## Цільова структура docs/ (мінімально інвазивна)

- docs/README.md (точка входу)
- docs/architecture/...
- docs/fxcm/...
- docs/smc/...
- docs/ui/...
- docs/runbooks/...
- docs/updates/...

> D1 робимо максимально безпечно: лише TOC + за потреби перенесення *в межах docs/* зі stub на старому місці.

## План операцій хвилями

### D1 (safe, no deletions)

- Додати docs/README.md з TOC на канонічні сторінки.
- (Опційно) Перенести 2–4 файли у підпапки `docs/fxcm/` та `docs/ui/` і залишити stubs у старих шляхах.
- Оновити docs/architecture/migration_log.md записом "Docs D1".

### D2 (merge + stubs)

- Створити канонічні сторінки для тем, що дублюються (наприклад UI drift).
- Перенести унікальний контент із дублікатів у канон.
- Перетворити дублікати на stubs (без видалень).

### D3 (cleanup)

- Видаляти *лише* якщо: немає посилань з README/коду/інших доків і немає унікального контенту.
- Перед видаленням — grep по репо на посилання та ручна валідація.
