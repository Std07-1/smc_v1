# SMC Liquidity Layer

Документ описує модуль `smc_liquidity`, який фіналізує ліквідність після завершення
Stage3 (AMD/SFP/Wick). Шар спирається на готову структуру й відповідає за `pools`,
`magnets`, `amd_phase`, що надалі виносяться в UI та Stage2 через
`smc_core.liquidity_bridge`.

## Роль та межі

- Приймає `SmcInput`, `SmcStructureState`, `SmcCoreConfig`; не зчитує дані напряму з
  Redis/біржі.
- Не змінює структуру (тільки читає `swings`, `bias`, `active_range`).
- Повертає `SmcLiquidityState`, який використовується як є (UI — пас-тру, Stage2 —
  через bridge).
- Усі додаткові події (`sfp_events`, `wick_clusters`, причини AMD) передаються
  виключно через `meta`.

## Конвеєр обробки

1. **EQH/EQL пулі** (`pools.build_eq_pools_from_swings`) — групує swing high/low у
   кластери з допуском `cfg.eq_tolerance_pct`, визначає роль на основі bias.
2. **Трендові TLQ/SLQ** (`pools.add_trend_pools`) — бере останній swing low/high і
   додає пул PRIMARY у бік bias.
3. **Range/session пулі** (`pools.add_range_and_session_pools`) — активний діапазон
   → `RANGE_EXTREME`; контекст `pdl/pdh` → `SESSION_LOW/HIGH`.
4. **SFP + wick** (`sfp_wick.detect_sfp_and_wicks`) — проходить кожну свічку,
   шукає sweep проти рівня й довгі гніта (`WICK_RATIO`), повертає додаткові пулі та
   телеметрію.
5. **Магніти** (`magnets.build_magnets_from_pools_and_range`) — агрегує пулі за ціною
   з тим самим допуском, визначає тип за пріоритетом (`RANGE_EXTREME` > session >
   trend > EQ) і роль PRIMARY/COUNTERTREND.
6. **AMD-фаза** (`amd_state.derive_amd_phase`) — FSM з пріоритетом
   MANIPULATION → DISTRIBUTION → ACCUMULATION → NEUTRAL. Використовує
   `range_state`, SFP/wick сигнали, BOS, домінування TLQ/SLQ та ATR-медіану.

## Ключові структури

- `SmcLiquidityPool` — рівень, тип (`EQH/EQL/TLQ/...`), сила, кількість торкань,
  роль (`PRIMARY/COUNTERTREND/NEUTRAL`), `meta.source` (eq_cluster/range/session/sfp/wick).
- `SmcLiquidityMagnet` — агрегований діапазон `price_min/max/center`, список пулів,
  тип і роль.
- `SmcLiquidityState` — `pools`, `magnets`, `amd_phase`, `meta` (загальна телеметрія).

## Метадані `SmcLiquidityState.meta`

- `bar_count`, `symbol`, `primary_tf`, `pool_count`, `magnet_count`, `bias`.
- `sfp_events` (масив словників з рівнем/стороною/часом), `wick_clusters`
  (рівень, кількість гніт, max_wick, джерело).
- `amd_reason` — текстове пояснення поточної фази.

## Конфігурація та пороги

- Використовує ті самі `SmcCoreConfig`, що й структура: `eq_tolerance_pct`,
  `min_range_bars`, `max_lookback_bars`. Специфічні константи визначені в
  `sfp_wick.py` (`SFP_BREAK_FRACTION`, `MIN_BREAK_PCT`, `WICK_RATIO`) та
  `amd_state.py` (`_LOW_ATR_RATIO`, `_RECENT_EVENT_WINDOW`, `_TREND_POOL_MIN`).
- Під час змін цих констант треба оновлювати документацію та тести — Stage2/Stage3
  покладаються на стабільність ролей PRIMARY.

## Вихід у Stage2 / UI

- UI: `UI/publish_full_state.py` викликає
  `_to_plain_smc_liquidity` і відправляє `pools`, `magnets`, `amd_phase` як є.
- Stage2: `smc_core.liquidity_bridge.build_liquidity_hint` стискає стан у прапори
  `smc_liq_has_above/below`, відстань до найближчого PRIMARY магніта, `amd_phase`.
  Будь-які нові поля для Stage2 потрібно додавати через bridge, не змінюючи базовий
  `SmcLiquidityState` без плану міграції.

## Інваріанти

- Кожен пул має `meta.source`, щоб QA могла відслідковувати походження рівня.
- PRIMARY ролі залежать тільки від bias/типу/сторони, використовуються Stage2 для
  фільтрації; змінювати логику можна лише синхронно зі Stage2.
- AMD FSM гарантує єдину фазу в кожен момент (пріоритетна черга умов).
- `liquidity.meta` завжди містить `pool_count` і `magnet_count`, навіть якщо списки
  порожні.

## QA та тести

- Низькорівневі тести: `tests/test_smc_liquidity_*.py` (кластеризація, ролі,
  AMD-фаза). При додаванні нових ролей або порогів — додати окремий тест-кейс.
- Інтеграція: `tools/smc_snapshot_runner.py` з ключем `--show-liq` (усі поля SMC
  відображаються в JSON).

## Відомі обмеження / наступні кроки

- Немає історії пулів/магнітів, лише поточний стан — історичні «соглядки» для
  фільтрації залишено на Stage2.
- AMD FSM поки що не враховує обсяг/дельту; в майбутньому можна підключити
  `snapshot.context["whale_flow"]` через окремий гейт.
- Робота над `smc_zones + Fusion` має використовувати `SmcLiquidityState.meta` для
  нових прапорів замість зміни базових структур.
