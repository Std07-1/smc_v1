# SMC Structure Layer • Етап 2

**Статус:** у робочому стані (готовий до freeze після фінальних QA-кроків).

## 1. Мета

- Побудувати MAJOR-структурний шар для `tf_primary = 5m` (XAUUSD як еталон), який формує:
  - свінги (`swing_detector` → локальні high/low із вікном `min_swing_bars`);
  - ноги (legs) із мітками HH/HL/LH/LL та амплітудою;
  - тренд `SmcTrend` (UP/DOWN/RANGE/UNKNOWN) на базі послідовності ніг;
  - стан діапазону (`range_state = INSIDE/DEV_UP/DEV_DOWN` + `active_range`/EQ рівень);
  - події BOS/CHOCH із ATR/percent-порогами з `SmcCoreConfig`;
  - OTE-зони (0.62–0.79) по валідних імпульсах із ролями PRIMARY/COUNTERTREND/NEUTRAL;
  - bias та `last_choch_ts`.
- Результат серіалізується у `SmcStructureState` та передається як `SmcHint.structure`.
- Ніяких сигналів, ліквідності, зон, FSM або Stage3 дій на цьому етапі.

## 2. Обсяг реалізації

- **Пакети:**
  - `smc_structure/swing_detector.py` — 3-свічне правило + захист від дублікатів.
  - `smc_structure/structure_engine.py` — побудова ніг, тренд, bias, BOS/CHOCH.
  - `smc_structure/range_engine.py` — активний рендж + стан (INSIDE/DEV_UP/DEV_DOWN).
  - `smc_structure/metrics.py` — ATR (14 bar, M1 масштаб) для порогів BOS/CHOCH та відсічення «шумових» ніг.
  - `smc_structure/ote_engine.py` — відбір валідних імпульсів, побудова OTE 0.62–0.79, обмеження PRIMARY (1 per side) та meta про джерело (leg id, bias, ATR).
- **Типи:** `SmcStructureState`, `SmcStructureEvent`, `SmcOteZone` оновлені у `smc_core/smc_types.py` (swings, legs, events, `range_state`, `bias`, `meta`, `ote_zones`).
- **Engine:** `SmcCoreEngine.process_snapshot` вже вкладає структуру в `SmcHint` без додаткових бізнес-рішень; інші підшари отримують готовий state.

## 3. QA та реперні зрізи

- **Тести:**
  - `tests/test_smc_structure_basic.py` — synthetic HH/HL/LH/LL, range_state, bias, BOS/CHOCH.
  - `tests/test_smc_ote_basic.py` — побудова PRIMARY/COUNTERTREND, bias-фільтр, ліміт зон.
- **Інструменти:**
  - `tools/smc_snapshot_runner.py` — формує `SmcHint` по історії (через `build_smc_input_from_store`).
  - `tools/smc_structure_threshold_study.py` — акумуляція статистики по легам/ATR/подіям у CSV.
- **Фактичні результати (XAUUSD 5m, 2000 барів):**
  - Зріз «D» (`smc_xau_5m_2000bars_D.json`): 40 свінгів, 39 легів, `trend=DOWN`, `range_state=DEV_DOWN`, `bias=SHORT`, `events=[]`, одна SHORT PRIMARY OTE по нозі `159→163` (H 4202.55 @21:00 → L 4193.04 @21:20, зона 4198.94–4200.55). `atr_last≈1.75`, `atr_median≈1.93`, пороги `bos_min_move_atr_m1=0.6`, `bos_min_move_pct_m1=0.002` пояснюють, чому BOS/CHOCH не з’явились на «чистому спаді».
  - Зрізи «B» і «C» використовуються для тижня з явним BOS/CHOCH (2 BOS + 1 CHOCH) та сусіднього тижня; у CSV видно event_leg-id та амплітуди.

## 4. Контрольні кроки перед freeze

1. **Візуальна звірка TradingView:**
   - «Down-only» тиждень (D) уже перевірено.
   - Додати ще один ап-трендовий тиждень і один флет-тиждень: переконатися, що BOS/CHOCH і range_state відповідають тому, що видно на графіку.
2. **Golden set threshold CSV:**
   - Залишити три файли (`*_B.csv`, `*_C.csv`, `*_D.csv`) як baseline.
   - Занотувати частку легів, що проходять ATR/pct пороги, середню/медіанну амплітуду, кількість BOS/CHOCH.
3. **Контрактний freeze:**
   - Після пунктів 1–2 поставити тег/гілку `smc_structure_v1_frozen`.
   - Заборонити зміни в `SmcStructureState`, `SmcStructureEvent`, `SmcOteZone` без нового RFC.

## 5. Межі та відкладені ідеї (Етап 2.5)

- Не додаємо session-level структуру, додаткові типи подій або «session_ote_zones» у SmcTypes.
- Допускається лише пасивна meta-довідка (напр., `session_label` чи `session_range_high/low`) без впливу на MAJOR-layer логіку.
- Справжній session-layer запускаємо після завершення Етапу 3 (liquidity), щоб розуміти, які локальні сетапи потрібні Stage3.

## 6. Подальші етапи

- Перейти до Етапу 3 (`smc_liquidity`) із уже стабільним structure state.
- Використовувати golden set для перевірки, що зміни ліквідності/порогів не ламають MAJOR-структуру.

## 7. Аналітика BOS/CHOCH порогів (стан на 2025-12-06)

- **Покриття даними:** три унікальні зрізи `smc_xau_5m_2000bars_{10_14,17_21,D}` (≈3 активні торгові дні). A/B/C дублюють перші два вікна і в статистику не входять.
- **Структурні метрики:** ~155 легів, із них 6 позначені як BOS/CHOCH (14 листопада — 3 BOS SHORT; 21 листопада — 2 BOS SHORT + 1 CHOCH LONG; зріз D подій не містить).
- **Розподіли амплітуд:**
  - Усі леги: median ≈2.95 ATR (~0.25%), Q75 ≈4.65 ATR (~0.39%), P90 ≈7.1 ATR (~0.63%).
  - BOS/CHOCH: min ≈1.33 ATR (~0.13%), median ≈4.4 ATR, P75 ≈11.5 ATR, max ≈20.2 ATR (~2%).
- **Вплив поточних порогів:**
  - Фільтр `delta ≥ max(atr * bos_min_move_atr, mid_price * bos_min_move_pct)` із 0.6 ATR / 0.2% пропускає ≈64% усіх легів та 5/6 BOS (одна подія з ~0.13% відсікається).
  - Варіант 1.3 ATR / 0.35% пропускає ≈33% легів та лише 4/6 BOS — дві події втрачаємо через підвищений %-поріг (ATR частина майже не впливає, бо більшість легів і так >1 ATR).
- **Висновки:**
  - Поточна вибірка годиться для розуміння порядку величин (типова нога ≈3 ATR/~0.25%, «справжні» BOS ≥0.3–0.4%).
  - Для остаточної фіксації `bos_min_move_*` потрібно щонайменше 30–50 унікальних BOS/CHOCH у різних режимах (тренд, флет, висока/низька вола). Після розширення вибірки перевірити кілька кандидатів (наприклад 0.6/0.002; 1.0/0.0025; 1.3/0.003–0.0035) й виміряти втрати реальних BOS.
  - До того моменту пороги лишаємо без змін, документуючи нинішні спостереження як «фазу 1» аналізу.
