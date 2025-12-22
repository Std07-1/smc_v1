# SMC-core Roadmap (стан на 2025-12-06)

## 0. Архітектурна рамка SMC-core

**Ціль:** окремий SMC-шар живе поруч з основним пайплайном без каскаду `if` у Stage1/Stage3.

### 0.1 Каталоги та ролі

1. `smc_core/` — центральний конфіг та `SmcCoreEngine.process_snapshot(...)`, який послідовно викликає підядра та збирає `SmcHint`.
2. `smc_structure/` — тренд, ренджі, dev, BOS/CHOCH, OTE та пов’язані події.
3. `smc_liquidity/` — EQH/EQL, TLQ/SLQ, свіпи, SFP/Wick, AMD-контекст.
4. `smc_zones/` — зони (Order Block, Breaker, Imbalance/FVG, POI/FTA) та OTE-фільтрація.
5. `smc_fusion/` (опційно пізніше) — агрегація всіх ознак у сигнали Stage2/Stage3.

### 0.2 Контракти (типи / API)

- `SmcInput` — snapshot даних: symbol, `tf_primary`, `ohlc_by_tf`, контекст (`trend_context_h1`, `whale_flow`, `vol_regime`, PDH/PDL тощо).
- `SmcStructureState`, `SmcLiquidityState`, `SmcZonesState` — окремі state-блоки.
- `SmcSignal`, `SmcHint` — агрегований вихід для Stage2/Stage3 (direction, сценарій, POI, SL/TP, confidence, R:R, FTA).
- `SmcCoreEngine.process_snapshot(snapshot: SmcInput) -> SmcHint` — фасад, який викликає структуру → ліквідність → зони.

## 1. Сценарії інтеграції

1. **S1 – Аналітичний режим:** SmcHint лише логів/телеметрії, Stage2/Stage3 поки не реагують.
2. **S2 – Stage2-lite:** SmcHint стає частиною контексту Stage2; використовується як фільтр/вага без зміни Stage3 ризиків.
3. **S3 – Повна інтеграція Stage3:** TP/SL/partial/FTA прив’язуються до зон, AMD-FSM керує сценаріями.

Рухаємося S1 → S2 → S3, але типи проектуємо одразу з прицілом на S3.

## 2. Impact (латентність, PnL, ризики)

- **Латентність:** робота на агрегованих барах (H1/M15/M5); бюджет 20–40 мс/символ/бар. Модулі незалежні й читають один `SmcInput`.

- **PnL / winrate:** зниження FP, поліпшення R:R завдяки OB/OTE/FVG входам.
- **Ризики:** перенавчений контекст може блокувати угоди. Потрібні метрики покриття (частка барів із сигналами) та A/B проти поточних стратегій.

## 3. GO/NO-GO

- **GO** на повний SMC-core з поступовою інтеграцією та телеметрією.

- Стартові кроки: створити `smc_core/`, `smc_structure/`, розширити `types.py`, інтегрувати лише у QA/backtest.

## 4. Етапи реалізації

### Етап 1 — Каркас SMC-core та типи _(статус: завершено 2025-12-06)_

- Створити каталог `smc_core/` (`__init__.py`, `config.py`, `engine.py`).
- `config.py`: `SmcCoreConfig` + `SMC_CORE_CONFIG` без state.

- Розширити `smc_core/smc_types.py`: `SmcInput`, `SmcStructureState`, `SmcLiquidityState`, `SmcZonesState`, `SmcSignal`, `SmcPoi`, `SmcZone`, `SmcLiquidityPool`, enum-и (`SmcTrend`, `SmcRangeState`, `SmcZoneType`, `SmcLiquidityType`, `SmcSignalType`).
- `backtest_runner_v1.py`: опційний крок `SmcCoreEngine` за прапором `SMC_BACKTEST_ENABLED` у конфізі.
- Тести: `tests/test_smc_types.py`, `tests/test_smc_core_contracts.py`.
- **Складність:** низько–середня. **Ризики:** правильно зафіксувати типи. **Факт:** пакети `smc_core/*`, `smc_structure/*` та тести (`test_smc_types.py`, `test_smc_core_contracts.py`) уже в репозиторії; інтеграція активна лише в backtest/QA через `SMC_BACKTEST_ENABLED`.

