# Stage6 (4.2 vs 4.3): повний запис етапу

Дата: 2025-12-21

Цей документ — максимально змістовний «журнал» того, як ми будували, тестували та налаштовували Stage6 (класифікацію 4.2 / 4.3 / UNCLEAR) у AiOne_t.

Мета: щоб після відкату або міграції можна було відтворити логіку, методологію тюнінгу, критерії успіху та інструменти перевірки.

Дотичний документ (UI/рендер/офлайн-відтворення візуалізації):

- `docs/architecture/smc_context_stage6_ui_v2_playbook.md`

---

## 1) Що таке Stage6 (і чого він НЕ робить)

Stage6 — це детермінований «технічний розбір» (SMC context explainer), який відповідає на питання:

- Після sweep/маніпуляції в HTF‑контексті поведінка ринку більш схожа на:
  - **4.2**: continuation / rejection (повернення до діапазону, відхилення, пост‑sweep BOS у бік діапазону), або
  - **4.3**: break / accept / hold (закріплення за swept‑рівнем + підтверджуючі факти).

Stage6 НЕ є:

- торговим сигналом;
- входом у позицію;
- ризик‑менеджментом;
- «магічним» передбаченням.

Ключовий принцип довіри: Stage6 має право чесно повертати **UNCLEAR** і пояснювати чому.

---

## 2) Архітектура Stage6: де що живе

### 2.1 SMC-core: «raw» рішення

- Raw Stage6 обчислюється в `smc_core/stage6_scenario.py::decide_42_43()`.
- Це рішення:
  - детерміноване;
  - не має таймерів/TTL/гістерезису;
  - містить `scenario_id`, `direction`, `confidence`, `why[]`, `key_levels`, `telemetry`.

Важливо: `key_levels["smc"]` використовується як JSON-friendly словник для UI та QA.

### 2.2 SmcStateManager (app): «stable» рішення (anti-flip / hysteresis)

- Стабілізація винесена за межі core (щоб core залишався чистою класифікацією).
- Реалізація: `app/smc_state_manager.py::SmcStateManager.apply_stage6_hysteresis()`.
- «Stable» використовується для UX у UI (щоб не було постійних фліпів на шумі), але при цьому не має «брехати»:
  - не приховувати UNCLEAR назавжди;
  - мати зрозумілі причини блокування/перемикання;
  - підтримувати «hard invalidation» (коли є жорсткі факти інвалідації).

### 2.3 Конфіг/рейки

- Runtime-параметри Stage6 живуть у `config/config.py` як `SMC_RUNTIME_PARAMS["stage6"]`.
- Це бізнес-рейки UX, не керуються через ENV.

Поточні значення (SSOT):

- `ttl_sec=180`
- `confirm_bars=2`
- `switch_delta=0.08`
- `micro_confirm_enabled=True`
- `micro_ttl_sec=90`
- `micro_dmax_atr=0.80`
- `micro_boost=0.05`
- `micro_boost_partial=0.02`

---

## 3) Еволюція Stage6 (хвилі та ключові рішення)

Нижче — стислий, але повний опис того, як ми прийшли до «успішного» варіанту.

Джерела істини по змінах:

- `docs/architecture/migration_log.md` (S6 / S6.1 / S6.2)
- `UPDATE_CORE.md` (Stage6 чесний UNCLEAR + SCORE_DELTA)

### 3.1 Базова проблема, яку ми вирішували

Якщо Stage6 завжди змушений вибирати 4.2 або 4.3, то на шумі він неминуче робить "confident lie".
Це руйнує довіру трейдера і робить будь-яку стабілізацію (anti-flip) небезпечною: stable починає «липнути» до випадкового напрямку.

Тому Stage6 отримав:

- **чесний UNCLEAR**
- **пояснюваність** (why + unclear_reason)
- **стабілізацію поза core** з можливістю повернення у UNCLEAR (decay) і з можливістю жорсткої інвалідації.

