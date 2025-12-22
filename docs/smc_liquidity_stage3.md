# SMC Liquidity Layer • Етап 3

**Статус:** реалізовано та інтегровано (freeze). Оновлення тільки хвилями з тестами.

## 1. Мета та результат

- Побудувати MAJOR-liquidty шар над `tf_primary = 5m` (еталон XAUUSD) з опорою на готову структуру (`SmcStructureState`) і сесійні мітки (Tokyo/London/NY).
- Побудувати MAJOR-liquidty шар над `tf_primary = 5m` (еталон XAUUSD) з опорою на готову структуру (`SmcStructureState`) і сесійний контекст (ASIA/LONDON/NY).
- На вході: OHLCV-бари, swings/legs/BOS/CHOCH/bias/range_state/OTE, сесійний контекст з `SmcInput.context` (власні обчислення SMC з OHLCV, UTC-вікна).
- На виході: `SmcHint.liquidity` заповнюється інстансом `SmcLiquidityState`, який містить:
  - `pools`: усі рівні EQH/EQL/TLQ/SLQ/RANGE_EXTREME/WICK_CLUSTER з роллю (PRIMARY/COUNTERTREND/NEUTRAL), strength 0..100, n_touches, first/last_time, source_swings, meta.
  - `magnets`: 1–3 ключові області (price_min/max/center, liq_type=RANGE_EXTREME|POOL_CLUSTER, role, перелік pools).
  - `amd_phase`: ACCUMULATION / MANIPULATION / DISTRIBUTION / UNKNOWN.
  - `meta`: symbol, primary_tf, bar_count, pool_count, magnet_count, bias, `sfp_events`, `wick_clusters`, службові прапори.
  - `meta.liquidity_targets`: список цілей ліквідності `internal/external` (не ламає базові dataclass-и; додається тільки в `meta`).
- Призначення: показати, де «лежать стопи/ліквідність», стиснути їх у зрозумілі магніти та дати грубий режим AMD без втручання у Stage3 ризик-логіку.

Додатково для Stage2/UI: `smc_core.liquidity_bridge.build_liquidity_hint()` віддає найближчі цілі як `smc_liq_nearest_internal` / `smc_liq_nearest_external` (за наявності `ref_price` і `meta.liquidity_targets`).

## 2. Обсяг і модулі `smc_liquidity`

1. `pools.py`
   - EQH/EQL: послідовності swing-high/low у межах `cfg.eq_tolerance_pct`; strength пропорційна кількості торкань та свіжості.
   - TLQ/SLQ: «останні» high/low перед імпульсом (через legs + ATR).
   - RANGE_EXTREME: верх/низ активного діапазону з `smc_structure.range_engine` + підтвердження wick-кластерами.
   - WICK_CLUSTER: групування хвостів у вузькому діапазоні (max wick, count, side, timestamps).
   - Роль визначається зіставленням із bias та відносним розташуванням до поточної ціни (стопи лонгів/шортів).
2. `magnets.py`
   - Кластеризація pools за ціною/типом → магніти з `price_min/max`, `center`, `liq_type` (POOL_CLUSTER/RANGE_EXTREME), `role`, списком базових pools.
   - Ліміт 1–3 PRIMARY магнітів у snapshot, решта маркуються як COUNTERTREND/NEUTRAL.
3. `amd_phase.py`
   - Евристики:
     - range_state=INSIDE + ціна між кількома магнітами → ACCUMULATION;
     - пробій RANGE_EXTREME з сильними wick-кластерами → MANIPULATION;
     - трендовий рух від магніта до магніта без нових кластерів → DISTRIBUTION;
     - інакше UNKNOWN.
4. `sfp_wick.py` (існує) → розширити метадані про sweep/wick, щоб живити pools/meta.
5. `facade.py` / `__init__.py`
   - `compute_liquidity_state(snapshot: SmcInput, structure: SmcStructureState, cfg: SmcCoreConfig) -> SmcLiquidityState`.
   - Підтримує порожні списки, якщо структура порожня або барів мало.

## 3. Типи / API