### Етап 2 — `smc_structure` _(статус: готовий до freeze, очікує фінальних QA)_

- **Передумова (дані TF):** у Data layer (`UnifiedDataStore`) є SSOT-матеріалізація `1m→5m→1h→4h` (закрито 2025-12-19).

- Каталог `smc_structure/` з:
  - `swing_detector.py` — 3-свічний патерн.
  - `structure_engine.py` — HH/HL/LH/LL, тренд, BOS/CHOCH.
  - `range_engine.py` — ренджі, dev, EQ.
  - `ote_engine.py` — зони 0.62–0.79 між свінгами.

- Інтеграція у `SmcCoreEngine`.
- Тести: `tests/test_smc_structure_basic.py`, `tests/test_smc_ote_basic.py` + `tools/smc_snapshot_runner.py` / `tools/smc_structure_threshold_study.py` для історичних зрізів (`smc_xau_5m_2000bars_{A..D}.json`).
- **Факт:** для XAUUSD 5m структура дає 40 свінгів, 39 легів із bias=SHORT, range_state=DEV_DOWN і однією SHORT PRIMARY OTE на зрізі «D», події BOS/CHOCH відсутні (узгоджується зі «спадним днем без нового структурного пробою»). ATR-пороги: `bos_min_move_atr_m1=0.6`, `bos_min_move_pct_m1=0.002`.
- **Контрольні кроки перед freeze:** (1) візуально звірити ще ап-трендовий і флет-тиждень; (2) зафіксувати "golden set" threshold CSV для тижнів B/C/D; (3) поставити тег `smc_structure_v1_frozen` після валідації, не торкаючись контрактів.
- **Складність:** середня. **Ризики:** коректність подій та збереження контракту при подальших порогових змінах.

### Етап 3 — `smc_liquidity` _(статус: реалізовано, триває розширене QA)_

- **Факт:** `smc_liquidity.compute_liquidity_state` уже входить у `SmcCoreEngine`; модулі `pools.py`, `magnets.py`, `sfp_wick.py`, `amd_state.py` будують EQH/EQL/TLQ/SLQ, RANGE/SESSION/SFP/WICK пули, агрегують магніти та визначають AMD-фазу.
- **Контракт у проді:** `SmcLiquidityState` містить `pools`, `magnets`, `amd_phase`, `meta` (`symbol`, `tf`, `pool_count`, `magnet_count`, `bias`, `sfp_events`, `wick_clusters`, `amd_reason`) і пас-тру транзитом іде в UI/Stage2 через `smc_core.liquidity_bridge`.
- **Тести:** `tests/test_smc_liquidity_basic.py` і місток `tests/test_smc_liquidity_bridge.py` прикривають ролі PRIMARY, формування магнітів і AMD FSM; додатково використовуємо `tools/smc_snapshot_runner.py --show-liq` для інтеграційної перевірки.
- **Next:**
  1. Зібрати «golden set» для AMD/магнітів (ренджі + трендові тижні XAUUSD/XAUUSDm) і зафіксувати очікувані `meta` поля.
  2. Розширити тести edge-кейсами (порожні swings, екстремальні ATR, контекст без `pdh/pdl`).
  3. Доставити Stage2 телеметрію (`smc_liq_has_above/below`, відстань до магніта) в основні монітори.
- **Складність:** середня–вища. **Ризики:** вирівнювання ролей PRIMARY із bias та уникнення шумових SFP/Wick пулів у волатильні дні.

### Етап 4 — `smc_zones` _(перезапуск, фокус на OB_v1)_

- **Факт:** SmcCoreEngine вже викликає `smc_zones.compute_zones_state(...)`; фасад повертає `SmcZonesState` навіть за відсутності зон і тримає інваріанти `zones/active_zones/poi_zones`. Каркас та типи `SmcZone/*` стабільні.
- **Проблема попереднього плану:** всі сутності (OB + Breaker + FVG + POI/FTA) були звалені в один етап без acceptance-критеріїв → combinatorial explosion.
- **Нова рамка:** послідовність мікроетапів, кожен закінчується тестами й документованим контрактом. Перший крок — лише Order Block.

#### 4.1 Санітарний каркас (статус: завершено)

