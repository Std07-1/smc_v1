# Документація smc_v1

Цей каталог містить канонічні документи (SSOT) та історичні нотатки.
Принцип: **канонічні сторінки** мають бути очевидними; дублікати не видаляємо одразу — робимо *deprecated stubs* з посиланням.

## Швидка навігація (канонічні сторінки)

### Архітектура та стандарти

- Архітектурні принципи: [architecture/principles.md](architecture/principles.md)
- Межі модулів та імпорти: [architecture/module_boundaries.md](architecture/module_boundaries.md)
- Контракти (Contract-first): [architecture/contracts.md](architecture/contracts.md)
- Style guide: [style_guide.md](style_guide.md)
- Журнал міграцій: [architecture/migration_log.md](architecture/migration_log.md)
- Інвентаризація docs: [_inventory.md](_inventory.md)

### SMC-core

- Огляд SMC-core (SSOT): [smc_core_overview.md](smc_core_overview.md)
- Plain JSON контракт SmcHint (SSOT): [smc_hint_contract.md](smc_hint_contract.md)
- Структура (SSOT): [smc_structure.md](smc_structure.md)
- Ліквідність (SSOT): [smc_liquidity.md](smc_liquidity.md)
- Roadmap: [roadmap.md](roadmap.md)

### FXCM

- Аудит контрактів FXCM у Redis (SSOT): [fxcm_contract_audit.md](fxcm_contract_audit.md)
- Integration guide (операційно): [fxcm_integration.md](fxcm_integration.md)
- Канал стану конектора `fxcm:status` (SSOT): [fxcm/status.md](fxcm/status.md)

### UI

- UI_v2 fullscreen drift (SSOT): [ui/ui_v2_fullscreen_chart_layout.md](ui/ui_v2_fullscreen_chart_layout.md)
- UI_v2 mobile drift (SSOT): [ui/ui_v2_mobile_chart_drift_fix.md](ui/ui_v2_mobile_chart_drift_fix.md)

#### Часті проблеми UI

Якщо в UI є «дрейф вниз»/проблеми лейаута графіка:

- Fullscreen: [ui_v2_fullscreen_chart_layout.md](ui_v2_fullscreen_chart_layout.md)
- Mobile (visualViewport): [ui_v2_mobile_chart_drift_fix.md](ui_v2_mobile_chart_drift_fix.md)

### Runbooks

- TradingView-like live (public domain): [runbook_tradingview_like_live_public_domain.md](runbook_tradingview_like_live_public_domain.md)
- Cloudflare named tunnel (Windows) + 502 debug: [runbook_cloudflare_named_tunnel_windows.md](runbook_cloudflare_named_tunnel_windows.md)

## Історичні/статусні документи

Ці сторінки можуть бути актуальними, але не завжди є SSOT:

- Stage1 legacy pipeline: [stage1_pipeline.md](stage1_pipeline.md)
- FXCM tick-agg update (2025-12-13): [fxcm_tick_agg_update_2025-12-13.md](fxcm_tick_agg_update_2025-12-13.md)
- UDS↔SMC update (2025-12-13): [uds_smc_update_2025-12-13.md](uds_smc_update_2025-12-13.md)
- Breaker_v1 status: [breaker_v1_status.md](breaker_v1_status.md)
- OB_v1 + event_history status: [ob_v1_event_history_status.md](ob_v1_event_history_status.md)