### 3.2 S6 (2025-12-21): UNCLEAR reasons + SCORE_DELTA + gate структури

Зміни:

- `telemetry.unclear_reason` для прозорості:
  - hard-gates (приклади): `NO_LAST_PRICE`, `NO_HTF_FRAMES`, `ATR_UNAVAILABLE`, `NO_STRUCTURE`.
  - soft-gates: `LOW_SCORE` та `CONFLICT`.
- Введено правило **SCORE_DELTA**: якщо |score_42 - score_43| < delta → `UNCLEAR(CONFLICT)`.
- Додано гейт `NO_STRUCTURE` (якщо факти структури недостатні).

Навіщо:

- Прибрати «вигадані» 4.2/4.3 там, де даних/фактів замало або скоринг майже рівний.

Тест-гейт:

- `pytest tests/test_smc_stage6_scenario.py`.

### 3.3 S6.1 (2025-12-21): асиметричний anti-flip + hard_invalidation

Проблема:

- Симетричний anti-flip з TTL інколи або:
  - пропускав «погані фліпи» 4_3→4_2 (шум), або
  - блокував справжню інвалідацію (коли вже є hard-факти).

Рішення:

- Додано hard_invalidation (override TTL/confirm/delta) у `SmcStateManager`.
- Зроблено асиметрію:
  - `4_2 → 4_3` може пробити TTL при `hold_above_up` або strong micro-confirm.
  - `4_3 → 4_2` жорсткіший: без `failed_hold_up` switch не вважається валідним;
    при BOS_DOWN після sweep без `failed_hold_up` — швидка інвалідація у `UNCLEAR`.

Чому це дає «довіру»:

- Ми дозволяємо зміну stable тільки коли є причинно‑наслідковий доказ, а не просто шумний raw.

Тест-гейт:

- `pytest tests/test_smc_stage6_hysteresis.py`.

### 3.4 S6.2 (2025-12-21): анти-конфліктні факти + QA лічильники

Проблеми:

- Після sweep могли одночасно зʼявлятися сигнали `BOS_UP` і `BOS_DOWN` (chop), і це псувало скоринг.
- Подвійний bias у скорингу (HTF bias і додатковий HTF‑Lite bias як окремий внесок) множив `UNCLEAR(CONFLICT)` і робив картину «параноїдальною».

Рішення:

- P0b: якщо після sweep одночасно є BOS_UP і BOS_DOWN → `events_after_sweep.chop=true` і не додаємо обидва внески.
- P0c: HTF‑Lite bias не додається як окремий скоринговий факт, якщо вже є контекстний bias.
- Узгодження рівнів:
  - `hold_level_up` показує інвалідаційний рівень (max з 5m/HTF),
  - `failed_hold_up` рахується на 5m `range_high`, щоб не ламати sweep→failed_hold при далекому HTF рівні.
- QA: додаємо лічильники `hard_invalidation_count` та `flip_pairs_by_reason`.

Тест-гейт:

- `pytest tests/test_smc_stage6_scenario.py` (є кейси для P0b/P0c).

---

## 4) Як ми тестували Stage6 (DoD практично)

Stage6 має дві площини тестування:

### 4.1 Юніт-тести core (raw рішення)

Файл: `tests/test_smc_stage6_scenario.py`.

Покриває (приклади з тестів):

- 4_2 continuation / rejection після sweep з BOS_DOWN.
- 4_3 break/hold після sweep з підтвердженням.
- P0c: "HTF‑Lite bias" не має бути окремим скоринговим фактом, якщо є контекстний bias.
- P0b: chop‑випадок (BOS_UP і BOS_DOWN після sweep) має маркуватися і не має ламати скоринг.

Запуск:

- `; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" -m pytest -q tests/test_smc_stage6_scenario.py`

### 4.2 Юніт-тести стабілізації (stable рішення)

Файл: `tests/test_smc_stage6_hysteresis.py`.

Покриває:

- confirm_bars: без підтвердження — не фліпаємо;
- ttl_sec: блокування фліпа до завершення TTL;
- UNCLEAR: не затирає stable одразу;
- decay_to_unclear_after: повернення stable у UNCLEAR після N підряд raw UNCLEAR;
- strong_override: може пробити TTL при великому score-diff;
- hard_invalidation: дозволяє обхід TTL при `hold_above_up` / `micro_confirm` і інвалідацію при `bos_down_no_failed_hold`.

Запуск:

- `; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" -m pytest -q tests/test_smc_stage6_hysteresis.py`

---

## 5) Як ми робили QA на реальних даних (і навіщо це критично)

Юніт-тести захищають логіку, але не дають відповіді "чи довіряти".
Тому Stage6 має окремий QA-інструмент, який працює на реальних snapshot.jsonl.

### 5.1 SSOT інструмент

Скрипт: `tools/qa_stage6_scenario_stats.py`.

Що робить:

- бере 5m snapshot як primary;
- автоматично підтягує 1m/1h/4h снапшоти того ж символа з `datastore/`;
- проганяє SMC-core на останніх `--steps` кроках;
- застосовує `SmcStateManager.apply_stage6_hysteresis()`;
- рахує:
  - частоти 4_2/4_3/UNCLEAR (raw/stable);
  - `UNCLEAR reason` розподіли;
  - flip-rate та причини;
  - опційний пост‑фактум outcome (TP/SL у ATR на 1m горизонті).

Чому це не "бектест":

- outcome тут — не стратегія, а sanity-check: чи direction хоча б не протирічить руху в середньому.

### 5.2 Як ми запускали (PowerShell)

Приклад (схожий на той, що реально використовували):

- `; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" -m tools.qa_stage6_scenario_stats --path datastore/xauusd_bars_5m_snapshot.jsonl --steps 120 --warmup 220 --horizon-bars 60 --tp-atr 1.0 --sl-atr 1.0 --out reports/stage6_stats_xauusd_h60_v6_2.md --exemplars 12`

Альтернативно (довший горизонт):

- `; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" -m tools.qa_stage6_scenario_stats --path datastore/xauusd_bars_5m_snapshot.jsonl --steps 500 --horizon-bars 120 --tp-atr 1.0 --sl-atr 1.0 --out reports/stage6_stats_xauusd_h120.md`

### 5.3 Як ми інтерпретували метрики

Ми дивилися на:

- **raw UNCLEAR rate**: високий raw UNCLEAR — нормальний (чесність > частота).
- **stable UNCLEAR rate**: не має бути 0% "за будь‑яку ціну"; але й не має бути 80% (тоді stable не дає користі).
- **flips**: критично, щоб flips були рідкі та пояснювані.
- **flip_reasons** і `flip_pairs_by_reason`: чи є "погані" фліпи, і чому.
- **unclear_reason_counts**: чи UNCLEAR виникає переважно через LOW_SCORE/CONFLICT (тобто через невизначеність), а не через hard-gates (NO_*), що свідчить про проблеми з даними.
- **hard_invalidation_count**: це окремий важіль; має бути рідкісним, але не нульовим, якщо дійсно трапляються інвалідації.
- Outcome (WIN/LOSS/NO_HIT) — як sanity-check, не як KPI.

---

## 6) Приклади результатів, які вважалися "успішними"

Ціль Stage6 — довіра, а не максимізація winrate.
Успіх визначався як:

- UNCLEAR не приховується (є причини і приклади);
- після стабілізації фліпи рідкі і мають пояснення;
- немає очевидних логічних протиріч у exemplars (коли дивишся руками);
- у середньому direction не «анти‑корельований» з рухом (sanity-check).

### 6.1 Приклад: `reports/stage6_stats_xauusd_h60_v6_2.md`

Факти з репорту:

- кроків: 120
- raw: `{'4_2': 31, '4_3': 39, 'UNCLEAR': 50}`
- stable: `{'4_2': 71, 'UNCLEAR': 10, '4_3': 39}`
- flips: 3
- raw UNCLEAR rate: 41.67%
- stable UNCLEAR rate: 8.33%
- unclear_reason_counts: `{'LOW_SCORE': 46, 'CONFLICT': 14}`