- Гарантуємо, що `compute_zones_state` повертає `SmcZonesState` з порожніми списками й meta навіть при `structure=None`.
- Тримач інваріантів: `zones` — усі зони; `active_zones` — lookback-фільтр, `poi_zones` — резерв для майбутніх POI.
- Тест: `tests/test_smc_zones_skeleton.py` (synthetic DataFrame без структури).

#### 4.2 OB_v1 (статус: у роботі)

- Детектор (`smc_zones/orderblock_detector.py`) працює виключно по ногах зі `SmcStructureState`, де:
  - амплітуда ≥ `cfg.ob_leg_min_atr_mul * ATR`, барів ≤ `cfg.ob_leg_max_bars`;
  - є прив’язаний BOS/CHOCH (`SmcStructureEvent`), який підтверджує імпульс;
  - знайдено протилежну свічу-прелюдію в межах `cfg.ob_prelude_max_bars` із домінуючим тілом (`ob_body_domination_pct`, `ob_body_min_pct`).
- Зона заповнює `SmcZone` (BODY/WICK entry, роль PRIMARY/COUNTERTREND) та meta (`body_pct`, `reference_event_type`, `bar_count`).
- `SmcZonesState.meta` зберігає `orderblocks_*`, `active_zone_count`, `ob_params` (snapshot конфігів). Активні зони відсікаються за `cfg.max_lookback_bars` відносно часової осі фрейму.
- Тести: `tests/test_smc_zones_ob_basic.py` (PRIMARY long, COUNTERTREND, edge-кейси без BOS/малого тіла) + існуючий skeleton-тест.

#### 4.3+ відкладені підетапи (після стабілізації OB_v1)

1. **Breaker_v1:** будує breaker-блок лише на основі вже зафіксованих OB + свіп ліквідності.
2. **Imbalance/FVG_v1:** окремий детектор розривів між барами з логікою partial/full fill.
3. **POI/FTA_v1:** кластеризація OB/FVG та створення перших POI/FTA без впливу на існуючі ролі.
4. **Fusion/Signals:** лише після стабілізації зон і телеметрії.

- **Acceptance для закриття 4.2:** OB зʼявляються у `tools/smc_snapshot_runner.py --show-zones`, кількість PRIMARY не засмічує UI (1–3 актуальні зони), counters мають telemetry (`ob_count_total`, `ob_params`).
- **Ризики:** edge-кейси з індексами свічок/таймінгом lookback, надмірна чутливість порогів `ob_body_*`.

### Етап 5 — Fusion (SmcSignal)

- `smc_core/fusion.py`: набір правил (`Rule.apply(structure, liquidity, zones) -> list[SmcSignal]`).
- Приклади правил: trend continuation з ренджу; reversal із breaker + SFP + imbalance.

- Метрики: `ai_one_smc_signals_total`, `ai_one_smc_poi_active`, покриття сигналами.
- UI: передати SmcHint у `publish_full_state`.
- **Складність:** середня (дизайн правил). **Ризики:** не перетворити на дерево `if`.

### Етап 6 — Інтеграція в Stage2/Stage3

- **Stage2-lite:** `smc_bridge.py`, прапор `SMC_STAGE2_ENABLED`.
- **Stage3:** TP/SL/FTA від зон; A/B (поточний risk_manager vs SMC-aware на paper-режимі).
- **Складність:** середня–висока. **Ризики:** не зламати існуючі контракти.

### Етап 7 — QA / A/B / AMD-FSM / Trade-аналіз

- Побудувати AMD-FSM із ренджу та dev.
- `analyze_trades_v1.py`: знімати стан SMC під час угод.
- A/B метрики: `no_candidate_rate`, `p75/p95 |ret|` для сигналів, FP vs baseline.
- **Складність:** середня. **Ризики:** дисципліна зі збору метрик.

## 5. Оцінка складності та ризиків

- Етап 1: низько–середня, ключ — незмінність типів.
- Етап 2: середня, важливо зафіксувати події та ролі.
- Етап 3: середня–вища, багато edge-кейсів.
- Етап 4: висока, найбільший обсяг ітерацій.
- Етап 5: середня, концептуальний дизайн rules.
- Етап 6: середня–висока, інтеграція з ризик-менеджментом.
- Етап 7: середня, фокус на телеметрії й QA.

Правильний темп: завершувати кожен етап із тестами та документацією, триматись S1→S2→S3 і не протикати Stage3 контракт, поки SMC не пройде A/B.