- Оновлення `smc_core/smc_types.py`:
  - `SmcLiquidityPool`: `level`, `liq_type`, `role`, `strength`, `n_touches`, `first_time`, `last_time`, `source_swings`, `meta` (включно з `wick_cluster_id`, `range_extreme_id`, `sfp_id`).
  - `SmcLiquidityMagnet`: `price_min/max`, `center`, `liq_type`, `role`, `pools`, `meta` (bias, dominant_types).
  - `SmcLiquidityState`: `pools`, `magnets`, `amd_phase`, `meta` (symbol, tf, bar_count, pool_count, magnet_count, bias, `sfp_events`, `wick_clusters`).
- JSON приклад закріплений у snapshot `smc_xau_5m_2000bars_D` (розділ `liquidity`).
- Контракт не змінює Stage2/Stage3 API: це enrichment у `SmcHint`.

## 4. Межі Етапу 3

- **Робимо:** лише `smc_liquidity` пакет, оновлення типів, інтеграцію в `SmcCoreEngine`, відображення в snapshot/debug-viewer, тестове покриття.
- **Не робимо:** OrderBlock/Breaker/FVG (Етап 4), SmcSignal/entry правила (Етап 5), Stage2/Stage3 ризик-зміни, складну session-FSM.

Примітка по сесіях:

- Сесійний контекст (ASIA/LONDON/NY) — SSOT у `SmcInput.context` (обчислення SMC з OHLCV), далі прокидується у `SmcHint.meta`.
- Liquidity використовує цей контекст тільки як довідкові рівні/кандидати (session pools + session-based external targets), без окремої FSM.

## 5. Підетапи (DONE)

1. **Каркас + типи** — вирівняти `SmcLiquidity*` dataclass та документацію (`smc_core_overview.md`, цей файл).
2. **Wick-кластери та RANGE_EXTREME** — знайти хвостові конгломерати, побудувати RANGE_EXTREME рівні, додати у pools із роллю vs bias.
3. **EQH/EQL/TLQ/SLQ** — відбір swing-серій і імпульсних рівнів, присвоєння ролей.
4. **Магніти** — згрупувати pools, залишивши максимум 1–3 PRIMARY магніти.
5. **AMD-phase** — евристика на основі range_state + взаємодії з магнітами.
6. **Facade + інтеграція** — `compute_liquidity_state` + виклик у `SmcCoreEngine`.
7. **Тести й QA** — синтетичні трендові/рендж-кейси + 1–2 mini-snapshot-и (200–300 барів) для регресії.

## 6. Тестування та QA

- `tests/test_smc_liquidity_basic.py` — synthetic перевірки ролей (EQH/EQL/TLQ/SLQ, RANGE_EXTREME, магніти, AMD-phase fallback).
- `tests/test_smc_sfp_wick.py` (або розширений `tests/test_smc_liquidity_wick.py`) — детекція sweep/wick кластерів.
- QA інструменти:
  - `tools/smc_snapshot_runner.py` для формування JSON.
  - viewer/debug-панель (показати pools + магніти «як є» без нових розрахунків).
  - міні-зрізи XAUUSD (план: трендовий, флет, маніпуляційний день) із заскріненою еталонною поведінкою.
  - "golden set" історії `smc_xau_5m_2000bars_{A..D}` + threshold-CSV (B/C/D) з Етапу 2 як референс для перевірки `pool_count/magnet_count/amd_phase`.

## 7. Критерії готовності та freeze

- `SmcHint.liquidity` завжди присутній (навіть з порожніми списками) у QA snapshot-ах.
- У viewer видно 1–3 основні магніти, які можна описати мовою книжки (EQH/EQL, stop run, range extremes).
- Synthetic і mini-snapshot тести зелені; `pool_count`, `magnet_count`, `amd_phase` співпадають із зафіксованими очікуваннями.
- Після цього — тег `smc_liquidity_v1_frozen` та перехід до Етапу 4 (zones) або 2.5 (session minor), не змінюючи контракт.

## 8. Пов’язані документи

- `smc_core_stage1.md` — опис каркаса та API.
- `smc_structure_stage2.md` — джерело структури (swings/legs/bias/OTE) для liquidity.
- `roadmap.md` — інтегрована картина етапів.