Інтерпретація:

- raw чесно часто UNCLEAR (це нормально для noisy ділянок).
- stable зменшує UNCLEAR, але не зводить його до нуля.
- flips небагато, причини контрольовані (`confirm`, `decay_unclear`).

### 6.2 Приклад: `reports/stage6_stats_xauusd_h120.md`

Факти з репорту:

- кроків: 500
- raw: `{'4_3': 234, '4_2': 87, 'UNCLEAR': 179}`
- stable: `{'4_3': 500}`
- flips: 1
- raw UNCLEAR rate: 35.80%
- stable UNCLEAR rate: 0.00%

Важлива примітка:

- stable=100% одного сценарію може бути нормою на конкретній ділянці, але це також "червоний прапорець" для перевірки:
  - чи не занадто липкий anti-flip;
  - чи не зависокий TTL;
  - чи нема перекосу через bias/держання stable.

Тому ми завжди дивилися exemplars і flip_reasons, а не тільки частоти.

---

## 7) Типові проблеми, які ми ловили і як вирішували

### 7.1 «Confident lie» (майже рівний скоринг)

Симптом:

- 4_2 і 4_3 близькі за score, але алгоритм вибирає один.

Рішення:

- SCORE_DELTA → `UNCLEAR(CONFLICT)`.

### 7.2 «Chop після sweep» (BOS в обидва боки)

Симптом:

- одночасно `BOS_UP` і `BOS_DOWN` після sweep.

Рішення:

- P0b: маркуємо як chop і не додаємо обидва внески у скоринг.

### 7.3 Подвійний HTF bias

Симптом:

- множиться `UNCLEAR(CONFLICT)` і "дивні" why.

Рішення:

- P0c: не додавати HTF‑Lite bias як окремий скоринговий факт при наявному контекстному bias.

### 7.4 Stable «липне» і не повертається в нейтраль

Симптом:

- raw стає UNCLEAR довго, а stable тримає старий сценарій без сигналу, що це вже "неактуально".

Рішення:

- decay_to_unclear_after=N (у SmcStateManager), щоб повертати stable у UNCLEAR після N підряд UNCLEAR.

### 7.5 Потрібно швидко пробивати TTL при справжній інвалідації

Симптом:

- TTL блокує switch навіть коли є hard-факт.

Рішення:

- hard_invalidation (override TTL/confirm/delta) з причинами `hold_above_up`, `micro_confirm`, `bos_down_no_failed_hold`.

---

## 8) Практичний чеклист відтворення Stage6 після відкату

1) Переконатися, що Stage6 у core повертає `unclear_reason`/`score`/`why`.

- Запустити `pytest tests/test_smc_stage6_scenario.py`.

2) Переконатися, що anti-flip працює як задумано.

- Запустити `pytest tests/test_smc_stage6_hysteresis.py`.

3) Прогнати QA на 5m snapshot із підтягнутими 1m/1h/4h.

- Запустити `tools/qa_stage6_scenario_stats.py` з `--exemplars 12`.

4) Перевірити UI-рендер і зрозумілість stable/raw/pending.

- Дивись `docs/architecture/smc_context_stage6_ui_v2_playbook.md`.

---

## 9) Ризики / важливі застереження

- Підбір порогів (min_score/score_delta та ваги фактів) у `smc_core/stage6_scenario.py` наразі «жорсткий» (мінімальний диф). Це зручно для стабільності, але гірше для швидкого тюнінгу без деплою.
- Anti-flip може «поліпшити UX», але погіршити прозорість. Тому stable завжди супроводжуємо raw і поясненнями, і має існувати шлях повернення у UNCLEAR.
- Офлайн‑реплей без мультитаймфреймового входу може показувати іншу «картинку» і інші гейти. Для відтворення потрібно mirror продакшн-входів (див. playbook).
