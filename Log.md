# Log змін (AiOne_t / smc_v1)

Цей файл — журнал змін у репозиторії. Формат записів: дата/час → що зроблено → де зроблено → причина → тести/перевірки → ризики/нотатки/очікуваний результат.

## ВАЖЛИВА ІНЖЕНЕРНА ПРИМІТКА (не загубити) → 3.2.2x: канонічне джерело барів у production payload

- Контекст → DAILY candidates (3.2.2b/3.2.2c) зараз залежать від `asset.ohlcv_frames_by_tf` (в replay/QA це вже подається), але в live цей блок може бути відсутній → `levels_candidates_v1` буде порожнім.
- Пропозиція кроку → **3.2.2x (після 3.2.2c, до sessions/range)**: у producer/publisher (де формується asset/state для UI) додати **мінімальний** `ohlcv_frames_by_tf`, без HTTP-запитів з builder.
- Мінімальні обсяги →
 	- `1h`: останні ~72 `complete` бари,
 	- `5m`: останні ~600 `complete` барів.
- Вплив/обмеження → не змінює SMC-core і не змінює UI; це лише гарантія, що candidates (daily/session/…) працюватимуть у live перед cutover.
- Ризик, якщо пропустити → перед cutover можна «вистрілити собі в ногу»: у production рівні не з’являться, бо немає джерела барів у payload.

## 2025-12-24 — Bugfix: replay_snapshot_to_viewer падав на WICK_CLUSTER timestamps

- Симптом: `tools.replay_snapshot_to_viewer` падав з `AttributeError: 'str' object has no attribute 'isoformat'` у `smc_liquidity/sfp_wick.py` під час серіалізації `wick_clusters`.
- Причина: `_track_wick_clusters` міг підхопити `first_ts/last_ts` із `prev_wick_clusters` у `snapshot.context`, де timestamp інколи був ISO-рядком (JSON-friendly), а не `pd.Timestamp`.
- Фікс: у [smc_liquidity/sfp_wick.py](smc_liquidity/sfp_wick.py) додано нормалізацію `first_ts/last_ts` до `pd.Timestamp` перед побудовою `wick_meta` та `SmcLiquidityPool`.
- Тести/перевірки: додано регресійний тест [tests/test_smc_sfp_wick.py](tests/test_smc_sfp_wick.py) (`test_wick_cluster_prev_first_ts_string_does_not_crash`).

## 2025-12-24 — UI_v2 trader_view: прибрати *_P, POI distance-gate, анти-«фантоми» + story execution

- Ціль: зменшити когнітивний шум у trader view, не чіпаючи SMC truth.
- Зміни у [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js):
 	- Нормалізація типів pools: суфікс `_P` прибирається (напр. `EQL_P` → `EQL`).
 	- POI distance-gate: у trader view POI зони показуються, лише якщо ціна в зоні або `distance_atr <= 1.5`.
 	- Анти-«фантоми»: якщо `setZones([])` приходить порожнім апдейтом — прибираємо геометрію, DOM-лейбли та markers.
 	- Story execution: у trader view лишаємо тільки `SWEEP` та `RETEST_OK`; у tooltip `RETEST_OK(source=break_hold)` показуємо як `RECLAIM`.
- Тести/перевірки: ручна перевірка в UI (режими `?trader_view=1` та без нього).
- Ризики/нотатки: поріг `distance_atr` може потребувати калібрування під інструмент/TF.

## 2025-12-25 — UI_v2: tooltip execution «видно рідко» (таймер скидався через autoscale)

- Симптом: у режимі з текстом лише в tooltip (execution-стрілки без лейблів) підказка з `EXEC:` з’являлась дуже рідко.
- Причина: ключ стабільності курсора (`pointKey`) включав `y`, який змінюється під час live/update/autoscale навіть без руху миші → show-timer (200ms) постійно скидався.
- Фікс: у [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js) `pointKey` зроблено стабільним по `(x,time)` (y прибрано).
- Тести/перевірки: ручна перевірка в UI_v2 на live/replay потоці.

## 2025-12-25 — UI_v2 trader_view: FAR_KEY (1 маяк) + POI квоти/гейти + стилі рамкою (без «кольорового цирку»)

- Ціль: прибрати “чому завжди лише FVG” і дати 1 далекий контекстний маяк, не повертаючи шум; усе — лише presentation-layer.
- Зміни у [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js):
  - FAR_KEY: у trader view додаємо максимум 1 «далекий маяк» з типів `PDH/PDL/PWH/PWL/HTF_SWING`
    за гейтами `distance_atr<=8` і (`score>=0.8` або `is_htf=true`), без дубля по ціні;
    візуалізація слабка (нейтральний dotted бейдж `FAR`, без ліній на полі).
  - POI: type-aware distance gate (`FVG<=1.5 ATR`, `OB/Breaker<=2.75 ATR`) + квоти (`total=3`, `max_fvg=1`, `max_ob_breaker=1`, пріоритетно намагатись взяти хоча б 1 OB/Breaker якщо є).
  - Стилі POI в trader view: розрізнення формою/рамкою (FVG solid тонка, OB solid товстіша, Breaker dashed) + нейтральна палітра для POI (без «кольорового цирку»).
  - DOM-лейбли POI в trader view: короткі `FVG/OB/BRK`.
- Дотично (підготовка даних під гейти): у [UI_v2/web_client/app.js](UI_v2/web_client/app.js) мапінг pools дістає `distance_atr/score/is_htf` best-effort (в т.ч. з `pool.meta`), non-breaking.
- Тести/перевірки: ручна перевірка в UI_v2 у режимі `?trader_view=1` (поява `FAR`, диверсифікація POI не лише FVG, читабельність рамок/лейблів).
- Ризики/нотатки:
 	- Якщо бекенд не віддає `distance_atr/score/is_htf` для частини pools — FAR_KEY може не з’являтись (це очікувано).
 	- Пороги ATR/квоти можуть потребувати калібрування під інструмент/TF.

## 2025-12-25 — UI_v2 trader_view: always-on чіпи POI + crosshair tooltip (hit-test) + жорсткий whitelist рівнів

- Контекст: у trader_view не можна покладатись на hover по DOM-елементах; тултіп має працювати детерміновано від crosshair.
- Зміни у [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js):
 	- Always-on POI «чіпи» в куті зони: текст формату `FVG 5m` / `OB 5m` / `BRK 5m` (+ `(D/S)` якщо є direction), позиціонування в верхньому куті box’а (а не по центру).
 	- Єдиний crosshair tooltip (без DOM-hover): hit-test по
  		- zone: `price ∈ [low,high]` і `time ∈ [t1,t2]`;
  		- level: `abs(price-level.price) <= tol`;
  		- event: `time ~= event.ts` (вікно ±1 бар).
 		Показує максимум 2–4 рядки (`POI`, `Level`, `Event`).
 	- Жорсткий whitelist рівнів у trader_view: дозволені `BSL/SSL/EQH/EQL/PDH/PDL/RANGE_H/RANGE_L`; відсів `RANGE_MID`/mid/quarter.
 	- Антишум: у trader_view вимкнено рендер `ranges` та `OTE` (вони дають зайві горизонталі/смуги).
- Тести/перевірки: ручна перевірка в UI_v2 (`?trader_view=1`): чіпи видно завжди; tooltip з’являється при попаданні в зону/біля рівня/на події; на екрані ≤3–4 правих ярлики рівнів і ≤3 POI.
- Ризики/нотатки: якщо вхідні payload-и не викликають `setLiquidityPools/setZones` на кожному тіку, tooltip може тимчасово працювати по останньому selection (це очікувано).

## 2025-12-25 — Bugfix UI_v2 trader_view: POI чіпи/tooltip були порожні (zones=0) через мапінг poi_zones

- Симптом: у `?trader_view=1` не видно POI-підписів/тултіпів; у консолі `ui: zone_labels=1 ... zones=0`.
- Причина:
 	- бекенд віддає POI-зони у `state.zones.raw.poi_zones`, але елементи часто не мають `poi_type` (є лише `zone_type`),
 	- а `chart_adapter.js` у trader_view вважає зону POI лише якщо `poi_type` непорожній (або label містить `POI`) → `_isPoi=false` → зони відфільтровуються до нуля.
- Фікс: у [UI_v2/web_client/app.js](UI_v2/web_client/app.js)
 	- у trader_view пріоритетно беремо `raw.poi_zones` (fallback: `active_zones` → `zones`),
 	- для `poi_zones` заповнюємо `poi_type` best-effort (`poi_type`→`zone_type`→`type`→label), щоб UI коректно розпізнавав POI.
- Дотично: піднято cache-bust версію `?v=...` у [UI_v2/web_client/index.html](UI_v2/web_client/index.html), щоб гарантовано підтягнути оновлений фронтенд.

## 2025-12-25 — Bugfix UI_v2 trader_view: POI DOM-лейбли могли бути невидимі через відсутній origin_time у poi_zones

- Симптом: у звичайному режимі підписи зон/POI видно, а у `?trader_view=1` POI-бокси могли бути, але “чіпів” (DOM-лейблів) та стабільного hit-test тултіпа не було.
- Причина: `raw.poi_zones` інколи приходять без `origin_time/origin_ts`, але з альтернативними полями (`start_time/start_ts/from/time_start`).
	UI мапив `origin_time` надто вузько → `origin_time=null` → DOM-лейбли ховались (бо `timeToCoordinate` не мав валідного часу).
- Фікс: у [UI_v2/web_client/app.js](UI_v2/web_client/app.js) розширено евристику `origin_time`: додаємо fallback на `start_time/start_ts/from/time_start` (і аналоги в `meta`).
- Дотично: піднято cache-bust версію `?v=...` у [UI_v2/web_client/index.html](UI_v2/web_client/index.html) до `20251225_3`.

## 2025-12-25 — UI_v2 normal: «людські» теги рівнів + скорочення сесій/екстремумів + чистіші pools

- Ціль: прибрати `*_P` (preview/internal) і не різати маркери, щоб трейдер бачив зрозумілі рівні (EQL/EQH, PDH/PDL, BSL/SSL, RANGE, сесійні H/L) і менше «слідів».
- Зміни:
 	- У [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js) normal view більше не обрізає типи через `slice(0,6)`; додано скорочення: `RANGE_H/L` → `RNG H/L`, `SESSION_HIGH/LOW` → `AS/LDN/NY H/L` (fallback `SES H/L`).
 	- У [UI_v2/web_client/app.js](UI_v2/web_client/app.js) таблиця pools показує нормалізований тип без `_P`; також прокинуто `session_tag` (best-effort) для читабельних сесійних міток.
 	- У [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js) пули ліквідності з явними прапорами `taken/swept/mitigated/invalidated` або `active=false` не рендеряться (лише presentation, SMC truth без змін).
- Тести/перевірка: ручна перевірка в UI_v2 (normal режим): EQL_P → EQL; `SESSION_*` не обрізаються; «зняті» pools зникають за наявності lifecycle-полів.

## 2025-12-25 — UI_v2 normal: zones без «слідів» + лише 5m BOS/CHOCH + менш агресивний near-filter

- Ціль: у normal режимі не тримати на графіку відпрацьовані зони/структуру і не «гасити» далекі 5m POI надто рано; усе — presentation-only.
- Зміни у [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js):
 	- Zones: додано фільтр, який **не рендерить** зони зі станами `TOUCHED/TAPPED/MITIGATED/FILLED/INVALIDATED` або з `invalidated_time` (якщо lifecycle-поля присутні в payload).
 	- Events: у normal режимі лишаємо лише `BOS/CHOCH` з TF `5m` (якщо `tf` присутній) + додаємо cap історії маркерів (останні 12), щоб зменшити шум.
 	- Zones (1m-view): послаблено distance-gate для 5m POI (вікно відсікання збільшено), щоб «дальні» зони не зникали занадто агресивно при русі ціни.
- Тести/перевірка: ручна перевірка в UI_v2 (normal): після торкання/мітігації/інвалідації зона зникає; BOS/CHOCH показуються лише 5m; у 1m-view 5m POI не зникають так швидко.

## 2025-12-25 — UI_v2 normal: ключові рівні (EQH/EQL/PDH/PDL/BSL/SSL) повернуті, шум зменшено

- Контекст: у normal режимі пропадали трейдерські ключові рівні (EQH/EQL, PDH/PDL, BSL/SSL), а натомість домінували дрібні близькі рівні → шум.
- Зміни у [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js):
  - Pools selection (normal): ключові типи пріоритезуються і показуються як компактні бейджі (без горизонтальних сегментів), дрібні добираються лише в малому бюджеті.
  - Pools filter (normal): ключові типи не відсікаються критеріями `strength/touches`.
  - Нормалізація назв: `normalizePoolType()` розширено для старих/варіативних назв (консервативні патерни для EQH/EQL, PDH/PDL, PWH/PWL, SESSION_*, BSL/SSL).
- Тести/перевірка: ручна перевірка в UI_v2 (normal): з’являються EQH/EQL/PDH/PDL/BSL/SSL якщо вони є у payload; кількість «дрібних» ліній/сегментів суттєво менша.

## 2025-12-25 — UI_v2: аудит pools/levels (engine → smc_hint → viewer_state → рендер) + виправлення нестиковок назв

- Додано дослідницький звіт: [docs/ui/ui_v2_pools_levels_audit_2025-12-25.md](docs/ui/ui_v2_pools_levels_audit_2025-12-25.md).
- Зміни у [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js):
  - `WICK_CLUSTER` більше не маскується як `WICK` (прибрано плутанину в тегах).
  - `RANGE_EXTREME` нормалізується до `RANGE_H/RANGE_L` за `meta.side`.
  - Normal selection: `SESS_H/SESS_L` виправлено на `SESSION_HIGH/SESSION_LOW`.
  - Normal labels: `COUNTERTREND` позначається як `C`.

## 2025-12-25 — Docs: as-is аудит UI_v2 SMC A–E (levels/pools/zones/structure/targets)

- Додано новий звіт “як є зараз” (truth → viewer_state → UI_v2), з dataflow, edge cases (preview/close, restart) і таблицею параметрів з посиланнями на файли/рядки:
 	- [docs/ui/ui_v2_smc_audit_current_state_2025-12-25.md](docs/ui/ui_v2_smc_audit_current_state_2025-12-25.md)
- Тести/перевірки: не застосовувались (документація).

## 2025-12-24 — SMC producer: стабільність стану при `fast_symbols` флапах

- Симптом: інколи UI показує `Trend/Bias/Range/AMD = UNKNOWN` і немає pools/zones, хоча тики/ціна є.
- Ймовірний тригер: `fast_symbols` може тимчасово повертати неповний список → `smc_producer` видаляв asset зі `SmcStateManager`, а при повторному додаванні ініціалізував порожній INIT-стан.
- Фікс: у [app/smc_producer.py](app/smc_producer.py) оновлення `fast_symbols` стало недеструктивним:
 	- removed-символи не `pop()` з state; замість цього ставимо `signal=SMC_PAUSED` + hint, але **зберігаємо останній `smc_hint`**.
 	- додано юніт-тест [tests/test_smc_producer_fast_symbols_update.py](tests/test_smc_producer_fast_symbols_update.py).

## 2025-12-24 — SMC producer: не затирати `smc_hint` gated-empty Stage0 (preserve last known)

- Симптом: при Stage0 gate (наприклад `NO_5M_DATA`/`INSUFFICIENT_5M`/`STALE_5M`) продюсер міг записати `SmcHint` з `structure/liquidity/zones=None`.
- Наслідок: це перезаписувало попередній валідний стан → UI ставав повністю порожнім (`Trend/Bias/AMD = UNKNOWN`).
- Фікс: у [app/smc_producer.py](app/smc_producer.py) додано правило `_preserve_previous_hint_if_gated(...)`:
 	- якщо новий hint має `meta.gates` і він gated-empty, а попередній hint містить хоча б один блок (`structure`/`liquidity`/`zones`), то зберігаємо попередній, але оновлюємо `meta` з нового та ставимо `meta.smc_hint_preserved=true`.
- Тести/перевірки: додано [tests/test_smc_producer_preserve_hint_on_gates.py](tests/test_smc_producer_preserve_hint_on_gates.py).
- Ризики/нотатки:
 	- UI може показувати “останній відомий” стан, але це краще за порожній екран; причина прозоро видима через `meta.gates/history_state/tf_health`.

## 2025-12-24 — Рейка процесу: заборона змін без `Log.md` (log-gate)

- Вимога: записи в `Log.md` — правило топ-рівня (вище тестів/чат-відповідей).
- Зміна: додано рейку-скрипт [tools/check_log_updated.py](tools/check_log_updated.py).
 	- Політика: якщо у `git diff` є зміни у системних файлах (код/конфіг/тести/UI/tools), то `Log.md` має бути серед змінених файлів.
 	- Артефакти/дані (наприклад `reports/`, `datastore/`, `tmp/`) не вимагають Log.md.
- Примітка: скрипт використовує лише `git diff` (read-only), без жодних git-операцій.
 	- Інтеграція: додано hook `log-md-required` у [.pre-commit-config.yaml](.pre-commit-config.yaml), і додано `pre-commit` у [requirements-dev.txt](requirements-dev.txt).

## 2025-12-24 — Висновки з QA-прогону (XAUUSD): інваріанти та фокус

- Головний шумогенератор: **pools/WICK_CLUSTER**, не zones.
 	- Preview vs close по pools нестабільний (низький jaccard, десятки preview_only/close_only на бар).
 	- Дуже короткий lifetime у pools (частка lifetime<=1/<=2 висока) → фліккер як системна норма.
 	- “Вбивця довіри”: `touched_late` після `invalidated_rule` та `evicted_cap` (semantics remove ≠ правда).
- Zones: між preview/close стабільні, але є “килим” overlap/dup (потрібен dedup/merge у presentation-layer).
- Інваріанти/обмеження (обов’язково дотримуватись):
 	- **CHOCH не чіпаємо** (це базовий “truth” індикатор).
 	- Формування BOS також **не чіпаємо** (допускаємо лише presentation-політики без зміни детекції).
 	- Не чіпаємо “успішні” магніти/пули (EQL/EQH тощо) без прямої задачі.
 	- Ключова ідея: **розділити “існує в правді” і “видиме в UI”**; `evicted_cap` має ставати hidden/archived, а не removed.
- План робіт (узгоджений напрямок):
 	- Спершу low-risk UI/presentation стабілізація (matured-only/anti-flicker, hidden-евікшн, маркери touched).
 	- Далі — lifecycle/ID семантика саме для pools (найвищий ROI по `touched_late`).
 	- Після цього — dedup/merge зон по IoU (читабельність, без зміни “truth”).

## 2025-12-24 — UI_v2 presentation: pools close-only + matured-only (age>=2) + лічильники truth/shown

- Ціль: різко зменшити фліккер pools без зміни SMC truth (за результатами QA: pools/WICK_CLUSTER — головний шумогенератор).
- Зміни:
 	- У [UI_v2/viewer_state_builder.py](UI_v2/viewer_state_builder.py) введено різні пороги newborn:
  		- zones: `MIN_CLOSE_STEPS_BEFORE_SHOW_ZONES=1` (як було за замовчуванням);
  		- pools: `MIN_CLOSE_STEPS_BEFORE_SHOW_POOLS=2` (matured-only).
 	- Pools зроблено **close-only**: на preview pools не віддаємо в `viewer_state.liquidity.pools`.
 	- Додано `viewer_state.liquidity.pools_meta` з лічильниками truth vs shown:
  		- `truth_count`, `shown_count`, `filtered_preview_count`, `filtered_newborn_count`, `dropped_by_cap_count`, `policy`.
 	- Контракт розширено non-breaking: [core/contracts/viewer_state.py](core/contracts/viewer_state.py) (`SmcViewerLiquidity.pools_meta`).
- Тести/перевірки:
 	- Оновлено [tests/test_ui_v2_viewer_state_builder.py](tests/test_ui_v2_viewer_state_builder.py) під нову політику (pools стають видимими з 3-го close).

## 2025-12-24 — UI_v2 presentation: cap-evicted pools => hidden (TTL) + причини в `pools_meta`

- Ціль: зняти “вбивцю довіри” для pools — коли сутність «пропадає» через cap/top-K і потім раптом фіксується як touched_late.
 	- Важливо: це **лише presentation-логіка**; SMC truth/детекцію не змінюємо.
- Зміни:
 	- У [UI_v2/viewer_state_builder.py](UI_v2/viewer_state_builder.py) додано кеш-політику для pools:
  		- якщо pool був показаний у UI, а на наступному close вилетів за `MAX_POOLS` (cap), він помічається як `hidden` у кеші на `POOLS_HIDDEN_TTL_CLOSE_STEPS` close-кроків;
  		- у `viewer_state.liquidity.pools_meta` додано: `hidden_count`, `hidden_reasons`, а також `policy.hidden_ttl_close_steps`.
- Тести/перевірки:
 	- Додано тест cap-евікшну: [tests/test_ui_v2_viewer_state_builder.py](tests/test_ui_v2_viewer_state_builder.py).

## 2025-12-24 — UI_v2 presentation: `touched_while_hidden` для pools (строго з truth)

- Ціль: прибрати “late-touch” як trust-killer на presentation-рівні, не підміняючи правду.
 	- Якщо pool прихований через `evicted_cap`, а в truth він отримав нові touch-ознаки, UI має змогу показати це як `touched_while_hidden`, а не як “щось зникло/з’явилось”.
- Зміни:
 	- У [UI_v2/viewer_state_builder.py](UI_v2/viewer_state_builder.py) додано відстеження touch-сигнатури для pools у кеші (лише на close):
  		- сигнал береться **строго з truth**: `n_touches` та/або зростання `last_time`.
  		- у `viewer_state.liquidity.pools_meta` додано: `touched_while_hidden_count`, `touched_while_hidden_reasons`.
- Тести/перевірки:
 	- Розширено тест cap-евікшну: [tests/test_ui_v2_viewer_state_builder.py](tests/test_ui_v2_viewer_state_builder.py) (симуляція `n_touches` зростає, поки pool hidden).

## 2025-12-24 — UI_v2 presentation: стабільний top-K pools + canonical keys (менше remove+create)

- Ціль: зменшити churn у списку pools (remove+create/evicted фліккер) через нестабільний порядок у truth.
 	- Важливо: це **presentation-only**; SMC truth/детекція/пороги не змінюються.
- Зміни:
 	- У [UI_v2/viewer_state_builder.py](UI_v2/viewer_state_builder.py) selection `MAX_POOLS` тепер детермінований:
  		- сортування pools за `(strength desc, n_touches desc, pool_key)` перед cap.
 	- Розширено canonical key для pools:
  		- `_pool_key(...)` використовує стабільні `meta` ID (де доступні): `cluster_id`/`wick_cluster_id` (WICK_CLUSTER), `range_extreme_id` (RANGE_EXTREME), `sfp_id` (SFP*).
- Тести/перевірки:
 	- Оновлено тест cap-евікшну під новий детермінований top-K (evict через зміну `strength`): [tests/test_ui_v2_viewer_state_builder.py](tests/test_ui_v2_viewer_state_builder.py).

## 2025-12-24 — UI_v2 presentation: dedup/merge зон по IoU (canonical + stack=N)

- Ціль: прибрати “килим” overlap/dup по zones у UI без зміни SMC truth.
- Зміни:
 	- У [UI_v2/viewer_state_builder.py](UI_v2/viewer_state_builder.py) додано merge зон у presentation:
  		- кластеризація зон за `(zone_type, direction, role, timeframe)`;
  		- merge якщо IoU по price-range >= `ZONES_MERGE_IOU_THRESHOLD`;
  		- для кластерів додаємо `meta.stack=N`; канонічний `price_min/max` **не роздуваємо**, а envelope зберігаємо в `meta.envelope_min/max`.
- Тести/перевірки:
 	- Додано тест overlap-мерджу: [tests/test_ui_v2_viewer_state_builder.py](tests/test_ui_v2_viewer_state_builder.py).

## 2025-12-24 — UI_v2 presentation: `zones_meta` (truth/shown) для QA-гейтів

- Ціль: зробити merge/фільтрацію зон прозорою та вимірюваною (truth vs shown), щоб QA не «покращувався» лише за рахунок приховування.
- Зміни:
 	- Контракт розширено non-breaking: [core/contracts/viewer_state.py](core/contracts/viewer_state.py) (`SmcViewerZones.zones_meta`).
 	- У [UI_v2/viewer_state_builder.py](UI_v2/viewer_state_builder.py) додано `zones_meta` з лічильниками:
  		- `truth_count`, `shown_count`, `merged_clusters_count`, `merged_away_count`, `max_stack`, `filtered_missing_bounds_count`.
  		- `policy`: `merge_iou_threshold`, `min_close_steps_before_show`, `max_zones_shown` (поки `null`), `scope_key=active_zones`.
 	- Для debug/прозорості merge додається `meta.merged_from_ids_sample` (до 3 id).
- Тести/перевірки:
 	- Розширено overlap-мердж тест + додано no-merge тест: [tests/test_ui_v2_viewer_state_builder.py](tests/test_ui_v2_viewer_state_builder.py).

## 2025-12-24 — QA gate (KPI) для кроків 1–5 у `tools/smc_journal_report.py`

- Вимога: після кожного кроку фіксувати не “враження”, а числа (KPI) і мати автоматичний pass/fail.
- Зміни у [tools/smc_journal_report.py](tools/smc_journal_report.py):
 	- Додано режим `--gate`, який друкує таблицю `qa_gate_kpi(steps_1_5)` і повертає **exit code 3** при FAIL.
 	- Пороги задаються CLI-аргументами:
  		- `--gate-min-pools-jaccard-p50`
  		- `--gate-max-pools-short-lifetime-le1-share`
  		- `--gate-max-zone-overlap-frames-share-iou-ge-08`
  		- `--gate-max-shown-counts-rel-range`
 	- Фікс CLI: аргумент з `0.8` у назві перейменовано на `...-08`, щоб уникнути крихкого доступу до `argparse` атрибутів.
 	- Фікс KPI: `shown_*_counts_rel_range` рахуємо з `frame.active_ids` (довжина set), а не з неіснуючого `frame.counts`.
- Smoke-команда (м’які пороги, лише для перевірки, що все рахується):

	```powershell
	C:/Aione_projects/smc_v1/.venv/Scripts/python.exe tools/smc_journal_report.py --dir reports/smc_journal_p0_run1 --symbol XAUUSD --gate --gate-min-pools-jaccard-p50 0.0 --gate-max-pools-short-lifetime-le1-share 1.0 --gate-max-zone-overlap-frames-share-iou-ge-08 1.0 --gate-max-shown-counts-rel-range 999
	```

---

## 2025-12-22

- **Ініціалізація журналу**: створено `Log.md` у корені репозиторію.
- **Причина**: вимога процесу — завжди робити змістовні записи про виконані дії/зміни ("дія/зміна → тести → запис → відповідь" або "дія/зміна → запис → відповідь").
- **Тести/перевірки**: не запускались (зміна лише документаційна).
- **Ризики/нотатки**: відтепер всі подальші правки коду/конфігів/логіки супроводжуються записом у цьому файлі.

- **Розслідування: "застарілі свічки" у UI + потреба рестарту**
 	- **Спостереження**: UI може показувати старі свічки (графік не оновлюється), тоді як бекенд/SMC виглядає живим.
 	- **Root-cause (ймовірний, підтверджено кодом)**: у web-клієнті `UI_v2/web_client/app.js` формувався `to_ms` навіть коли replay-курсор відсутній:
  		- `appState.replay.lastCursorMs` ініціалізований як `null`;
  		- `Number(null) === 0`, отже клієнт додавав `&to_ms=0` до кожного запиту `/smc-viewer/ohlcv`;
  		- сервер, отримавши `to_ms=0`, відсікав всі бари як "майбутні" → `bars=[]`;
  		- у клієнті `pushBarsToChart()` нічого не робив при `bars.length==0`, тому на екрані залишались старі свічки → виглядало як "фриз".
 	- **Зміна**: виправлено генерацію `cursorSuffix`, щоб `to_ms` додавався тільки коли `lastCursorMs` є кінцевим числом.
  		- Файл: `UI_v2/web_client/app.js`
 	- **Дотичне виправлення тестів**: оновлено фейковий провайдер OHLCV у тесті під новий параметр `to_ms`, щоб не було `TypeError`.
  		- Файл: `tests/test_ui_v2_viewer_state_server.py`
 	- **Тести/перевірки**:
  		- `pytest -q tests/test_ui_v2_viewer_state_server.py` (OK)
  		- Примітка: повний прогін `pytest -q tests` у репозиторії зараз має інші, не пов'язані з цією правкою фейли (зафіксовано під час перевірки).
 	- **Ризики/нотатки**:
  		- Поведінка replay не змінюється: коли курсор реально є числом, `to_ms` продовжує передаватись.
  		- Це саме "поломаний браузер/UI" сценарій, бо помилка в клієнтській логіці запиту.

- **Warmup/backfill + live FXCM канали (під вимогу 300–800 барів)**
 	- **Спостереження**: live-оновлення барів може бути відсутнє, а warmup/backfill команди можуть «не доходити», якщо процес слухає/публікує `fxcm_local:*`, а конектор працює на канонічних `fxcm:*`.
 	- **Зміна**:
  		- У [config/config.py](config/config.py) дефолтний prefix FXCM каналів тепер **завжди** `fxcm` (незалежно від `AI_ONE_MODE`). Для ізоляції dev/локального конектора треба явно задати `FXCM_CHANNEL_PREFIX=fxcm_local`.
  		- У [config/config.py](config/config.py) піднято `SMC_RUNTIME_PARAMS["limit"]` з 50 до 300, щоб S3 requester просив мінімум ~300 барів на старті (у барах, не «останні хвилини»).
  		- Оновлено тест [tests/test_config_fxcm_channels_by_mode.py](tests/test_config_fxcm_channels_by_mode.py) під нову політику дефолтів.
 	- **Тести/перевірки**: заплановано `pytest -q tests/test_config_fxcm_channels_by_mode.py`.
 	- **Ризики/нотатки**:
  		- Якщо у вас справді локальний FXCM конектор публікує `fxcm_local:*`, треба виставити `FXCM_CHANNEL_PREFIX=fxcm_local` (і перезапустити процеси).
  		- Підняття warmup-ліміту може збільшити стартове завантаження історії; за потреби можна підкрутити в межах 300–800.

- **Спрощення вибору env-файлу (прибрано евристику `AI_ONE_MODE`)**
 	- **Вимога**: має бути один прозорий перемикач профілю через `AI_ONE_ENV_FILE`, а всі налаштування каналів/namespace/портів живуть у `.env.local` або `.env.prod`.
 	- **Зміна**: у [app/env.py](app/env.py) `select_env_file()` тепер вибирає env-файл тільки так:
  		- 1) `AI_ONE_ENV_FILE` у process-ENV
  		- 2) `AI_ONE_ENV_FILE` у dispatcher `.env`
  		- 3) fallback лише на `.env` (без автоматичного вибору `.env.local/.env.prod` і без `*.example`)
 	- **Тести/перевірки**: додано [tests/test_env_selection.py](tests/test_env_selection.py).
 	- **Ризики/нотатки**:
  		- Якщо в середовищі немає `AI_ONE_ENV_FILE` і немає dispatcher `.env`, система використовує `.env`.
  		- Це навмисно: без явного перемикача немає прихованих "або/або" гілок.

- **Стартові логи + коректний порядок завантаження ENV**
 	- **Проблема**: у [app/main.py](app/main.py) та [app/settings.py](app/settings.py) частина модулів (наприклад `config.config`) імпортувались раніше, ніж виконувався `load_dotenv(...)`, через що значення з `.env.local/.env.prod` могли не застосовуватись.
 	- **Зміна**:
  		- У [app/main.py](app/main.py) `load_dotenv(...)` виконується до імпортів, що читають ENV; додано лог-ланцюжок `[BOOT]` (env-файл/джерело/існування, ключові env-параметри, namespace, FXCM канали, Redis host/port, попередження по HMAC).
  		- У [app/settings.py](app/settings.py) `load_dotenv(...)` тепер виконується до імпорту `config.config`.
  		- У [app/env.py](app/env.py) додано `select_env_file_with_trace()` для прозорої діагностики джерела вибору env-файлу.
 	- **Тести/перевірки**: заплановано `pytest -q tests/test_env_selection.py` та smoke `pytest -q tests/test_ui_v2_viewer_state_server.py`.
 	- **Ризики/нотатки**:
  		- Поведінка runtime стає більш детермінованою: config читає ENV вже після підхоплення `.env.*`.

- **Уточнення BOOT-логів + стабільніший live після F5**
 	- **Проблема**: у логах `AI_ONE_ENV_FILE=None` могло виглядати як "env не підхопився", хоча профіль був взятий з dispatcher `.env`.
 	- **Зміни**:
  		- У [app/env.py](app/env.py) `EnvFileSelection` тепер містить `ref` (сире значення `AI_ONE_ENV_FILE`, з якого обрано профіль).
  		- У [app/main.py](app/main.py) `[BOOT]` лог показує `AI_ONE_ENV_FILE(process)` і `AI_ONE_ENV_FILE(ref)`.
  		- У [UI_v2/web_client/app.js](UI_v2/web_client/app.js) збільшено ліміт reconnect-спроб для FXCM OHLCV/ticks WS з 3 до 12, щоб після refresh не залишатися без live при тимчасовій недоступності WS.
 	- **Тести/перевірки**: таргетні pytest-тести без змін; JS-частина покривається ручною перевіркою через refresh.

- **Інцидент інтеграції: `fxcm_local:ohlcv` мовчить при `stream.async_supervisor=true` (FXCM connector)**
 	- **Симптом**:
  		- У цьому репо конфіг показує `FXCM_CHANNEL_PREFIX=fxcm_local` і `[BOOT]` лог підтверджує `fxcm_local:*` канали,
    але live OHLCV не приходить (UI не бачить «живу свічку», інжестор не отримує повідомлень).
  		- При цьому тики/інші частини могли працювати, що робило проблему «дивною».
 	- **Root-cause (у FXCM конекторі, інше репо)**:
  		- Коли `stream.async_supervisor=true`, публікація OHLCV ішла через `AsyncStreamSupervisor`.
  		- Усередині `_publish_ohlcv_batch()` виклик `publish_ohlcv_to_redis(...)` робився **без** параметра `channel=...`.
  		- Через це публікація падала в legacy-дефолт `fxcm:ohlcv` замість конфігурованого `config.ohlcv_channel` (`fxcm_local:ohlcv`).
  		- Наслідок: AiOne_t був підписаний на `fxcm_local:ohlcv`, а конектор фактично штовхав у `fxcm:ohlcv` → «мовчання».
 	- **Фікс (у FXCM конекторі)**:
  		- Додано `ohlcv_channel` у `AsyncStreamSupervisor`.
  		- Прокинуто `channel=self._ohlcv_channel` (і `source=batch.source`) у `publish_ohlcv_to_redis(...)`.
  		- При створенні supervisor тепер передається `ohlcv_channel=config.ohlcv_channel`.
  		- Файли у конекторі: `connector.py` (AsyncStreamSupervisor.**init**, _publish_ohlcv_batch, місце створення supervisor).
  		- Окремо оновлено запис у `UPDATE.md` у репо конектора.
 	- **Чому проявлялось саме так**:
  		- Без async supervisor все працювало (публікація йшла іншим шляхом і брала правильний канал).
  		- З async supervisor з'являлась «неузгодженість каналів» тільки для OHLCV.
 	- **Як швидко діагностувати наступного разу**:
  		- У цьому репо: звірити `[BOOT] FXCM канали: ...` і логи інжестора `[FXCM_INGEST] ... channel=...`.
  		- Якщо `FXCM_CHANNEL_PREFIX=fxcm_local`, але інжестор стартує на `fxcm:ohlcv` — це майже завжди означає, що конектор публікує не туди або env не підхопився.
  		- У конекторі: перевірити, в який Redis канал реально викликається publish для OHLCV у режимі supervisor.
 	- **Шляхи виправлення (коротко)**:
  		- Правильний: передавати `channel=` у всі publish-шляхи конектора (зокрема supervisor), не покладатися на legacy-дефолти.
  		- Тимчасовий workaround: підписати AiOne_t на `fxcm:*` (не бажано, бо ламає ізоляцію профілів).

- **Cold-start/історія: автодетект внутрішніх дірок + менше втрат хвоста на рестарті**
 	- **Симптоми (з поля)**:
  		- Видимі «міні-гепи» між свічками всередині історії (internal gaps).
  		- Ефект «нова свічка позаду» / «на 1 бар позаду» — коли з’являється бар з меншим `open_time`, ніж очікуваний у хвості.
  		- Після рестарту стабільно губляться «останні хвилини» (write-behind flush lag): RAM очищається, диск не встигає отримати snapshot, Redis тримає лише last-bar.
  		- На вихідних/закритому ринку система може генерувати зайві warmup/backfill запити, які не дають користі.
 	- **Зміни (мінімальний диф)**:
		1) **S2: детект `gappy_tail`**
			- У [app/fxcm_history_state.py](app/fxcm_history_state.py) `compute_history_status()` тепер додатково аналізує tail-вікно `open_time` і рахує:
				- `gaps_count` — кількість кроків `delta(open_time)` більших за `1.5 * tf_ms`;
				- `max_gap_ms` — найбільший виявлений gap.
			- Якщо базовий стан був `ok`, але `gaps_count > 0`, стан переводиться у `gappy_tail` і встановлюється `needs_backfill=True`.
		2) **S3: реакція на `gappy_tail` + policy на `market=closed`**
			- У [app/fxcm_warmup_requester.py](app/fxcm_warmup_requester.py) requester тепер:
				- для `gappy_tail` надсилає команду (для `1m` → `fxcm_warmup`, для TF>1m → `fxcm_backfill`) з `reason=gappy_tail`;
				- додає в payload діагностику `s2.gaps_count` та `s2.max_gap_ms`;
				- при `market=closed` не надсилає команди, якщо стан не `insufficient` (тобто «на вихідних не шумимо», але cold-start warmup дозволяємо).
		3) **Shutdown: зменшення flush lag**
			- У [app/main.py](app/main.py) в `run_pipeline()` у `finally` додано `await datastore.stop_maintenance()`, щоб виконати фінальний drain write-behind черги і зменшити ризик втрати хвоста історії при рестарті.
 	- **Тести/перевірки**:
  		- Оновлено [tests/test_s2_history_state.py](tests/test_s2_history_state.py): додано тест `gappy_tail`.
  		- Оновлено [tests/test_s3_warmup_requester.py](tests/test_s3_warmup_requester.py): додано тест на publish при `gappy_tail`.
  		- Запуск: `pytest -q tests/test_s2_history_state.py tests/test_s3_warmup_requester.py` (OK).
 	- **Ризики/нотатки**:
  		- `gappy_tail` — best-effort сигнал: requester може попросити warmup/backfill, але заповнення внутрішніх дірок залежить від можливостей конектора.
  		- Поріг gap-а зараз евристичний (`1.5 * tf_ms`). Якщо FXCM/конектор дає нерівномірні open_time, може знадобитись підкрутка.

- **Cold-start/історія: детект "бар позаду" (non_monotonic_tail) — уточнення без фолс-позитивів**
 	- **Проблема**: початковий детектор рахував `delta(open_time) <= 0` як не-монотонність, тобто дублікати `open_time` теж помилково зводили стан до `non_monotonic_tail`.
  		- Це ламало очікування в S3: замість `ok`/`prefetch_history` могли з'являтися зайві warmup/backfill.
  		- Також у тестовому кейсі "бар позаду" стан маскувався як `stale_tail`, бо `last_open_time` був занадто старий (пріоритет `stale_tail` вище).
 	- **Зміна**:
  		- У [app/fxcm_history_state.py](app/fxcm_history_state.py) `non_monotonic_count` тепер збільшується лише коли `delta < 0` (реальний крок назад у часі).
  		- У тестах non_monotonic-tail підігнано `base`, щоб хвіст був свіжий і не попадав у `stale_tail`.
  		- Додано регресійний тест: дублікати `open_time` не повинні давати `non_monotonic_tail`.
 	- **Тести/перевірки**: `pytest -q tests/test_s2_history_state.py tests/test_s3_warmup_requester.py`.

---

## 2025-12-23

- **UI_v2: форензика “разовий ривок/розмазування масштабу по Y” на першому wheel**
 	- **Вимога**: нічого не міняти у коді, а спочатку зафіксувати поточний стан максимально детально.
 	- **Симптом (зі слів користувача)**:
  		- Після refresh або зміни TF перший wheel по price-axis дає разовий “ривок/розмазування” (ніби двічі застосувався scale або “підкрутило” range).
  		- Далі wheel працює більш очікувано.
 	- **Контекст реалізації (поточний стан, “як є”)**:
		1) **Built-in scaling lightweight-charts увімкнений у дефолтних опціях**
			- Файл: [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js)
			- `DEFAULT_CHART_OPTIONS.handleScale.mouseWheel = true`
			- `DEFAULT_CHART_OPTIONS.handleScale.axisPressedMouseMove.price = true`
			- `DEFAULT_CHART_OPTIONS.handleScroll.mouseWheel = true`
			- Отже бібліотека має повне право скейлити/скролити на wheel сама.
		2) **Кастомний wheel-хендлер для price-axis (перехоплення у capture)**
			- Файл: [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js)
			- Коментар у коді: “wheel по price-axis перехоплюємо у capture-фазі… інакше lightweight-charts може встигнути застосувати власний scale, а наш manualRange додасться зверху”.
			- `WHEEL_OPTIONS = { passive: false, capture: true }`.
			- Listener: `container.addEventListener("wheel", handleWheel, WHEEL_OPTIONS)`.
			- Ключове: хендлер робить `event.preventDefault()`, `stopImmediatePropagation()` (якщо доступно), потім `stopPropagation()`.
			- Гейти перед тим, як він зупиняє подію:
				- `pointerInAxis = isPointerInPriceAxis(event)`.
				- `pointerInPane = isPointerInsidePane(event)`.
				- Якщо НЕ `pointerInAxis` і НЕ (`event.shiftKey` && `pointerInPane`) → `return`.
				- Якщо `getEffectivePriceRange()` повернув `null` → `return` (важливо: у цьому випадку НЕ виконується `preventDefault/stop*`).
			- Функціональність:
				- `Shift+wheel` у pane → `applyWheelPan()` (вертикальний pan manualRange).
				- Wheel у price-axis → `applyWheelZoom()` (zoom навколо anchor-price).
		3) **Manual price range через autoscaleInfoProvider**
			- У коді є `priceScaleState = { manualRange, lastAutoRange }`.
			- `candles` і `liveCandles` мають `autoscaleInfoProvider: priceScaleAutoscaleInfoProvider`.
			- Логіка:
				- Якщо `manualRange` не активний → використовується baseImplementation(), а `lastAutoRange` оновлюється з базового autoscale.
				- Якщо `manualRange` активний → series повертає ОДНАКОВИЙ `priceRange` для всіх серій на правій шкалі (щоб не було “стеля/підлога”).
		4) **Синхронізація після зміни manualRange: requestPriceScaleSync()**
			- `applyManualRange(range)` ставить `priceScaleState.manualRange = normalized` і викликає `requestPriceScaleSync()`.
			- `requestPriceScaleSync()` робить:
				- `logicalRange = chart.timeScale().getVisibleLogicalRange()`; якщо є — повторно викликає `setVisibleLogicalRange({from,to})`.
				- інакше бере `scrollPosition()` та викликає `scrollToPosition(position, false)`.
			- Важливо: тут немає явного guard-а “logicalRange ще не готовий після init/fitContent”, окрім fallback на scrollPosition.
		5) **Додатковий wheel listener “best-effort” (не для масштабу)**
			- Є окремий `container.addEventListener("wheel", onWheel, { passive: true })`, який лише планує перерахунок DOM-лейблів POI через RAF.
			- Коментар у коді: “wheel у capture-режимі вже обробляється price-axis. Тут — best-effort.”
			- Цей listener не викликає `preventDefault` і не змінює scale/range напряму.
  - **Найімовірніший механізм “перший wheel стрибає” (гіпотеза №1, найсильніша по коду)**:
    - На першому wheel після refresh/зміни TF `getEffectivePriceRange()` інколи повертає `null` (ще немає валідного `lastAutoRange`, або `paneSize/coordinateToPrice` тимчасово не дають чисел).
    - Через ранній `return` кастомний `handleWheel` НЕ викликає `preventDefault/stop*`.
    - Built-in wheel-scale бібліотеки (бо `handleScale.mouseWheel=true`) відпрацьовує і змінює scale/range → користувач бачить разовий “ривок”.
    - На наступних wheel `lastAutoRange` вже ініціалізовано, `getEffectivePriceRange()` стає не-null, і кастомний хендлер починає перехоплювати події в capture-фазі та глушити built-in → “після першого разу норм”.
  - **Альтернативний механізм (гіпотеза №2, слабша, але можлива)**:
    - Якщо lightweight-charts підписався на `wheel` у capture на тому ж target раніше, ніж наш `addEventListener`, то його built-in може спрацювати першим.
    - У цьому випадку навіть `stopImmediatePropagation` у нашому хендлері не зупинить вже виконаний built-in.
    - Але це гірше пояснює “саме перший wheel”, якщо немає одноразової неготовності (гіпотеза №1).
  - **Примітка**: `axisPressedMouseMove.price = true` також означає, що drag по price-axis може активувати built-in логіку шкали.
    Поточний кастомний код блокує лише `handleScroll.pressedMouseMove` під час нашого vertical-pan, але не вимикає `handleScale.axisPressedMouseMove.price` глобально.
  - **Тести/перевірки**: не запускались (зміна лише документаційна, код не чіпали).
  - **Ризики/нотатки**:
    - Цей запис — “зріз стану” перед будь-якими правками.
    - Якщо будемо робити мін-фікс, треба зберегти UX: wheel у pane лишається за бібліотекою, а wheel у price-axis має бути детерміновано перехоплений без одноразових пропусків.

- **UI_v2: P0 фікс “перший wheel не проскакує в built-in scale”**
  - **Проблема**: у `setupPriceScaleInteractions()` подію wheel глушили (preventDefault/stop*) лише після `getEffectivePriceRange()`.
    Якщо `getEffectivePriceRange()` на першій взаємодії повертає `null`, wheel проходить у built-in масштабування lightweight-charts → разовий “ривок/розмаз”.
  - **Зміна (мінімальний диф, UX не розширюємо)**:
    - У [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js) wheel-хендлер тепер викликає `preventDefault()` + `stopImmediatePropagation()` + `stopPropagation()` ДО перевірки `getEffectivePriceRange()`.
    - Якщо range ще не готовий — ми просто виходимо, але built-in вже заблоковано (замість разового стрибка).
  - **Ризики/нотатки**:
    - Якщо користувач одразу після refresh крутить wheel по price-axis, а метрики ще не готові, wheel “нічого не зробить” (але це краще за неконтрольований built-in ривок).
    - Обробка wheel у pane без Shift не змінюється (там ми не перехоплюємо подію).
  - **Smoke-перевірка (2 хв)**:
    - Refresh/F5 → одразу wheel по price-axis: не має бути разового ривка/«розмазу».
    - Wheel у pane без Shift: time-zoom як раніше.
    - Shift+wheel у pane: manual vertical pan працює, сторінка не скролиться.
  - **Факт після спроби**:
    - На практиці у конкретному середовищі користувача це НЕ прибрало симптом і місцями зробило UX гіршим (wheel інколи “глушиться”, але дія не застосовується).

- **UI_v2: P0.1 фікс (стабільний hit-test price-axis + відкладений wheel на 1 кадр)**
 	- **Гіпотеза**:
  		- Після init/зміни TF `chart.paneSize()` і/або `priceScale("right").width()` можуть тимчасово бути 0.
  		- Через це `isPointerInPriceAxis()`/`isPointerInsidePane()` могли повертати `false`, і перехоплення wheel ставало недетермінованим.
  		- Додатково, коли `getEffectivePriceRange()` ще `null`, ми глушили wheel, але дія не виконувалась → “відчуття гірше”.
 	- **Зміна**:
  		- У [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js) додано fallback hit-test для price-axis на випадок нульових метрик:
   			- Використовується `PRICE_AXIS_FALLBACK_WIDTH_PX = 56` (узгоджено з CSS-резервом: `styles.css` -> `.chart-overlay-actions { padding-right: 56px; }`).
   			- `isPointerInPriceAxis()`/`isPointerInsidePane()` більше не вимагають `paneWidth/paneHeight` як обов'язкові для роботи.
  		- Якщо `getEffectivePriceRange()` ще не готовий, wheel-дія (zoom/pan) відкладається на 1 кадр через `requestAnimationFrame`, щоб перша взаємодія не “помирала”.
 	- **Очікування**:
  		- Wheel по price-axis після refresh/зміни TF має бути детермінований: без built-in “ривка” і без “глухого” колеса.

- **UI_v2: live-оновлення (тик/WS) → зменшення “стрибків” від частих перерендерів**
 	- **Спостереження (з поля)**:
  		- “Стрибки” проявляються саме коли ринок відкритий і live-свічка/ціна рухається.
  		- На закритому ринку (коли свічка не рухається) графік стабільний.
  		- При вимкненні live-volume поведінка volume стабілізується.
 	- **Root-cause (ймовірний, по коду)**:
  		- `setLiveBar()` викликається часто (tick stream throttled ~200 мс) і робив:
   			- `liveVolume.setData([])` практично на кожен тик (бо volume=0 у tick-стрімі) → зайві перерахунки/перемальовки;
   			- `updateCurrentPriceLine()` пересоздавав price-line на кожне оновлення ціни (remove + create) → потенційне “смикання” бейджа/шкали.
 	- **Зміна (мінімальний диф, UX не міняємо)**:
  		- У [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js):
   			- `currentPriceLine` тепер оновлюється через `applyOptions({price,color})`, якщо owner не змінився (замість remove+create на кожен тик).
   			- `liveVolume` тепер НЕ робить `setData([])` на кожен тик: чиститься/малюється лише при зміні видимості або при зміні бару; у межах одного бару використовує `update()`.
 	- **Очікування**:
  		- Менше “дергання” при live-оновленнях, особливо по осі/бейджу ціни та по volume.

- **UI_v2: SSOT “інваріанти/межі графіка”**
 	- Додано документ [docs/ui/ui_v2_chart_invariants_and_boundaries.md](docs/ui/ui_v2_chart_invariants_and_boundaries.md) з правилами для wheel/drag/manualRange/lastAutoRange.

- **UI_v2: “80% unit/logic” — витяг чистих функцій + тести без Node.js**
 	- Витягнуто чисту логіку в [UI_v2/web_client/chart_adapter_logic.js](UI_v2/web_client/chart_adapter_logic.js): `normalizeRange`, hit-test (price-axis/pane), `computeEffectivePriceRange`, `clamp`.
 	- У [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js) підключено ці функції (через `globalThis.ChartAdapterLogic`) без зміни UX.
 	- Додано pytest-тести, які виконують JS через `quickjs`: [tests/test_ui_v2_chart_adapter_logic.py](tests/test_ui_v2_chart_adapter_logic.py).
 	- **Тести/перевірки**: `pytest -q tests/test_ui_v2_chart_adapter_logic.py`.
 	- Додано чисті wheel-функції: `computeWheelZoomRange`, `computeWheelPanRange` + тести на напрям/зсув/anchor.

- **UI_v2: P2 фікс “перший vertical-pan/axis-drag не дає Y-стрибок при live-свічці”**
 	- **Симптом (з поля)**:
  		- Після refresh/зміни TF: wheel/scroll по часу працюють стабільно.
  		- Але перший легкий drag вгору у pane або взаємодія по правій шкалі ціни дає різкий Y-стрибок (“їжаки”), після чого поведінка стабілізується до наступного refresh/TF change.
  		- На закритому ринку (без live-руху) симптом відсутній.
 	- **Root-cause (ймовірний, по коду)**:
  		- `getEffectivePriceRange()` використовує `priceScaleState.lastAutoRange`, якщо він є.
  		- `lastAutoRange` оновлювався через `autoscaleInfoProvider` і для `candles`, і для `liveCandles`.
  		- Коли liveCandles активний (1 жива свічка, часто оновлюється), `lastAutoRange` міг “перетиратись” діапазоном саме live-серії.
  		- При першому vertical-pan `ensureManualRange(baseRange)` міг стартувати з цього “live-range”, що виглядає як різкий Y-zoom/розмаз.
 	- **Зміна**:
  		- У [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js) `lastAutoRange` тепер трекається тільки по основній серії `candles` (liveCandles не перетирає `lastAutoRange`).
  		- При reset датасету (`setBars([])`, `looksLikeNewDataset`, `clearAll`) додатково очищаємо `priceScaleState.lastAutoRange = null`, щоб не брати застарілий діапазон після TF/symbol reset.
 	- **Очікування**:
  		- Перший vertical-pan/axis взаємодія після refresh/TF change не повинна давати різкий Y-стрибок, навіть якщо live-свічка рухається.
 	- **Статус після первинної перевірки (з поля)**:
  		- Користувач підтвердив: “зараз все виглядає чудово та ніби виправили”.
  		- Примітка: потрібен час на польові тести/спостереження (різні TF, різні стани ринку, кілька refresh/перезапусків), щоб підтвердити відсутність регресій.

- **S2/S3: розслідування пропусків хвилин (дірки) + автоматичний repair**
 	- **Симптом (з поля)**: візуально помітні “розрізи” між 1m свічками, які виглядають як гепи; внаслідок цього "биті" також вищі TF (напр. 4h), бо 4h матеріалізується з 1m→5m→1h.
 	- **Root-cause (по коду, підтверджено)**:
  		- S2 `compute_history_status()` рахував `gaps_count/non_monotonic_count` лише на вікні `limit=min_history_bars` (типово 300), тому gap-и, що були глибше в історії (наприклад у видимих ~800 барах UI), могли не детектитись.
  		- S3 при `gappy_tail/non_monotonic_tail` просив у конектора лише `desired_bars` (типово 300), тож навіть коли gap був виявлений глибше, repair міг не перекрити ділянку розриву.
  		- S3 на `market=closed` пропускав команди для будь-якого стану, крім `insufficient`, що блокувало офлайн-добір “дір”, які утворились у відкритий ринок.
 	- **Зміни (мінімальний диф)**:
  		- У [app/fxcm_history_state.py](app/fxcm_history_state.py) додано `diagnostic_bars`: S2 може аналізувати ширше вікно для gaps/non-monotonic, не змінюючи поріг готовності `min_history_bars`.
  		- У [app/fxcm_warmup_requester.py](app/fxcm_warmup_requester.py):
   			- при `gappy_tail/non_monotonic_tail` збільшено `lookback_bars` до `~3x desired` (cap 1200), щоб реально перекривати видимі розриви;
   			- при `market=closed` repair дозволено для `gappy_tail/non_monotonic_tail` (а `stale_tail` як і раніше не шумимо на закритому ринку).
 	- **Тести/перевірки**:
  		- `pytest -q tests/test_s2_history_state.py` (OK)
  		- `pytest -q tests/test_s3_warmup_requester.py` (OK)
 	- **Ризики/нотатки**:
  		- S2 трохи частіше читає з UDS (до 1200 барів на пару), але cap 2000/1200 обмежує навантаження.
  		- Якість 4h напряму залежить від повноти 1m (агрегація зараз сувора: неповні bucket-и пропускаються).

- **UI_v2: стабілізація hover-tooltip на живому графіку**
 	- **Симптом (з поля)**: тултіпи нестабільні — швидко зникають або довго не з’являються; причина — live-оновлення графіка постійно «підбиває» crosshair callback.
 	- **Зміна**: у [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js)
  		- додано короткий grace на hide (250мс) для шумних подій без `time/point`;
  		- зменшено затримку показу з 1000мс до 200мс;
  		- якщо курсор не рухався (та сама точка/час) — не скидаємо show-таймер, щоб tooltip не “ніколи не з’являвся”.

- **UDS: on-demand refresh похідних TF (5m/1h/4h) при live-стрімі**
 	- **Симптом (з поля)**: 1h/4h можуть бути “діряві” саме під час стріму; після рестарту інколи виглядає краще.
 	- **Root-cause (по коду, підтверджено)**: у [data/unified_store.py](data/unified_store.py) `get_df()` матеріалізував 5m/1h/4h лише коли snapshot порожній; якщо snapshot уже існує, він повертався як є, без спроби дозбирати нові complete-бакети.
 	- **Зміна**:
  		- У [data/unified_store.py](data/unified_store.py) додано best-effort refresh похідних TF при читанні, якщо parent TF просунувся достатньо для нового complete bucket.
  		- Додано throttle (щоб часті UI-запити не викликали часту агрегацію).
  		- Матеріалізація для refresh читає лише tail джерельного TF (cap по барах), щоб обмежити навантаження.
 	- **Тести/перевірки**: `pytest -q tests/test_uds_tf_materialization.py` (OK).
 	- **Ризики/нотатки**:
  		- Це не “пом’якшує” сувору агрегацію: якщо в 1m є реальні пропуски, 1h/4h все одно можуть мати дірки, доки S3/backfill не добере хвилини.
  		- Але тепер, коли 1m дотягується, 1h/4h можуть підхопити виправлення без рестарту.

- **Інструмент: “доказовий” аудит гепів у 1m/5m**
 	- **Вимога**: почати з 1m та 5m і довести, що в хвості немає жодного гепа/дірки/незаповненого проміжку.
 	- **Зміна**: додано утиліту [tools/gap_audit.py](tools/gap_audit.py), яка читає дані через `UnifiedDataStore.get_df()` і строго перевіряє крок `open_time` (Δ має дорівнювати TF).
 	- **Тести/перевірки**:
  		- `pytest -q tests/test_gap_audit_logic.py` (OK)
 	- **Нотатки**:
  		- Рекомендований запуск під час live: `python tools/gap_audit.py --symbol xauusd --timeframes 1m 5m --limit 2000`.
  		- Гаряче виправлення: для Windows/скрипт-режиму додано підкладання кореня репо в `sys.path`, щоб `import app/...` працював.

- **UI_v2: “щільний” графік при live-гепах (gap-fill тільки у /smc-viewer/ohlcv)**
 	- **Проблема (з поля)**: гепи з'являються у live, отже видно й у UI; хочемо, щоб графік малювався без порожніх проміжків.
 	- **Зміна**: у [UI_v2/ohlcv_provider.py](UI_v2/ohlcv_provider.py) додано best-effort gap-fill у відповіді:
  		- пропущені TF-слоти заповнюються синтетичним flat-баром (OHLC=prev_close, volume=0);
  		- це робиться лише на рівні UI-віддачі, без запису в `UnifiedDataStore`.
 	- **Тести/перевірки**: `pytest -q tests/test_ui_v2_ohlcv_provider.py` (OK).
 	- **Ризики/нотатки**:
  		- Це косметичний фікс для UI; канонічну історію все одно має добирати S3/backfill.
  		- Для дуже великих розривів є ліміт на кількість синтетичних барів у відповіді.

- **FXCM ingest: live auto-backfill при гепі (kill-switch + cooldown)**
 	- **Вимога**: авто-backfill при виявленні гепа у live, з kill-switch і мінімальним навантаженням
		(важливо і для UI, і для SMC аналізу).
 	- **Зміна**: у [data/fxcm_ingestor.py](data/fxcm_ingestor.py) додано live gap guard:
  		- після успішного `put_bars()` інжестор тримає `last_ingested_open_time` per (symbol, tf);
  		- якщо наступний бар має `open_time` зі стрибком (Δ > tf_ms) → publish команду **для FXCM-конектора** у `FXCM_COMMANDS_CHANNEL` (Redis):
   			- `1m` → `fxcm_warmup`
   			- `TF>1m` → `fxcm_backfill`
  		- rate-limit per (symbol, tf) через cooldown.
 	- **Конфіг (kill-switch)**: у [config/config.py](config/config.py)
  		- `SMC_LIVE_GAP_BACKFILL_ENABLED` (default: `False`, керується конфігом)
  		- `SMC_LIVE_GAP_BACKFILL_COOLDOWN_SEC` (default: `120`)
  		- `SMC_LIVE_GAP_BACKFILL_LOOKBACK_BARS` (default: `800`, внутрішній cap `1200`)
  		- `SMC_LIVE_GAP_BACKFILL_MAX_GAP_MINUTES` (default: `180`) — щоб не спамити конектор
			на вихідних/закритому ринку.
 	- **Тести/перевірки**: `pytest -q tests/test_ingestor.py` (OK).
 	- **Ризики/нотатки**:
  		- Статус: тему зафіксовано та тимчасово закрито — потрібен час на польову перевірку в live
			(кілька сесій/TF/перезапусків).
  		- Великі розриви (наприклад вихідні) спеціально не тригерять live-команди (керується `MAX_GAP_MINUTES`);
			їх має добирати S3 requester (`gappy_tail`) без live-спаму.

- **Docs/Log: уточнення межі “не напряму FXCM”**
 	- **Контекст**: у цьому репо прямі команди в FXCM заборонені; взаємодія йде лише через FXCM-конектор.
 	- **Зміна**: уточнено формулювання, що “команди” — це публікація payload у Redis канал конектора (`FXCM_COMMANDS_CHANNEL`), а не прямий виклик FXCM.
 	- **Де**: [Log.md](Log.md), [config/config.py](config/config.py), [docs/architecture/migration_log.md](docs/architecture/migration_log.md).

- **Shutdown: менше шуму у логах при зупинці пайплайна**
 	- **Проблема (з поля)**: під час штатного завершення (Ctrl+C / Cancel) з'являвся traceback типу
		`redis.exceptions.ConnectionError: Connection closed by server` у
		[UI_v2/viewer_state_ws_server.py](UI_v2/viewer_state_ws_server.py).
 	- **Зміна**:
  		- У [UI_v2/viewer_state_ws_server.py](UI_v2/viewer_state_ws_server.py) додано `stopping`-гейт:
			під час shutdown pubsub помилки більше не логуються як warning+traceback.
  		- У [app/main.py](app/main.py) та воркерах [data/fxcm_ingestor.py](data/fxcm_ingestor.py),
			[data/fxcm_price_stream.py](data/fxcm_price_stream.py),
			[data/fxcm_status_listener.py](data/fxcm_status_listener.py) CancelledError-логи переведено на DEBUG.
 	- **Тести/перевірки**:
  		- `pytest -q tests/test_ui_v2_viewer_state_server.py` (OK)
  		- `pytest -q tests/test_ingestor.py tests/test_s3_warmup_requester.py` (OK)

- **UI_v2: Playwright E2E smoke (офлайн, без CDN) + тестовий lifecycle для HTTP сервера**
 	- **Мета**: “20% E2E” — зафіксувати регресії UI-фіксів (перший wheel після refresh/TF change без ривка; tooltip стабільний).
 	- **Зміни (мінімальний набір)**:
  		- Додано офлайн harness сторінку: [UI_v2/web_client/e2e_smoke.html](UI_v2/web_client/e2e_smoke.html).
  		- Додано локальний stub lightweight-charts для тестів: [UI_v2/web_client/lightweight_charts_stub.js](UI_v2/web_client/lightweight_charts_stub.js).
  		- Додано драйвер `window.__e2e__`: [UI_v2/web_client/e2e_smoke_driver.js](UI_v2/web_client/e2e_smoke_driver.js).
  		- Додано E2E smoke тести Playwright: [tests/e2e/test_ui_v2_playwright_smoke.py](tests/e2e/test_ui_v2_playwright_smoke.py);
		  маркер `e2e` зареєстровано у [pytest.ini](pytest.ini).
  		- Для програмного старт/стоп у тестах розширено HTTP сервер:
		  [UI_v2/viewer_state_server.py](UI_v2/viewer_state_server.py) (`start()`/`stop()`/`get_listen_url()` з `port=0`).
 	- **Тести/перевірки**:
  		- `pytest -q -m e2e` (OK)
  		- `pytest -q tests/e2e/test_ui_v2_playwright_smoke.py` (OK)
 	- **Ризики/нотатки**:
  		- Це smoke-рівень: ловить грубі регресії взаємодій, але не замінює польові live-перевірки.
  		- Таймінги tooltip залежать від реальних `SHOW_DELAY_MS/HIDE_GRACE_MS`, тому у тестах є контрольовані wait-и.

- **Config: виправлення дефолтів kill-switch та ENV false-values (без зміни контрактів)**
 	- **Проблема**:
  		- `_FALSE_ENV_VALUES` у [config/config.py](config/config.py) був некоректним (`{"1"}`), що могло зламати `_env_bool`-гейти.
  		- `SMC_LIVE_GAP_BACKFILL_ENABLED` був увімкнений попри коментар про kill-switch.
 	- **Зміна**: у [config/config.py](config/config.py)
  		- `_FALSE_ENV_VALUES = {"0", "false", "no", "off"}`
  		- `SMC_LIVE_GAP_BACKFILL_ENABLED = False` (default off; вмикати лише явним рішенням)
 	- **Тести/перевірки**: `pytest -q tests/test_ingestor.py` (OK) + E2E smoke (OK)

---

## 2025-12-24

- **Фіксація фактів/артефактів: QA-прогін `tools/smc_journal_report.py` для XAUUSD (5m) з пріоритетом POI → OTE → pools**
  - **Вимога**: зафіксувати “доказово” (цифри + приклади для replay), що саме на 5m створює шум/недовіру у візуалізації, без передчасного “вимикання”.
  - **Що запущено (фактична команда)**:

    ```powershell
    ; & "C:\Aione_projects\smc_v1\.venv\Scripts\python.exe" tools\smc_journal_report.py --dir reports/smc_journal/2025-12-19 --frames-dir reports/smc_journal/frames/2025-12-19 --symbol XAUUSD --run-dir reports/smc_journal_p0_run5 --ohlcv-path datastore/xauusd_bars_5m_snapshot.jsonl
    ```

    - Статус: exit code = 0 (репорт/CSV згенеровано).
  - **Де артефакти (SSOT для цього прогону)**:
    - Звіт: `reports/smc_journal_p0_run5/report_XAUUSD.md`
    - TODO для ручного replay: `reports/smc_journal_p0_run5/audit_todo.md`
    - CSV-артефакти (ключові для цього аналізу):
      - `touch_rate.csv`, `created_per_hour.csv`
      - Case B: `case_b_removed_then_late_touch_examples.csv`
      - Case C: `case_c_short_lifetime_examples.csv`, `short_lifetime_share_by_type.csv`, `flicker_short_lived_by_type.csv`
      - Case D: `case_d_widest_zone_examples.csv`, `wide_zone_rate.csv`, `span_atr_vs_outcomes.csv`
      - Case E: `zone_overlap_examples.csv`, `zone_overlap_matrix_active.csv`, `merge_rate.csv`
      - Case F/H (offline): `missed_touch_rate_offline.csv`, `touch_outcomes_after_touch_offline.csv`
  - **Ключові метрики (з `report_XAUUSD.md`)**:
    - `touch_rate`:
      - `pool`: created=10298, touched=978 (touch_rate=9.5%), removed=10292, touched_late=7313 (late_touch_rate_vs_removed=71.1%).
      - `zone`: created=345, touched=88 (touch_rate=25.5%), removed=325, touched_late=207 (late_touch_rate_vs_removed=63.7%).
      - `magnet`: created=208, touched=160 (touch_rate=76.9%), removed=208, touched_late=208 (100%).
    - `wide_zone_rate(span_atr)`: zones_with_atr=345; span_atr>=3.0 → 18 (5.2%); span_atr_avg=0.926.
  - **POI / Zones (пріоритет №1): що саме дає “засмічення” на 5m**
    - **Case D: надширокі зони (span_atr)**
      - Факт: у прикладах є екстремальні ORDER_BLOCK PRIMARY LONG з `span_atr` до 7.393.
      - Конкретний приклад (top): `ob_xauusd_5m_157_162` (dt_created_utc=2025-12-19T04:49:00Z): price_min=4327.800, price_max=4342.940, atr_last=2.0479, span_atr=7.393.
      - Висновок для UI: навіть невелика частка таких зон (5.2% з span_atr>=3.0) може “розмазувати” картину (перекриття по ціні + конкуренція за top‑K).
    - **Case E: перекриття/дублі зон (IoU overlap)**
      - Факт (приклад стану): 2025-12-19T05:09:00Z — n_active=50, total_pairs=1225, pairs_iou_ge_0.4=67, max_iou=0.9697.
      - Max-overlap пара: `fvg_5m_1765957500000000000_78` vs `fvg_5m_1766069700000000000_440` (max_iou=0.9697).
      - Додатковий сигнал якості: `missing_bounds=12` у `zone_overlap_examples.csv`.
      - Висновок для UI: високий IoU (≈0.97) між активними FVG означає сильний ризик “дублів” та візуального шуму без merge/кластеризації.
  - **Pools (пріоритет №3): що саме підриває довіру/дає флікер**
    - **Case B: “видалили → пізно торкнуло” (late touch)**
      - Факт: late_touch_rate_vs_removed=71.1% (7313 late touches при 10292 removed).
      - Приклади з великим `bars_to_touch`: типово 250–272 бари (тобто 20–23 години на 5m).
      - Конкретний приклад: `pool:WICK_CLUSTER:COUNTERTREND:4349.180000:2025-12-17T16:10:00+00:00:2025-12-18T16:30:00+00:00` має `bars_to_touch=272`.
    - **Case C: коротке життя (short_lifetime<=1)**
      - Факт: `case_c_short_lifetime_examples.csv` містить багато прикладів pools з `lifetime_bars=1` і `reason=invalidated_rule` (close/preview).
      - Висновок для UI: масові one‑bar pools дають флікер (виникли/зникли) і засмічують шари.
  - **OTE (пріоритет №2): статус по цьому прогону**
    - Факт: за 2025‑12‑19 (XAUUSD 5m) немає “маси” OTE-прикладів у `audit_todo.md` як окремого кейсу/секції;
      для доказового аналізу OTE потрібен інший відрізок, де OTE реально генерується.
  - **Offline аудит журналу (touch) на цьому датасеті**
    - `missed_touch_rate(offline)`:
      - `preview`: zone_instances=268; should_touch_eps=66; journal_touched=72; missed_touch_fn=0 (0.0% FN); `journal_touch_but_no_ohlcv_touch_fp=6`.
      - `close`: zone_instances=16; should_touch_eps=1; journal_touched=1; missed_touch_fn=0; fp=0.
    - `case_F_missed_touch_examples(offline)` у звіті: “нема FN прикладів за поточними правилами offline-аудиту” (FN=0).
  - **Додатково: запуск `tools/gap_audit.py` (контекст для гепів, без змін коду)**

    ```powershell
    "C:\Aione_projects\smc_v1\.venv\Scripts\python.exe" tools\gap_audit.py --symbol xauusd --timeframes 1m 5m --limit 2000
    ```

    - Статус: exit code = 0 (вивід у консоль; артефакти не зберігались у файл у цьому прогоні).
  - **Ризики/нотатки**:
    - Це “заморозка фактів” по конкретному прогону (2025‑12‑19, TF=5m). Узагальнювати на весь ринок/періоди без повторних прогонів не можна.
    - Для OTE: потрібна інша дата/відрізок (або інший символ/режим), де OTE справді присутній, і повторення цього ж протоколу (звіт + audit_todo + CSV).

## 2025-12-24 (UI_v2: time-scale під час паузи ринку)

- Контекст: під час паузи ринку (ніч/вихідні/свята) конектор віддає `market=CLOSED` і свічки не приходять;
  UI раніше міг «тягнути» time-scale до wall-clock через live-оновлення з часом далеко попереду останньої відомої
  свічки, що створювало порожній простір праворуч і «дірку» до першої нової свічки.
- Зміна: додано guardrail у [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js) —
  `setLiveBar()` відхиляє/очищає live-бар, якщо його `time` занадто далеко попереду `lastBar.time`
  (порог: `barTimeSpanSeconds * 2`), щоб шкала часу залишалась прив’язаною до останнього бару
  (поведінка в стилі TradingView).
- Тест: додано smoke E2E у [tests/e2e/test_ui_v2_playwright_smoke.py](tests/e2e/test_ui_v2_playwright_smoke.py) — симуляція «future» live-тіка не має змінювати `chartTimeRangeMax`, а `lastLiveBarTime` має очищатися.

## 2025-12-24 (UI_v2: хардени проти whitespace/дір/мерехтіння)

- Причини, які реально дають “whitespace data points”/дірки/мерехтіння в lightweight-charts:
 	- `time` інколи приходить у ms/us замість sec → бар стрибає в “далеке майбутнє”.
 	- для історії/last_bar береться close/end time, а не open time бакету → UI думає “новий бар” замість `update()`.
 	- tick-стрім без timestamp фабрикує час через wall-clock → псевдо-бари та розтягнення шкали часу.
- Зміни (мінімальний, акуратний підхід без глобальних перехоплень серій):
 	- [UI_v2/web_client/chart_adapter.js](UI_v2/web_client/chart_adapter.js): `normalizeBar()` тепер приймає sec/ms/us (евристика), бере `time` з кількох полів і відкидає NaN OHLC, щоб LWC не отримував “биті” значення.
 	- [UI_v2/web_client/app.js](UI_v2/web_client/app.js):
  		- `safeUnixSeconds()` та `normalizeTickTimestampToSeconds()` підтримують sec/ms/us.
  		- `normalizeOhlcvBar()` пріоритезує open/start time (open time бакету) замість end/close.
  		- `handleTickWsPayload()` більше не будує бар з `Date.now()` якщо у payload немає timestamp (цінові поля UI оновлюються як і раніше).
- Тест: [tests/e2e/test_ui_v2_playwright_smoke.py](tests/e2e/test_ui_v2_playwright_smoke.py) — додано smoke на ms-vs-s: live-бар з timestamp у мс не має створювати “бар у майбутньому” і не має міняти `chartTimeRangeMax`.

## 2025-12-24 (UI_v2: оверлеї можуть не повертатися після clearAll)

- Контекст: інколи трейдер бачить “то все є, то взагалі нічого немає” по оверлеях (pools/zones), хоча очікується стабільна наявність рівнів.
- Root-cause (по коду): при тимчасовому фейлі `/smc-viewer/ohlcv` фронтенд викликає `chart.clearAll()` (чистить бари/оверлеї).

- Наслідок: `overlaySeqBySymbol` зберігає попередній `seqKey`; якщо наступний `viewer_state` приходить з тим самим `seqKey`, `updateChartFromViewerState()` скіпає оновлення (seq-gate), тому оверлеї можуть не відмалюватися назад.

- Зміна: у [UI_v2/web_client/app.js](UI_v2/web_client/app.js) додано `resetOverlaySeqCache()` і виклик після `chart.clearAll()`, щоб після очищення графіка наступний `viewer_state` гарантовано перерендерив оверлеї навіть при незмінному `seqKey`.
- Тести/перевірки: таргетних автотестів саме на цей сценарій поки немає (існуючі Playwright smoke покривають chart_adapter, а не seq-gate у app.js).

## 2025-12-26 — Ознайомлення: Log + UI_v2 SMC audit current state

- Дія: прочитано поточний [Log.md](Log.md) перед будь-якими роботами, щоб не дублювати/не перекривати вже зроблені зміни.
- Дія: ознайомлення з “as-is” документом [docs/ui/ui_v2_smc_audit_current_state_2025-12-25.md](docs/ui/ui_v2_smc_audit_current_state_2025-12-25.md).
- Навіщо: синхронізувати SSOT-карту (truth → viewer_state → UI_v2) перед подальшими правками/діагностикою.
- Зміни в коді: немає.
- Тести/перевірки: не застосовувались (читання документації).

## 2025-12-26 — Оцінка: траєкторія V1→V2→V3 (SSOT presentation) + старт з Levels

- Дія: переглянуто ключові місця, де зараз живе “подвійна правда” для відбору/підказок:
 	- `UI_v2/viewer_state_builder.py`: presentation вже містить close-only/preview suppression для pools, newborn gating, hidden TTL, merge зон.
 	- `UI_v2/web_client/chart_adapter.js`: UI робить частину «рішень» (підказки/вибір target з pools, локальна нормалізація типів, trader_view політики тощо).
- Висновок по варіантах:
 	- V1 (мінімальний ризик) — найкращий старт: уже є готова точка концентрації логіки (viewer_state_builder), і міграція UI-логіки в нього узгоджується з поточною архітектурою без торкання SMC-core.
 	- V2 (контракт by-TF) — правильний цільовий дизайн, але краще робити після V1, коли rules стабілізовані та є snapshot-тести; інакше ризик «переписати контракт під рухомі правила».
 	- V3 (окремий presentation-модуль) — логічне завершення після V2 (коли контракт і правила усталені), бо тоді винесення стане механічним та безпечним.
- Рекомендована траєкторія:
 	- Почати з V1 для Levels: винести whitelist/нормалізацію/кап/дистанції/freeze з UI у presentation (viewer_state_builder), а UI залишити лише рендерером.
 	- Паралельно закласти «місток» до V2: структурувати Levels як `line|band` і готуватися до `levels_by_tf`, але не ламати існуючий потік до моменту, коли є тести й підтвердження візуально.
- Ризики/нотатки:
 	- Важливо уникнути дублю правил (UI+presentation одночасно). Міграція має бути хвилею: перенос → вимкнення UI-правила → регресія.
 	- Levels наразі фактично «виводяться» з pools, тому потрібен стабільний `id`/ключ і freeze на close (preview не рухає геометрію).
- Зміни в коді: немає (це аналітичний запис перед стартом робіт).
- Тести/перевірки: не застосовувались.

## 2025-12-26 — План Levels-V1 (production-підхід): scope + TF rules + L0–L5 + гейти якості

- Принцип (єдине правило архітектури): Truth (SMC-core) → Presentation (SSOT) → ViewerState → UI render.
 	- У цій хвилі **SMC-core не чіпаємо**.
 	- У цій хвилі **UI не приймає рішень**, тільки рендерить готові `levels`.
 	- Ціль: прибрати “подвійну правду” (whitelist/distance/truth-gate/trader_view у фронті) для Levels.

- Scope першої хвилі (Levels-V1):
 	- `Line-levels` (пунктир + тег праворуч): `PDH/PDL`, `EDH/EDL`, `ASH/ASL`, `LSH/LSL`, `NYH/NYL`, `RANGE_H/RANGE_L`.
 	- `Band-levels` (тонкий прямокутник/зона): `EQH/EQL`.
 	- Важливо: `EQH/EQL` у V1 — це **лише форма рендера** (band), не нова логіка пошуку.

- Цільові правила “що показуємо” по TF:
 	- TF=4h/1h (context, фон-маяки):
  		- Must show: `PDH+PDL` (завжди, але слабким стилем).
  		- Optional (за близькістю): `EDH/EDL`.
  		- Session: **лише 1 сесія** (одна з `AS`, `LDN`, `NY`) — вибір “релевантної зараз”.
  		- EQ bands: максимум `1` зверху + `1` знизу, якщо “поруч”.
  		- Caps: line-levels ≤3 (не рахуючи PDH/PDL), band-levels ≤2 (1/side).
  		- Distance gate:
   			- Для session/EDH/EDL/EQ: `distance ≤ 1.5 * DR(HTF)` або fallback `distance ≤ 6 * ATR(5m)`.
   			- Для PDH/PDL: gate вимкнений (завжди показуємо).
  		- Freeze: геометрія/відбір рівнів оновлюються **тільки на close 1h/4h**.
 	- TF=5m (structure, “рішення”):
  		- line-levels: `PDH/PDL` + ще 1 (EDH/EDL або 1 сесійний або range-mid*).
  		- band-levels: `EQH/EQL` як тонкі прямокутники (до 2 total, 1/side).
  		- Caps: line-levels ≤3 total; band-levels ≤2 total.
  		- Distance gate: line/band `≤ 2.5 * ATR(5m)`.
  		- Freeze: **тільки close 5m**; preview не має права зсувати геометрію.
 	- TF=1m (exec, “активний контекст”):
  		- `PDH/PDL` лише якщо `distance ≤ 1.5 * ATR(5m)`.
  		- 1 найближчий line-level (наприклад EDH або session).
  		- 1 band-level (EQH або EQL), якщо в радіусі.
  		- Caps: line-levels ≤2; band-levels ≤1.
  		- Distance gate: жорсткіший `≤ 1.5 * ATR(5m)`.
  		- Freeze: 1m рівні **беруться з зафіксованого 5m/HTF** (не перераховуємо щотік).

- Етапи робіт (Levels-V1), без накладання/дублю логіки:
 	- L0 — Специфікація (без коду):
  		- Зафіксувати taxonomy `line vs band`, таблицю `TF → допустимі level-и`, caps/distance/freeze/merge.
  		- Гейт: погоджена таблиця та правила в журналі (цей запис).
 	- L1 — Канонічна модель у viewer_state (мінімальний контракт для UI-рендера):
  		- Додати `levels` (або `levels_by_tf` без лому контракту) з полями:
   			- `id` (стабільний), `tf` (1m/5m/1h/4h), `kind` (line|band), `label`, `style_hint`, `asof_close_ts`, `price` або `top/bot`.
  		- Гейт: UI здатен відрендерити **без будь-яких if/whitelist**.
 	- L2 — Presentation-функція `select_levels_for_tf` (SSOT):
  		- Нормалізація джерел (key_levels + derived EQ bands).
  		- Merge/cluster:
   			- line-level: якщо два ближче ніж `tol = 0.25 * ATR(5m)` → злили, лишили сильніший.
   			- band-level: якщо overlap > X% → злили.
  		- Distance gate + caps (total/side) + пріоритети:
   			- PDH/PDL завжди top;
   			- потім nearest-to-price;
   			- потім session;
   			- потім range.
  		- Гейт: на replay кількість об’єктів стабільна і не “вибухає”.
 	- L3 — Freeze (close-only):
  		- builder кешує `levels_selected` і не міняє геометрію до наступного close:
   			- 5m: оновлення тільки при зміні `last_5m_close_ts`.
   			- 1h/4h: тільки при зміні close відповідного TF.
  		- Критичний гейт: під час preview **0 геометричних змін**.
 	- L4 — Cutover: UI = “тупий рендер”:
  		- Прибрати з `chart_adapter.js`:
   			- whitelist/trader_view по levels;
   			- локальні нормалізації/вибір HTF targets як логіку “що показати як level”;
   			- будь-які distance gates для levels.
  		- UI робить лише: взяв `viewer_state.levels` для поточного TF → намалював `line|band` за `style_hint`.
  		- Гейт: **одне джерело істини** — тільки builder.
 	- L5 — Регресійні перевірки (перед Pools):
  		- Авто:
   			- `levels_count{tf}` не перевищує caps.
   			- `levels_geometry_changes == 0` між close-ами (replay).
   			- stable IDs: той самий level не “перенароджується” на кожен тик.
  		- Візуально (контрольні кадри):
   			- 4h: PDH/PDL + максимум 1–2 додаткових.
   			- 5m: 1–2 bands + 2–3 lines, без шуму.
   			- 1m: максимум 1 band + 1–2 lines, фокус біля ціни.

- Очікуваний результат (що маємо реально побачити після cutover):
 	- На 5m: пунктирні PDH/PDL (+ максимум 1 лінія), EQH/EQL як тонкі прямокутники, короткі теги праворуч; у replay рівні не мігають і не «переїжджають» між close.
 	- На 1m: не дублюється “весь світ”; лише найближчі 1–2 маяки + 1 band (якщо актуально).
 	- На 1h/4h: рівні як фон, слабкий пунктир, мінімум кількості.

- Примітки/уточнення для реалізації:
 	- `RANGE_MID` згадувався раніше як можливий line-level на 5m; у поточному scope V1 він **не включений** (є лише `RANGE_H/RANGE_L`). Якщо потрібно — додамо окремим рішенням у L0.
 	- “DR(HTF)” потрібно визначити як канонічну метрику в presentation (звідки беремо і як рахуємо) до старту L2, щоб не було магії.

- Зміни в коді: немає (це план/специфікація перед початком L1).
- Тести/перевірки: не застосовувались.

## 2025-12-26 — Levels-V1 / Крок 1: Baseline Harness (метрики + JSON-снапшоти “як є”)

- Мета: перед будь-якими змінами Levels зафіксувати “as-is” у цифрах і JSON, щоб кожен наступний крок порівнювати об’єктивно (без «здається гірше/краще»).
- Звідки беремо дані:
 	- `GET /smc-viewer/snapshot?symbol=SYM` — джерело `viewer_state` (як у UI_v2).
 	- `GET /smc-viewer/ohlcv?symbol=sym&tf=TF&limit=N` — OHLCV для емулювання поточного view-вікна.
- Що саме вимірюємо (baseline):
 	- “levels як зараз у UI” = поточний відбір `liquidity.pools` через логіку UI `selectPoolsForRender()` (as-is, до cutover).
 	- Метрики: `count_by_tf`, список `labels/titles`, `geometry_hash` (стабільний хеш геометрії/лейблів), базові поля для діагностики.
- Артефакти:
 	- `reports/levels_baseline/<timestamp>_<symbol>/baseline.json`
 	- `reports/levels_baseline/<timestamp>_<symbol>/baseline_summary.md`
- Вимоги якості (гейт):
 	- Скрипт збирає мінімум 20 знімків з інтервалом і дає детерміновані метрики на однакових вхідних даних.
- Реалізація:
 	- Додано скрипт baseline: [tools/levels_baseline_harness.py](tools/levels_baseline_harness.py).
  		- Емулює поточний UI-відбір через порт `selectPoolsForRender()` (as-is) на базі `viewer_state.liquidity.pools` + `/smc-viewer/ohlcv`.
  		- Записує артефакти у `reports/levels_baseline/<timestamp>_<symbol>/`.
 	- Додано юніт-тести стабільності інструмента: [tests/test_levels_baseline_harness.py](tests/test_levels_baseline_harness.py).
- Як запускати (приклад):

	```powershell
	; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" tools/levels_baseline_harness.py --base-url http://127.0.0.1:8080 --symbol XAUUSD --tfs 1m 5m 1h 4h --samples 20 --interval-sec 0.7
	```

- Тести/перевірки:
 	- `pytest` таргетно: `tests/test_levels_baseline_harness.py` — OK.

## 2025-12-26 — Run: Levels baseline harness (XAUUSD)

- Дія: запускаємо baseline harness для фіксації “as-is” метрик/хешів (мін. 20 снапшотів).
- Команда (PowerShell):

	```powershell
	; function с { } ; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" tools/levels_baseline_harness.py --base-url http://127.0.0.1:8080 --symbol XAUUSD --tfs 1m 5m 1h 4h --samples 20 --interval-sec 0.7
	```

- Очікуваний артефакт: `reports/levels_baseline/<timestamp>_XAUUSD/baseline_summary.md`.
- Результат: OK.
 	- Артефакти: `reports/levels_baseline/20251226_213929_XAUUSD/`
  		- `baseline.json`
  		- `baseline_summary.md`
- Статистика (з `baseline_summary.md`, 20 знімків):
 	- TF=1m: count min=4 max=4 avg=4.00; geometry_hash unique=1; labels: `EQH P`, `EQL`, `RANGE_ P`, `WICK_C P` (кожен 20/20)
 	- TF=5m: count min=4 max=4 avg=4.00; geometry_hash unique=1; labels: `EQH P`, `EQL`, `RANGE_ P`, `WICK_C P` (кожен 20/20)
 	- TF=1h: count min=4 max=4 avg=4.00; geometry_hash unique=1; labels: `EQH P`, `EQL`, `RANGE_ P`, `WICK_C P` (кожен 20/20)
 	- TF=4h: count min=4 max=4 avg=4.00; geometry_hash unique=1; labels: `EQH P`, `EQL`, `RANGE_ P`, `WICK_C P` (кожен 20/20)
- Нотатка: baseline зараз емулює поточний UI-відбір через `selectPoolsForRender()`, тому “title” може бути укороченим (`RANGE_`, `WICK_C`). Це очікувано для baseline as-is; у Levels-V1 ці сутності будуть описані канонічно як `label`/`kind`.

## 2025-12-26 — Levels-V1 / Крок 2: Shadow contract (LevelView + levels_shadow_v1), без зміни UI

- Мета: додати у `viewer_state` тіньовий список рівнів у канонічному форматі, не змінюючи UI-рендер (0 ризику).
- Зміна (контракт):
 	- Додаємо модель `LevelView` і поле `levels_shadow_v1` у `SmcViewerState` (non-breaking).
 	- На цьому кроці `levels_shadow_v1` має бути 1:1 з тим, що UI фактично показує зараз (baseline as-is), тобто віддзеркалює поточну проєкцію з `liquidity.pools`.
- Зміна (builder):
 	- `UI_v2/viewer_state_builder.py` формує `levels_shadow_v1` на основі поточного стану liquidity (без зміни SMC-core).
- Зміна (harness):
 	- `tools/levels_baseline_harness.py` додатково читає `viewer_state.levels_shadow_v1` і рахує `shadow_count_by_tf` + `shadow_geometry_hash` + порівняння з as-is.
- Гейт: UI візуально не змінюється; `levels_shadow_v1` збігається з baseline as-is по count/hash.
- Статус: у роботі (PATCH + тести будуть зафіксовані окремо).

## 2025-12-26 — Levels-V1 / Крок 2: Shadow contract — PATCH (контракт + builder + harness)

- Мета: реалізувати `levels_shadow_v1` у viewer_state як "as-is" shadow-проєкцію, без змін UI.
- Зміни (контракт):
 	- Оновлено контракт viewer_state: додано `LevelViewShadowV1` + `LevelViewRenderHintV1`, а також поле `levels_shadow_v1` у `SmcViewerState`.
 	- Файл: [core/contracts/viewer_state.py](core/contracts/viewer_state.py).
- Зміни (builder):
 	- У [UI_v2/viewer_state_builder.py](UI_v2/viewer_state_builder.py) додано формування `levels_shadow_v1` через helper `_build_levels_shadow_v1(pools, ref_price, asof_ts)`.
 	- Важливо: поле додається **лише якщо список не порожній** (non-breaking).
 	- UI не змінювався.
- Зміни (harness):
 	- У [tools/levels_baseline_harness.py](tools/levels_baseline_harness.py) додано читання `viewer_state.levels_shadow_v1` і секцію `shadow` у `baseline.json` + розширений summary з `match(as-is geometry_hash)`.
 	- Додано базовий тест детермінованості shadow-екстракції у [tests/test_levels_baseline_harness.py](tests/test_levels_baseline_harness.py).

- Тести/перевірки:
 	- `pytest` таргетно: `tests/test_levels_baseline_harness.py` — OK (3 passed).
 	- Запуск harness (короткий прогін): `reports/levels_baseline/20251226_220016_XAUUSD/` — OK.

- Важлива примітка (операційно):
 	- У запущеному UI_v2 сервері `levels_shadow_v1` може не з’явитись одразу, якщо процес ще працює зі старим кодом.
 	- Після рестарту UI_v2 сервера потрібно повторити harness (рекомендовано 20+ знімків) і перевірити, що `shadow.geometry_hash == as-is.geometry_hash` 1:1 на кожному TF.

## 2025-12-26 — Операційно: ринок закритий → переходимо на роботу зі снапшотами (offline + replay)

- Контекст: live FXCM/ринок тимчасово недоступні (2 дні), тому валідації Levels/SMC робимо через історичні снапшоти барів.
- Рекомендований QA-пайплайн (без змін SMC-truth):
	1) Підняти UI_v2 offline сервер (HTTP+WS), який читає OHLCV з UnifiedDataStore:
		- `; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" tools/run_ui_v2_offline.py`
		- Вимога: запущений Redis.
	2) Прогнати реплей бар-за-баром з *_snapshot.jsonl, який будує SmcViewerState і публікує в Redis + WS:
		- `; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" tools/replay_snapshot_to_viewer.py --path datastore/xauusd_bars_5m_snapshot.jsonl --limit 300 --sleep-ms 30`
	3) Паралельно/після цього запускати `tools/levels_baseline_harness.py` для збору метрик і порівняння `as-is` vs `levels_shadow_v1`.

- Ризики/нотатки:
 	- Якщо Redis не піднятий або offline-сервер не запущений, `/smc-viewer/snapshot` може повертати порожній стан → baseline буде тривіально нульовим.
 	- Для детермінованого порівняння бажано запускати harness під час/після реплею з достатньою кількістю барів (20+ снапшотів).

## 2025-12-26 — Run (offline/snapshots): UI_v2 offline + replay + Levels baseline/shadow

- Мета: отримати нетривіальні метрики baseline у режимі "ринок закритий" і перевірити, що `levels_shadow_v1` == as-is (1:1) на офлайн-реплеї.
- План дій:
 	- Підняти UI_v2 offline (порт може авто-зсунутись, напр. 8083/8084).
 	- Запустити replay зі снапшоту: `datastore/xauusd_bars_5m_snapshot.jsonl`.
 	- Запустити baseline harness з `--base-url http://127.0.0.1:8083` (якщо HTTP порт зсунувся).
- Очікуваний результат:
 	- `baseline_summary.md`: `count>0` і `match(as-is geometry_hash): 20/20` для кожного TF.

- Примітка (ізоляція): якщо в Redis є інший продюсер, який перезаписує дефолтний snapshot (`ai_one_local:ui:smc_viewer_snapshot`) порожнім/іншим станом, для QA краще використовувати окремий ключ:
 	- offline UI: `SMC_VIEWER_SNAPSHOT_KEY=ai_one_local:ui:smc_viewer_snapshot_qa_levels`
 	- replay: `--snapshot-key ai_one_local:ui:smc_viewer_snapshot_qa_levels`
 	- (опційно) окремий channel: `--channel ai_one_local:ui:smc_viewer_extended_qa_levels`
	Це прибирає "гонку" і робить baseline нетривіальним.

## 2025-12-26 — Offline (ринок закритий): діагностика `run_ui_v2_offline.py` + QA baseline зі снапшотів

- Контекст: ринок закритий на ~2 дні → live FXCM недоступний, тому вся валідація Levels/Shadow повинна працювати через снапшоти (offline + replay).
- Стан на момент запису:
 	- `tools/run_ui_v2_offline.py` завершується з exit code=1 (сервер не стартує).
 	- `tools/replay_snapshot_to_viewer.py` відпрацьовує OK і пише у QA ключ/канал, але без HTTP-сервера baseline harness не може отримати нетривіальні дані.
 	- Окремий baseline прогін може бути “тривіально нульовим” (count=0) якщо `/smc-viewer/snapshot` повертає порожній стан.

- План дій (мінімальний диф):
	1) Зняти traceback `run_ui_v2_offline.py` і виправити причину падіння.
	2) Підняти офлайн HTTP endpoint `/smc-viewer/snapshot` + `/smc-viewer/ohlcv` на окремому порту/ключі.
	3) Прогнати `tools/levels_baseline_harness.py` на 20+ знімків у офлайн-режимі і підтвердити `match(as-is geometry_hash): 20/20` для кожного TF (і count>0).

## 2025-12-26 — Run (offline): replay_snapshot_to_viewer → baseline harness (netривіально, гейт shadow=as-is)

- Що зроблено:
 	- Піднято UI_v2 offline як окремий процес (через `Start-Process`), щоб паралельно запускати replay/harness.
 	- Виконано replay зі снапшоту барів: `datastore/xauusd_bars_5m_snapshot.jsonl` (350 кроків, sleep=0) з публікацією `SmcViewerState` у Redis key `ai_one_local:ui:smc_viewer_snapshot_qa_levels`.
 	- Запущено baseline harness на 20 знімків по HTTP endpoint офлайн-сервера.

- Артефакти:
 	- `reports/levels_baseline/20251226_222402_XAUUSD/`.

- Результат (з `baseline_summary.md`):
 	- TF=1m/5m/1h/4h:
  		- as-is count: min=max=avg=5.00; geometry_hash unique=1
  		- shadow count: min=max=avg=5.00; geometry_hash unique=1
  		- match(as-is geometry_hash): 20/20 (гейт Кроку 2 пройдено, нетривіально)
 	- Top labels стабільні 20/20: `EQH P`, `EQL`, `SLQ P`, `WICK_C`, `WICK_C P`.

- Нотатки:
 	- У цьому офлайн реплеї as-is baseline дає 5 елементів (не 4 як у live baseline 213929). Це очікувано як різниця даних/стану між live і offline кадрами.
 	- Ключова умова Кроку 2 виконана: `levels_shadow_v1` 1:1 з as-is (по geometry_hash) на всіх TF.

## 2025-12-26 — Levels-V1 / Крок 3.1: джерела рівнів + канонічні labels + stable id (без алгоритмів)

- Мета: підготувати основу для реальних `levels_v1` (SSOT selection) без впливу на UI/рендер.
- Зміст 3.1 (що додаємо зараз):
 	- `LevelSource` (звідки фізично може з’явитись рівень): DAILY/SESSION/RANGE/POOL_DERIVED.
 	- Канонічний whitelist лейблів Levels-V1:
  		- Line: `PDH, PDL, EDH, EDL, ASH, ASL, LSH, LSL, NYH, NYL, RANGE_H, RANGE_L`
  		- Band: `EQH, EQL`
 	- Мапінг `pool_type -> label` тільки для band-рівнів: `EQH/EQL`.
 	- Правило стабільного `id` (line/band) з округленням до tick/decimals для уникнення «перенароджень».
- Важливо (scope):
 	- На цьому кроці ми НЕ вирішуємо “що показувати” і НЕ робимо caps/distance/merge.
 	- TF=1m у `levels_v1` **не просуваємо** (1m відкидаємо); 1m лишається тільки в shadow/as-is до cutover.
- Гейт: 0 змін UI; лише підготовка контрактних/допоміжних визначень + юніт-тести.

## 2025-12-26 — Levels-V1 / Крок 3.1: triple-check (контракти + public API + тести)

- Мета: формально закрити 3.1 “під ключ” перед стартом 3.2.
- Перевіряємо:
 	- `core/contracts/levels_v1.py` (whitelist labels, sources, stable id, 1m не включено)
 	- Експорт через `core.contracts` (public API boundary)
 	- Юніт-тести: прогін таргетних тестів 3 рази для стабільності.
- Очікування: 0 змін UI/алгоритмів; тільки механічні перевірки.

- Результат:
 	- Статичні помилки: не виявлено (levels_v1.py / contracts/**init**.py / tests).
 	- Тести:
  		- `tests/test_levels_v1_contract.py` — OK (5 passed).
  		- `tests/test_levels_baseline_harness.py` — OK (3 passed).
 	- Примітка: повторний прогін одного й того ж файлу тестів інколи може з’являтись як 0/0 у зведенні інструмента, але фінальний комбінований прогін підтвердив `8 passed`.

## 2025-12-26 — Levels-V1 / Крок 3.2.1: каркас кандидатів + поле `levels_candidates_v1` у viewer_state

- Мета: додати “контейнер правди” для кандидатів рівнів (DAILY/SESSION/RANGE/POOL_DERIVED), без алгоритмів відбору.
- Зміни (контракт):
 	- Додаємо тип `LevelCandidateV1` (окремо від selected/shadow).
 	- Додаємо поле `levels_candidates_v1?: list[LevelCandidateV1]` у `SmcViewerState`.
 	- Non-breaking: поле додається у payload лише якщо список не порожній.
- Зміни (builder):
 	- Додаємо helper `_build_levels_candidates_v1(...) -> list[LevelCandidateV1]`.
 	- На цьому кроці helper повертає порожній список (чистий каркас, 0 змістових змін).
- Зміни (harness):
 	- `tools/levels_baseline_harness.py` додає секцію `candidates_v1` у baseline.json + baseline_summary.md.
 	- Порожній список вважається валідним (count=0).
- Гейти:
 	- as-is/shadow статистика не змінюється;
 	- UI 0 змін;
 	- harness не ламається при `levels_candidates_v1=[]` або відсутньому полі.

## 2025-12-27 → SMC Levels-V1: 3.2.2a (day window + policy bar source)

- Дата/час → 2025-12-27 (час: не зафіксовано в логах команди).
- Що зроблено →
 	- Додано конфіг `SMC_DAILY_START_HOUR_UTC: int = 0` (дефолт) як єдину точку правди для визначення «торгового дня» в UTC.
 	- Формалізовано day window: день = [D@start, D+1@start) (UTC).
 	- Додано чисті утиліти `get_day_window_utc()` / `get_prev_day_window_utc()` та експортовано їх через public API `core.contracts`.
 	- Додано policy вибору джерела барів для DAILY кандидатів: 1h preferred, fallback 5m (best-effort відсікання `complete=false`).
 	- Додано юніт-тести на межі day window (start/end + prev day adjacency).
- Де зроблено →
 	- `config/config.py` (константа `SMC_DAILY_START_HOUR_UTC`).
 	- `core/contracts/levels_v1_time.py` (утиліти day window).
 	- `core/contracts/__init__.py` (експорт public API).
 	- `UI_v2/viewer_state_builder.py` (policy `pick_daily_bars_for_levels_v1`, поки не використовується).
 	- `tests/test_levels_v1_time_window.py` (тести day window).
- Причина → старт 3.2.2 (DAILY) з підетапу 3.2.2a: додати лише одну “частину правди” (дефініція дня + політика джерела барів) перед обчисленням PDH/PDL/EDH/EDL у наступному підетапі.
- Тести/перевірки → `pytest tests/test_levels_v1_time_window.py tests/test_levels_v1_contract.py tests/test_levels_baseline_harness.py` (OK).
- Ризики/нотатки/очікуваний результат →
 	- Ризик: “час не зафіксовано” у цьому записі знижує forensic-цінність; далі фіксувати час запуску (локальний або UTC) прямо в заголовку.
 	- Ризик: policy зараз best-effort (не інтегрований у генерацію кандидатів) — це очікувано для 3.2.2a.
 	- Очікуваний результат: наступний підетап 3.2.2b зможе детерміновано брати prev_day/today вікна та обирати TF барів (1h/5m) без зміни UI і без «магії».

## 2025-12-27 → SMC Levels-V1: 3.2.2b (PDH/PDL prev-day candidates)

	- Дата/час → 2025-12-27 (час: не зафіксовано інструментально).
	- Що зроблено →
		- Реалізовано генерацію DAILY кандидатів PDH/PDL з попереднього day window (prev-day) у `levels_candidates_v1`.
		- Кандидати формуються як 6 записів: 2 labels (PDH/PDL) × 3 owner_tf (5m/1h/4h), з однаковим price всередині labels.
		- Вхідні дані для обчислення беруться через `pick_daily_bars_for_levels_v1()` (1h preferred, fallback 5m) + фільтр по prev-day вікну.
		- Додано readiness-гейт: для 1h потрібно >=12 барів у вікні, для 5m потрібно >=100 барів у вікні; інакше кандидатів немає (анти-фейк).
		- `id` формується через stable-id правило 3.1 (`make_level_id_line_v1`) і включає owner_tf.
		- Для offline/replay додано передачу OHLCV у builder через `asset["ohlcv_frames_by_tf"]`.
	- Де зроблено →
		- `UI_v2/viewer_state_builder.py`:
			- `_build_levels_candidates_v1()` тепер додає тільки prev-day PDH/PDL (без EDH/EDL) і лише коли є `frames_by_tf`.
			- `build_prev_day_pdh_pdl_candidates_v1()` + допоміжні `_resolve_asof_ts()` / `_bar_time_s()`.
		- `tools/replay_snapshot_to_viewer.py`: додає `ohlcv_frames_by_tf` у asset (UI /ohlcv-подібна форма барів) для коректного офлайн QA.
		- `tests/test_levels_daily_prev_day_candidates_v1.py`: синтетичні тести PDH/PDL + readiness + детермінізм id.
	- Причина → Крок 3.2.2b має додати одну порцію правди: тільки PDH/PDL з prev-day, без EDH/EDL і без будь-яких caps/distance/merge/UI.
	- Тести/перевірки →
		- `pytest tests/test_levels_daily_prev_day_candidates_v1.py tests/test_levels_v1_time_window.py tests/test_levels_v1_contract.py tests/test_levels_baseline_harness.py tests/test_ui_v2_viewer_state_builder.py` (OK).
	- Ризики/нотатки/очікуваний результат →
		- Ризик: у live-пайплайні `frames_by_tf` поки може бути відсутній, тому кандидати можуть з’являтись лише в replay/QA до окремого підетапу, який заведе канонічне джерело барів у production payload.
		- Ризик: `asof_ts` береться з `replay_cursor_ms` (якщо є) або з meta["ts"] як fallback; для non-replay треба стежити, щоб asof_ts відповідав close_time.
		- Очікуваний результат: після replay 20+ знімків harness має бачити `candidates_v1` з PDH/PDL (по 2 елементи на кожен owner_tf=5m/1h/4h) без регресій baseline/shadow.

- Примітка: пороги для “реального” гейту треба калібрувати під наші очікувані пост-фікс KPI (окремою хвилею), щоб gate ловив регресії, а не просто “проходив завжди”.

## 2025-12-27 → SMC Levels-V1: 3.2.2b (harness gate на offline replay, 20 знімків)

- Дата/час → 2025-12-27 (UTC: не зафіксовано; прогін робився локально).
- Що зроблено →
 	- Посилено `tools/levels_baseline_harness.py`: додано перевірку інваріантів 3.2.2b (PDH/PDL) по `levels_candidates_v1` з опційним strict-гейтом.
 	- Додано strict-прапорці:
  		- `--strict-3-2-2b-pdhpdl` (валідує, що якщо candidates присутні — то це PDH+PDL, `source=DAILY`, `kind=line`, валідний `window_ts`),
  		- `--strict-3-2-2b-pdhpdl-require-present` (для replay/QA: вимагає presence candidates у кожному знімку для TF=5m/1h/4h).
 	- Виправлено валідацію `window_ts`: це пара `(start_ts,end_ts)`, а не int.
 	- Для QA/replay додано `--publish-once-asof-ms` у `tools/replay_snapshot_to_viewer.py`, щоб можна було зафіксувати `asof` на конкретному close_time (коли останній день у снапшоті — без барів).
 	- Для уникнення перезапису від `app.main` використано окремий Redis snapshot key: `ai_one_local:ui:smc_viewer_snapshot_qa_levels`.
- Де зроблено →
 	- `tools/levels_baseline_harness.py` (валідація + summary + strict exit code).
 	- `tools/replay_snapshot_to_viewer.py` (прапорець `--publish-once-asof-ms`).
- Причина → довести 3.2.2b “під ключ”: на offline/replay прогнати harness (20 знімків) і підтвердити, що PDH/PDL candidates відповідають інваріантам, а baseline/shadow не регресують.
- Тести/перевірки →
 	- Publish-once replay (asof=2025-12-24, щоб prev-day=2025-12-23 мав дані):
  		- `C:/Aione_projects/smc_v1/.venv/Scripts/python.exe tools/replay_snapshot_to_viewer.py --path datastore/xauusd_bars_5m_snapshot.jsonl --limit 800 --publish-once --publish-once-asof-ms 1766602200000 --snapshot-key ai_one_local:ui:smc_viewer_snapshot_qa_levels`
 	- Harness (20 знімків, strict-гейт):
  		- `C:/Aione_projects/smc_v1/.venv/Scripts/python.exe tools/levels_baseline_harness.py --base-url http://127.0.0.1:8083 --symbol XAUUSD --samples 20 --interval-sec 0.2 --tfs 5m 1h 4h --strict-3-2-2b-pdhpdl --strict-3-2-2b-pdhpdl-require-present`
 	- Артефакти: `reports/levels_baseline/20251227_020311_XAUUSD/baseline_summary.md` (OK).
- Ризики/нотатки/очікуваний результат →
 	- Нотатка: 25 грудня (свято) у нашому 5m снапшоті не має барів, тому без `asof`-override prev-day кандидати можуть бути порожніми (це очікувано і відповідає readiness-анти-фейку).
 	- Очікуваний результат: у replay/QA при наявних prev-day барах harness стабільно бачить по 2 кандидати (PDH+PDL) для кожного owner_tf=5m/1h/4h, з детермінованим hash і без регресій shadow.

## 2025-12-27 → SMC Levels-V1: 3.2.2c (EDH/EDL today candidates + strict-гейт монотонності)

- Дата/час → 2025-12-27 (час: не зафіксовано інструментально).
- Що зроблено →
 	- Додано генерацію DAILY кандидатів EDH/EDL у межах поточного day window (today) у `levels_candidates_v1`.
 	- Анти-lookahead: today бари фільтруються так, щоб `bar_time <= asof_ts`.
 	- Додано readiness-гейт для today: для 1h потрібно >=3 барів у today window, для 5m потрібно >=20 барів у today window; інакше today кандидати не додаються.
 	- Посилено `tools/levels_baseline_harness.py`: додано strict-гейт 3.2.2c з інваріантами для EDH/EDL та перевіркою монотонності по серії знімків (в межах одного `window_ts`).
  		- Монотонність: EDH не зменшується, EDL не збільшується.
  		- Reset дозволений при зміні `window_ts` (новий торговий день).
  		- Додано прапорці:
   			- `--strict-3-2-2c-edhedl`
   			- `--strict-3-2-2c-edhedl-require-present`
- Де зроблено →
 	- `UI_v2/viewer_state_builder.py`: додано `build_today_edh_edl_candidates_v1(...)` і підключено у `_build_levels_candidates_v1()` після prev-day PDH/PDL.
 	- `tools/levels_baseline_harness.py`: додано валідацію EDH/EDL (інваріанти) + крос-знімкову перевірку монотонності.
 	- `tests/test_levels_v1_today_edh_edl_candidates.py`: тести today HL (readiness/correctness/no-lookahead).
 	- `tests/test_levels_baseline_harness_edhedl_monotonicity.py`: тести монотонності strict-гейту та reset на межі day window.
- Причина → Крок 3.2.2c має додати одну порцію правди: тільки EDH/EDL today як candidates, без caps/distance/merge/selection і без UI cutover.
- Тести/перевірки →
 	- `pytest tests/test_levels_v1_today_edh_edl_candidates.py tests/test_levels_baseline_harness_edhedl_monotonicity.py` (OK).
- Ризики/нотатки/очікуваний результат →
 	- Ризик: today EDH/EDL по визначенню можуть змінюватись протягом дня — тому gate йде не через hash, а через інваріанти+монотонність.
 	- Ризик: у live-пайплайні `frames_by_tf` може бути відсутній до окремого підетапу 3.2.2x (production-джерело барів у payload), тому today кандидати можуть бути лише в replay/QA режимі.
 	- Очікуваний результат: на офлайн replay серії знімків today EDH/EDL проходять strict-гейт монотонності для кожного owner_tf=5m/1h/4h (коли readiness виконано).

## 2025-12-27 03:29:45 → Процес: звірка Log.md + підготовка до 3.2.2c4 (replay+harness, 20+)

- Дата/час → 2025-12-27 03:29:45 (локально).
- Що зроблено →
 	- Ознайомився з поточним Log.md і звірив останній запис.
 	- Підтвердив, що останній зафіксований стан у Log.md відповідає 3.2.2c (EDH/EDL today candidates + strict-гейт монотонності) і що 3.2.2c4 (replay + harness “під ключ”, 20+) ще не задокументований як виконаний у цьому логу.
 	- Зафіксував технічну нотатку по PowerShell: інколи в терміналі інжектиться кирилична `с` на початку команд (симптом: `с; ...`).
- Де зроблено →
 	- `Log.md` (звірка останнього запису).
 	- PowerShell terminal у VS Code (перевірка часу; виявлено інжект `с`).
- Причина → Правило процесу: перед будь-якими діями спершу звірити останній запис у Log.md; далі переходити до підтвердження 3.2.2c4, щоб стан “3.2.2c1–c3 закрито, c4 не підтверджено” був перевірений не зі слів, а прогоном.
- Тести/перевірки →
 	- Перевірка “читанням”: звірено останній запис Log.md на наявність 3.2.2c і відсутність 3.2.2c4.
 	- Планована наступна перевірка: replay (asof під today window) + `tools/levels_baseline_harness.py --samples 20+` зі strict-гейтами 3.2.2c.
- Ризики/нотатки/очікуваний результат →
 	- Нотатка: інжект `с` може ламати запуск команд у PowerShell; за потреби застосувати обхід: `; function с { }` (no-op) у цьому терміналі.
 	- Очікуваний результат наступного кроку: коректно підібраний `asof_ms` (today window має бари) + harness без `validation_issues.md`, із валідним `baseline_summary.md`.

## 2025-12-27 03:30:38 → 3.2.2c4 підготовка: звірка портів/endpoint-ів та Redis snapshot key (UI_v2 offline ↔ replay ↔ harness)

- Дата/час → 2025-12-27 03:30:38 (локально).
- Що зроблено →
 	- Звірив реальні параметри UI_v2 offline та інструментів replay/harness, щоб команди для 3.2.2c4 були узгоджені.
 	- Підтвердив, що `tools/levels_baseline_harness.py` за замовчуванням очікує `--base-url http://127.0.0.1:8080` і звертається до endpoint `.../smc-viewer/snapshot` (тобто шаблон з `:8083` з попередніх нотаток тут не є дефолтом).
 	- Підтвердив, що `tools/run_ui_v2_offline.py` піднімає HTTP (дефолт 8080) і WS (дефолт 8081) та читає `snapshot_key` з `SMC_VIEWER_SNAPSHOT_KEY` (fallback: `config.config.REDIS_SNAPSHOT_KEY_SMC_VIEWER`).
 	- Підтвердив, що `tools/replay_snapshot_to_viewer.py` має прапорець `--publish-once-asof-ms` для QA (щоб зафіксувати asof і гарантувати наявність bar-ів у потрібному today window без lookahead).
 	- Застосовано технічний обхід для PowerShell: додано `function с { }`, щоб інжект кириличної `с` не ламав команди у цьому терміналі.
- Де зроблено →
 	- `tools/run_ui_v2_offline.py` (порти, snapshot_key, WS канал).
 	- `tools/levels_baseline_harness.py` (default `--base-url`, endpoint).
 	- `tools/replay_snapshot_to_viewer.py` (QA прапорці, зокрема `--publish-once-asof-ms`).
- Причина → У 3.2.2c4 важливо, щоб harness читав той самий snapshot, який публікує replay, і щоб base-url вказував на реально запущений офлайн UI (інакше буде хибний “не працює”).
- Тести/перевірки →
 	- Перевірка “читанням коду” (без виконання): звірено дефолти й контракти CLI.
- Ризики/нотатки/очікуваний результат →
 	- Нотатка: якщо паралельно працює прод-пайплайн (`app/main.py`), він може перезаписувати дефолтний snapshot key; для чистого QA доцільно використовувати окремий `--snapshot-key` у replay + `SMC_VIEWER_SNAPSHOT_KEY` для UI_v2 offline.
 	- Очікуваний результат: наступний прогін 3.2.2c4 буде відтворюваним (однакова адреса/ключі), без “фальш-негативів” через неправильний порт.

## 2025-12-27 03:36:38 → 3.2.2c4 (replay+harness): FAIL через розсинхрон Redis namespace (ai_one_local vs ai_one)

- Дата/час → 2025-12-27 03:36:38 (локально).
- Що зроблено →
 	- Виконав publish-once replay зі снапшоту 5m з фіксованим `asof_ms`, щоб today window мав бари.
 	- Запустив `tools/levels_baseline_harness.py` на 20 знімків зі strict-гейтом `--strict-3-2-2c-edhedl --strict-3-2-2c-edhedl-require-present`.
 	- Harness впав з `FAIL: issues=120`.
 	- Прочитав артефакти прогону і встановив причину: harness читав snapshot, де `levels_candidates_v1` відсутні (тобто UI віддає інший Redis snapshot key/namespace, ніж той, куди записав replay).
 	- Діагностика через Redis: перевірив 4 ключі (`ai_one_local:*` і `ai_one:*`). У `ai_one_local:ui:smc_viewer_snapshot` candidates присутні (len=6), у `ai_one:ui:smc_viewer_snapshot` — `levels_candidates_v1` відсутні.
- Де зроблено →
 	- Replay: `tools/replay_snapshot_to_viewer.py`.
 	- Harness: `tools/levels_baseline_harness.py`.
 	- Артефакти: `reports/levels_baseline/20251227_023213_XAUUSD/baseline_summary.md` + `reports/levels_baseline/20251227_023213_XAUUSD/validation_issues.md`.
 	- Діагностика: читання Redis ключів через короткий локальний скрипт (async Redis client).
- Причина → В поточному запуску UI/harness використовує namespace `ai_one` (або інший snapshot_key), тоді як replay publish-once записав результат у namespace `ai_one_local`.
Через це UI endpoint повертав стан без candidates, і strict-гейт закономірно падав як “відсутні”.
- Тести/перевірки →
 	- Replay (publish-once): `... python.exe tools/replay_snapshot_to_viewer.py --path datastore/xauusd_bars_5m_snapshot.jsonl --limit 800 --publish-once --publish-once-asof-ms 1766785499999`.
 	- Harness (strict): `... python.exe tools/levels_baseline_harness.py --base-url http://127.0.0.1:8080 --symbol XAUUSD --samples 20 --interval-sec 0.2 --tfs 5m 1h 4h --strict-3-2-2c-edhedl --strict-3-2-2c-edhedl-require-present`.
- Ризики/нотатки/очікуваний результат →
 	- Нотатка: це не баг у 3.2.2c логіці кандидатів; це інфраструктурний розсинхрон ключів між процесами.
 	- Наступна дія: перепублікувати replay у ключ `ai_one:ui:smc_viewer_snapshot` (через `--snapshot-key`) і повторити harness — очікуємо PASS без `validation_issues.md`.

## 2025-12-27 03:39:41 → 3.2.2c4 (replay + harness “під ключ”, 20+): PASS після фіксу sys.path у harness

- Дата/час → 2025-12-27 03:39:41 (локально).
- Що зроблено →
 	- Діагностовано залишковий FAIL (issues=60): `tools/levels_baseline_harness.py` не міг імпортувати `config`/`core` при запуску як скрипт (`python tools/...`) → `ModuleNotFoundError: No module named 'config'` у перевірці `today_window(asof_ts)`.
 	- Зроблено мінімальний фікс у harness: додано `_ensure_repo_on_syspath()` (аналогічно іншим `tools/*`), щоб корінь репо додавався в `sys.path`.
 	- Повторно прогнано harness на 20 знімків зі strict-гейтом 3.2.2c — отримано PASS.
- Де зроблено →
 	- `tools/levels_baseline_harness.py` (додано `_ensure_repo_on_syspath()` на старті модуля).
 	- Артефакти PASS-прогону: `reports/levels_baseline/20251227_023930_XAUUSD/baseline_summary.md` (OK), `reports/levels_baseline/20251227_023930_XAUUSD/validation_issues.md` (не створено/порожньо).
- Причина → Завершити 3.2.2c4 “під ключ”: strict інваріанти EDH/EDL мають бути перевірені інструментом на 20+ знімків без фальш-негативів через шлях імпорту.
- Тести/перевірки →
 	- Harness (PASS): `... python.exe tools/levels_baseline_harness.py --base-url http://127.0.0.1:8080 --symbol XAUUSD --samples 20 --interval-sec 0.2 --tfs 5m 1h 4h --strict-3-2-2c-edhedl --strict-3-2-2c-edhedl-require-present`.
- Ризики/нотатки/очікуваний результат →
 	- Нотатка: зміна суто інфраструктурна (тільки sys.path для tools), не впливає на SMC-core чи UI truth.
 	- Очікуваний результат: наступні прогони harness, які залежать від `config`/`core.contracts`, будуть стабільно відтворювані незалежно від робочої директорії.

## 2025-12-27 03:42:22 → Log: додано важливу інженерну примітку про 3.2.2x (production ohlcv_frames_by_tf)

- Дата/час → 2025-12-27 03:42:22 (локально).
- Що зроблено →
 	- Додано зверху Log.md окремий блок “ВАЖЛИВА ІНЖЕНЕРНА ПРИМІТКА” з описом кроку 3.2.2x.
 	- Зафіксовано вимогу: до cutover треба забезпечити `asset.ohlcv_frames_by_tf` у live так само, як у replay/QA, інакше DAILY candidates у production можуть бути порожніми.
 	- Вказано мінімальні обсяги барів (1h≈72, 5m≈600) і принцип “без HTTP з builder”.
- Де зроблено →
 	- `Log.md` (верх файлу, одразу після опису формату).
- Причина → Не втратити критичний pre-cutover крок, який визначає, чи будуть DAILY/session candidates реальними у live.
- Тести/перевірки →
 	- Не застосовувались (це нотатка/плановий крок, без змін у коді production).
- Ризики/нотатки/очікуваний результат →
 	- Очікуваний результат: наявність чіткого “рейка-нагадування” для наступної хвилі (3.2.2x) до початку cutover.

## 2025-12-27 03:45:55 → Перевірка статусу: 3.2.2c3 (юніт) + 3.2.2c4 (replay+harness) підтверджено

- Дата/час → 2025-12-27 03:45:55 (локально).
- Що зроблено →
 	- Перевірив наявність/стан юніт-тестів для 3.2.2c3 і повторно прогнав їх таргетно.
 	- Підтвердив, що 3.2.2c4 (replay+harness “під ключ”, 20+) вже має PASS-прогін із артефактами.
- Де зроблено →
 	- Юніт-тести: `tests/test_levels_v1_today_edh_edl_candidates.py`, `tests/test_levels_baseline_harness_edhedl_monotonicity.py`.
 	- Артефакти harness PASS: `reports/levels_baseline/20251227_023930_XAUUSD/baseline_summary.md`.
- Причина → Дати однозначну відповідь “завершено?” на базі перевірок, а не пам’яті чату.
- Тести/перевірки →
 	- `pytest tests/test_levels_v1_today_edh_edl_candidates.py tests/test_levels_baseline_harness_edhedl_monotonicity.py` (OK).
 	- Harness PASS (див. запис 2025-12-27 03:39:41): strict 3.2.2c на 20 знімків.
- Ризики/нотатки/очікуваний результат →
 	- Нотатка: у baseline_summary відображається частина “3.2.2b” як заголовок секції, але по факту в кадрах присутні EDH/EDL (labels EDH/EDL × 20) і strict-гейт 3.2.2c пройдений у PASS-прогоні.
 	- Висновок: 3.2.2c3 і 3.2.2c4 завершені.

## 2025-12-27 04:06:10 → 3.2.3a: SSOT UTC-вікна сесій + утиліти time-window (ASIA/LONDON/NY)

- Дата/час → 2025-12-27 04:06:10 (локально).
- Що зроблено →
 	- Додано SSOT-конфіг `SMC_SESSION_WINDOWS_UTC` (ASIA 22–07, LONDON 07–13, NY 13–22) як основу для SESSION кандидатів (ASH/ASL, LSH/LSL, NYH/NYL).
 	- У `core/contracts/levels_v1_time.py` додано детерміновані утиліти:
  		- `get_session_window_utc()` — повертає вікно сесії, що останньою стартувала відносно `ts` (з коректним переходом через північ).
  		- `find_active_session_tag_utc()` — визначає активну сесію за `ts` у межах [start, end).
 	- Експортовано ці утиліти через `core.contracts` (Public API), щоб builder/harness могли спільно використовувати одну логіку.
- Де зроблено →
 	- `config/config.py` (додано `SMC_SESSION_WINDOWS_UTC`).
 	- `core/contracts/levels_v1_time.py` (session time windows).
 	- `core/contracts/__init__.py` (експорт у Public API).
 	- `tests/test_levels_v1_session_windows_time.py` (нові тести).
- Причина → 3.2.3 SESSION кандидати потребують стабільного визначення “вікна сесії” та активної сесії, щоб забезпечити детермінованість, сумісність між інструментами та уникнути прихованої логіки в UI.
- Тести/перевірки →
 	- `pytest -q tests/test_levels_v1_time_window.py tests/test_levels_v1_session_windows_time.py`
- Ризики/нотатки/очікуваний результат →
 	- Нотатка: core/contracts не імпортує `config` (dependency rule). Конфіг зчитується у доменних шарах і передається у функції як параметр.
 	- Очікуваний результат: наступні кроки 3.2.3b–e зможуть посилатись на одне SSOT-вікно і пройти strict-гейти без “плаваючих” меж.

## 2025-12-27 04:28:30 → 3.2.3b–c: SESSION candidates (ASH/ASL, LSH/LSL, NYH/NYL) + strict-гейти у harness

- Дата/час → 2025-12-27 04:28:30 (локально).
- Що зроблено →
 	- 3.2.3b: у `UI_v2/viewer_state_builder.py` додано побудову SESSION кандидатів:
  		- функція `build_session_high_low_candidates_v1()` (source=SESSION, kind=line, window_ts з `get_session_window_utc`, анти-lookahead `t<=asof_ts`, readiness по кількості барів).
  		- інтеграція у `_build_levels_candidates_v1()` для 3 сесій за `SMC_SESSION_WINDOWS_UTC`: ASIA→ASH/ASL, LONDON→LSH/LSL, NY→NYH/NYL.
 	- 3.2.3c: у `tools/levels_baseline_harness.py` додано strict-гейти SESSION:
  		- `--strict-3-2-3-session` та `--strict-3-2-3-session-require-present`.
  		- інваріанти для кожного пару (0 або 2, labels/source/kind, валідний window_ts, window_ts == session_window(asof_ts)).
  		- cross-snapshot монотонність (HIGH не зменшується, LOW не збільшується) з reset при зміні window_ts.
 	- Додатково: strict-гейт 3.2.2b (PDH/PDL) у harness тепер фільтрує лише PDH/PDL, щоб не ламатися після появи SESSION кандидатів у `levels_candidates_v1`.
- Де зроблено →
 	- `UI_v2/viewer_state_builder.py`.
 	- `tools/levels_baseline_harness.py`.
 	- Тести: `tests/test_levels_v1_session_candidates.py`, `tests/test_levels_baseline_harness_session_monotonicity.py`.
- Причина → Підготувати SESSION candidates як “правду” в presentation-шарі та забезпечити QA-гейти інваріантів/монотонності, аналогічно DAILY EDH/EDL.
- Тести/перевірки →
 	- `pytest -q tests/test_levels_v1_session_candidates.py tests/test_levels_baseline_harness_session_monotonicity.py`
- Ризики/нотатки/очікуваний результат →
 	- Нотатка: SESSION, як і EDH/EDL, за природою може змінюватись в межах активної сесії; тому gate йде через інваріанти + монотонність + reset, а не через geometry_hash.
 	- Очікуваний результат: на кроці 3.2.3e replay+harness 20+ з `--strict-3-2-3-session` має проходити без `validation_issues.md` (за умови достатньої історії барів у snapshot).

## 2025-12-27 04:22:10 → 3.2.3d: юніт-тести SESSION (correctness/readiness/anti-lookahead/монотонність+reset)

- Дата/час → 2025-12-27 04:22:10 (локально).
- Що зроблено →
 	- Розширено юніт-тести 3.2.3d для SESSION кандидатів на синтетичних барах:
  		- correctness HL: екстремуми рахуються лише з барів у вікні;
  		- readiness: fallback на 5m потребує мінімуму барів (NY: 20), і на меншому наборі повертає `[]`;
  		- anti-lookahead: бар у межах вікна, але після `asof_ts`, не впливає на high/low.
 	- Додано негативні тести монотонності (порушення HIGH/LOW у межах одного window_ts) + поведінку `require_present=False`.
- Де зроблено →
 	- `tests/test_levels_v1_session_candidates.py` (додані кейси out-of-window + 5m fallback + anti-lookahead).
 	- `tests/test_levels_baseline_harness_session_monotonicity.py` (додані негативні кейси).
- Причина → Закрити 3.2.3d як correctness-рівень: не лише “є кандидати”, а й строгі правила їх побудови перевірені на синтетичних даних.
- Тести/перевірки →
 	- `pytest -q tests/test_levels_v1_session_candidates.py tests/test_levels_baseline_harness_session_monotonicity.py` (OK).

## 2025-12-27 04:20:40 → 3.2.3e: replay publish-once + harness 20+ strict SESSION (PASS, без validation_issues.md)

- Дата/час → 2025-12-27 04:20:40 (локально).
- Що зроблено →
 	- Зроблено publish-once з фіксованим `--publish-once-asof-ms`, щоб у кадрі були достатні 5m бари і readiness був виконаний хоча б для однієї сесії.
 	- Прогнано baseline harness на 25 знімків з strict SESSION гейтами.
- Ключова примітка (важливо для відтворюваності) →
 	- У цьому репо дефолтний `NAMESPACE` для Redis може бути `ai_one_local` (локальний режим), але у вже запущеному UI офлайн у користувача snapshot key був `ai_one:*`.
 	- Якщо publish-once зробити без override — він запише в `ai_one_local:*`, а UI/HTTP, що читає `ai_one:*`, не побачить SESSION лейбли → strict-гейти дадуть фальш-фейл “candidates відсутні”.
 	- Рішення: для узгодження треба або запускати UI у тому ж namespace, або робити publish-once з явними `--snapshot-key`/`--channel`.
- Команди (PowerShell) →
 	- Publish-once (узгоджено з UI snapshot key `ai_one:*`):

	```powershell
	; function с { }
	; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" tools/replay_snapshot_to_viewer.py --path datastore/xauusd_bars_5m_snapshot.jsonl --limit 800 --publish-once --publish-once-asof-ms 1766602200000 --snapshot-key ai_one:ui:smc_viewer_snapshot --channel ai_one:ui:smc_viewer_extended
	```

 	- Harness (PASS):

	```powershell
	; function с { }
	; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" tools/levels_baseline_harness.py --base-url http://127.0.0.1:8080 --symbol XAUUSD --samples 25 --interval-sec 0.2 --strict-3-2-3-session --strict-3-2-3-session-require-present --out-dir reports/levels_baseline/20251227_0420_XAUUSD_session_strict
	```

- Артефакти →
 	- `reports/levels_baseline/20251227_0420_XAUUSD_session_strict/baseline_summary.md` (містить SESSION лейбли ASH/ASL/LSH/LSL/NYH/NYL).
 	- `validation_issues.md` НЕ створено (harness OK).
- Ризики/нотатки/очікуваний результат →
 	- Нотатка: `asof_ms=1766602200000` підібраний як timestamp, де всі 3 сесії мали readiness на доступних 5m барах (потрібно для `--strict-3-2-3-session-require-present`).
 	- Очікуваний результат: 3.2.3e вважається закритим “під ключ” при стабільному namespace/keys.

## 2025-12-27 04:27:59 → Namespace policy: `ai_one_local` / `ai_one_prod` (legacy `ai_one` прибрано з дефолтів та прикладів)

- Дата/час → 2025-12-27 04:27:59 (локально).
- Що зроблено →
 	- Політику Redis namespace вирівняно під новий стандарт live/QA:
  		- `local` → `ai_one_local`
  		- `prod` → `ai_one_prod`
 	- Прибрано/позначено як legacy старий namespace `ai_one` у дефолтах і прикладах конфігів.
 	- Оновлено `.env.prod` / `.env.prod.example` та `docs/connector_profiles.md`, щоб не вводили в оману щодо дефолтного namespace і способу вибору профілю.
- Де зроблено →
 	- `config/config.py` (дефолтний namespace для `prod` → `ai_one_prod`).
 	- `app/settings.py` (узгодження namespace для `load_datastore_cfg()` з профілями + захист від legacy `ai_one` у YAML).
 	- `config/datastore.yaml` (прибрано `namespace: ai_one` та `admin.commands_channel: ai_one:...`, щоб YAML не форсив legacy).
 	- `app/main.py` (попередження для локального запуску, якщо підхоплено prod/legacy namespace).
 	- `.env.prod`, `.env.prod.example`, `docs/connector_profiles.md` (оновлені приклади).
 	- `UI_v2/smc_viewer_broadcaster.py`, `data/unified_store.py` (уточнення докстрінгів/прикладів).
- Тести/перевірки →
 	- `pytest -q tests/test_config_run_mode_namespace.py tests/test_env_selection.py tests/test_app_settings_datastore_cfg_paths.py` (OK).
- Ризики/нотатки →
 	- Якщо десь у зовнішніх сервісах/скриптах ще очікується `ai_one` як продовий namespace — треба або оновити їх на `ai_one_prod`, або явно задати `AI_ONE_NAMESPACE=ai_one` (тільки як тимчасовий legacy-режим).

## 2025-12-27 04:51:15 → 3.2.4a: RANGE candidates (source inventory + contract invariants)

- Дата/час → 2025-12-27 04:51:15 (локально).
- Що зроблено →
 	- Додано best-effort RANGE кандидати `RANGE_H`/`RANGE_L` без будь-яких fallback-обчислень з 5m.
 	- Джерело RANGE береться тільки якщо значення вже є в payload (пріоритет: `key_levels` → `context` → `htf_lite`). Якщо значень немає — RANGE не додається.
 	- Інваріанти (жорсткі): потрібні обидва значення `range_high/range_low` (або синоніми), і `range_high > range_low`.
 	- Формат: додаємо 0 або 6 записів (RANGE_H/RANGE_L × owner_tf=5m/1h/4h), `source=RANGE`, `kind=line`, `window_ts` переносимо тільки якщо він присутній у джерелі; інакше `None`.
- Де зроблено →
 	- `UI_v2/viewer_state_builder.py`:
  		- `try_extract_range_high_low_v1(...)` + `extend_range_candidates_v1(...)`.
  		- `_build_levels_candidates_v1(...)` розширено, щоб RANGE міг додаватися навіть коли `ohlcv_frames_by_tf` відсутні (DAILY/SESSION як і раніше потребують frames).
 	- `tests/test_levels_v1_range_candidates.py` (нові юніт-тести для RANGE).
- Тести/перевірки →
 	- `pytest -q tests/test_levels_v1_range_candidates.py tests/test_levels_v1_session_candidates.py` (OK).
- Правило процесу (важливо для QA/відтворюваності) →
 	- Будь-який replay/harness завжди запускаємо з явним `--snapshot-key` (і, якщо є WS, з явним `--channel`).
 	- UI offline завжди запускаємо з явним `SMC_VIEWER_SNAPSHOT_KEY`.
 	- Інакше можливі фальш-фейли strict-гейтів через namespace mismatch (publish пише в одні ключі, UI читає з інших).

## 2025-12-27 04:58:48 → 3.2.4b0: RANGE carrier census (знімок snapshot.json + probe.md)

- Дата/час → 2025-12-27 04:58:48 (локально).
- Що зроблено →
 	- Додано інструмент для “no-assumptions” інвентаризації RANGE-carriers у реальному payload з `/smc-viewer/snapshot`.
 	- Знято один snapshot для `symbol=XAUUSD`, збережено сирий JSON та згенеровано `probe.md` з фактами/шляхами.
- Де зроблено →
 	- Скрипт: [tools/levels_range_probe_b0.py](tools/levels_range_probe_b0.py).
 	- Артефакти: `reports/levels_range_probe/20251227_045848/snapshot.json`, `reports/levels_range_probe/20251227_045848/probe.md`.
- Факти (з probe) →
 	- `state_path_hint: $` (root).
 	- `$.liquidity.meta: ABSENT`.
 	- `$.key_levels: ABSENT`.
 	- `$.liquidity.pools: count=0; RANGE_EXTREME=0` (у цьому snapshot RANGE truth не присутній через pools).
- Тести/перевірки →
 	- Smoke-run: `python tools/levels_range_probe_b0.py --symbol XAUUSD` (OK).
- Ризики/нотатки →
 	- Цей snapshot містить `levels_candidates_v1`, але `liquidity` у ньому порожній — для 3.2.4b1 потрібен знімок, де `liquidity.pools` реально наповнений (live або replay з увімкненим liquidity-блоком).

## 2025-12-27 05:02:24 → 3.2.4b0 (уточнення): RANGE_EXTREME carrier у цьому payload = `liquidity.magnets[*].pools` (liq_type)

- Контекст → первинний probe шукав `liquidity.pools[type=RANGE_EXTREME]`, але поточний snapshot endpoint віддає liquidity pools усередині `liquidity.magnets[*].pools` з полем `liq_type`.
- Артефакти →
 	- `reports/levels_range_probe/20251227_050224/snapshot.json`
 	- `reports/levels_range_probe/20251227_050224/probe.md`
- Факти (з probe #2) →
 	- `$.liquidity.pools: count=0; RANGE_EXTREME=0`
 	- `$.liquidity.magnets: count=1; pools_total=9; RANGE_EXTREME(liq_type)=2`
 	- Приклади RANGE_EXTREME (канонічні поля):
  		- `$.liquidity.magnets[0].pools[*].liq_type == RANGE_EXTREME`
  		- рівні: `level=4477.17 (side=LOW)`, `level=4482.93 (side=HIGH)`
- Висновок → для 3.2.4b1 truth-побудову RANGE_H/L треба брати з `liquidity.magnets[*].pools[liq_type=RANGE_EXTREME]` (а не з `liquidity.pools`).

## 2025-12-27 05:18:08 → 3.2.4b1–3.2.4b2: RANGE канонізація (magnets pools) + strict-гейт у harness

- Що зроблено →
  - 3.2.4b1: RANGE кандидати беруться лише з `liquidity.magnets[*].pools[liq_type=RANGE_EXTREME].level` (без key_levels/context/htf_lite).
  - RANGE dedup по tick tolerance; потрібно 2+ унікальні рівні; `high > low`; `window_ts=None`.
  - Оновлено тести RANGE під magnets-only.
  - 3.2.4b2: додано harness-флаги `--strict-3-2-4-range` та `--strict-3-2-4-range-require-present` + валідація інваріантів.
- Де зроблено →
  - `UI_v2/viewer_state_builder.py`, `tests/test_levels_v1_range_candidates.py`, `tools/levels_baseline_harness.py`.
- Тести/перевірки →
  - `pytest -q tests/test_levels_v1_range_candidates.py` (OK)
  - `python -m py_compile tools/levels_baseline_harness.py` (OK)

## 2025-12-27 05:23:00 → 3.2.4b3: PASS (replay + harness, strict RANGE, 25 знімків)

- Контекст → UI_v2 offline запущено локально (http://127.0.0.1:8080), Redis 127.0.0.1:6379, snapshot_key=ai_one:ui:smc_viewer_snapshot.
- Команди (факт запуску) →
  - Replay (публікація в Redis у правильний ключ/канал):
    - python tools/replay_snapshot_to_viewer.py --path datastore/xauusd_bars_5m_snapshot.jsonl --limit 800 --sleep-ms 80 --snapshot-key ai_one:ui:smc_viewer_snapshot --channel ai_one:ui:smc_viewer_extended
  - Harness (strict RANGE require-present, 25 знімків):
    - python tools/levels_baseline_harness.py --base-url http://127.0.0.1:8080 --symbol XAUUSD --samples 25 --interval-sec 0.2 --strict-3-2-4-range --strict-3-2-4-range-require-present --out-dir reports/levels_baseline/20251227_0523_XAUUSD_range_strict
- Результат →
  - OK: baseline збережено в reports/levels_baseline/20251227_0523_XAUUSD_range_strict.
		- validation_issues.md не створено → strict-гейт 3.2.4 RANGE пройдено “під ключ”.
  - У baseline_summary.md RANGE_H/RANGE_L присутні 25/25 для TF=5m/1h/4h (відповідає require-present).

## 2025-12-27 05:38:24 → 3.2.2b-harness-fix: strict PDH/PDL фільтрація + читабельний звіт

- Проблема → `baseline_summary.md` та `validation_issues.md` могли показувати фальш-issues для 3.2.2b, коли список `levels_candidates_v1` став більшим (SESSION/EDH/EDL/RANGE). Додатково, для SESSION монотонності дублювалось репортування absence.
- Що зроблено →
  - У strict-гейтах harness звузив фільтри до канонічних піднаборів (label+source+kind) для:
    - PDH/PDL (3.2.2b), EDH/EDL (3.2.2c), SESSION (3.2.3), RANGE_H/RANGE_L (3.2.4b1).
  - У cross-snapshot (монотонність) для SESSION прибрано дубльовані issues про absence: presence перевіряється лише per-snapshot strict-гейтом.
  - Повідомлення `validation_issues.md` зроблено контекстними (рядки мають префікси `PDH/PDL`, `EDH/EDL`, `ASH/ASL` тощо), щоб не було “безіменних” `candidates відсутні`.
  - `baseline["validation"].strict_3_2_2b_pdhpdl` тепер відображає реальний стан флага (bool), а не завжди `True`.
- Де зроблено → `tools/levels_baseline_harness.py`.
- Тести/перевірки →
  - `python -m py_compile tools/levels_baseline_harness.py` (OK)
  - Smoke-run (5 знімків, strict+require-present) → `reports/levels_baseline/_smoke_3_2_2b_harness_fix/validation_issues.md` містить лише очікувані причини (відсутні PDH/PDL та ASH/ASL), без дублювань з монотонності.

## 2025-12-27 06:13:27 → 3.2.5a: EQ carrier census (probe) + факт-діагноз PDH/PDL=0/25

- Що зроблено →
  - Додано probe-скрипт для “no-assumptions” інвентаризації EQ-carriers у реальному payload з `/smc-viewer/snapshot`.
  - Знято snapshot для `symbol=XAUUSD`, збережено сирий JSON та згенеровано `probe.md` з фактами/шляхами.
- Де зроблено →
  - Скрипт: [tools/levels_eq_probe_3_2_5a.py](tools/levels_eq_probe_3_2_5a.py).
  - Артефакти: `reports/levels_eq_probe/20251227_054351/snapshot.json`, `reports/levels_eq_probe/20251227_054351/probe.md`.
- Факти (truth carriers) →
  - EQ присутній як carrier у `liquidity.magnets[*].pools`:
    - `liq_type=EQH` і `liq_type=EQL` (по 1 кожного у цьому snapshot).
    - Ці pool'и мають `level` (скаляр), і **не мають** явних `top/bot` у полях верхнього рівня.
    - Додатковий носій ширини (потенційно band truth): `source_swings[*].price` усередині EQ pool (кластерний розкид), з якого можна відновити top/bot як max/min.
  - `liquidity.pools` (не magnets) також містить `type=EQH/EQL`, але це не гарантує правду для band-геометрії без boundaries.
  - `levels_shadow_v1` уже показує `kind=band` для EQH/EQL, але наразі top=bot=price (це UI-presentational, не truth).
- Факт-діагноз PDH/PDL=0/25 →
  - Поточна логіка `UI_v2/viewer_state_builder.py: build_prev_day_pdh_pdl_candidates_v1()` має readiness-поріг для prev_day:
    - якщо source_tf=1h → потрібно >=12 барів у prev-day window;
    - якщо source_tf=5m → потрібно >=100 барів у prev-day window.
  - У поточному replay snapshot (з `datastore/xauusd_bars_5m_snapshot.jsonl`) `asof_ts` сидить близько `2025-12-26T21:45:00Z` (за close_time).
  - Але набір барів, який офлайн UI віддає через `/ohlcv` для кожного TF, імовірно не містить достатньо барів саме з prev-day window,
    тому readiness не проходить → PDH/PDL не додаються.

## 2025-12-27 06:21:20 → 3.2.5b–c: EQH/EQL як truth bands (magnets) + strict-гейт у harness

- Що зроблено →
  - 3.2.5b: додано канонічне формування EQ band candidates (EQH/EQL) з `liquidity.magnets[*].pools`.
    - Truth для ширини band: `source_swings[*].price` → `top=max`, `bot=min`.
    - Anti-fake: якщо немає >=2 унікальних swing-цін (з tick-tolerance) — кандидати не додаються.
    - Інваріант: або 0, або 6 (EQH/EQL × owner_tf=5m/1h/4h), `window_ts=None`, `source=POOL_DERIVED`, `kind=band`.
  - 3.2.5c: додано strict-гейт у baseline harness для EQH/EQL:
    - флаги `--strict-3-2-5-eq` та `--strict-3-2-5-eq-require-present`;
    - перевіряє 0/2 на TF, `top>bot`, `price=None`, `window_ts=None`, `source=POOL_DERIVED`, `kind=band`.
  - Додано юніт-тести на EQ bands.
- Де зроблено →
  - `UI_v2/viewer_state_builder.py`, `tools/levels_baseline_harness.py`, `tests/test_levels_v1_eq_band_candidates.py`.
- Тести/перевірки →
  - `pytest -q tests/test_levels_v1_eq_band_candidates.py` (OK)
  - `pytest -q tests/test_levels_v1_range_candidates.py` (OK)
- Ризики/нотатки →
  - Якщо у payload є лише `level` для EQH/EQL без `source_swings`, кандидати не з’являться (це навмисно: anti-fake).

## 2025-12-27 06:23:12 → 3.2.5c: PASS (replay + harness, strict EQ, 25 знімків)

- Контекст → офлайн UI_v2 (http://127.0.0.1:8080) + replay у Redis (`ai_one:ui:smc_viewer_snapshot`).
- Команди (факт запуску) →

```text
Replay:
python tools/replay_snapshot_to_viewer.py --path datastore/xauusd_bars_5m_snapshot.jsonl --limit 800 --sleep-ms 80 --snapshot-key ai_one:ui:smc_viewer_snapshot --channel ai_one:ui:smc_viewer_extended

Harness (strict RANGE+EQ require-present, 25 знімків):
python tools/levels_baseline_harness.py --base-url http://127.0.0.1:8080 --symbol XAUUSD --samples 25 --interval-sec 0.2 --strict-3-2-4-range --strict-3-2-4-range-require-present --strict-3-2-5-eq --strict-3-2-5-eq-require-present --out-dir reports/levels_baseline/20251227_0623_XAUUSD_range_eq_strict
```

- Результат →
  - OK: baseline збережено в `reports/levels_baseline/20251227_0623_XAUUSD_range_eq_strict`.
  - validation_issues.md не створено → strict-гейти 3.2.4 (RANGE) і 3.2.5 (EQ) пройдено “під ключ”.

## 2025-12-27 09:16:45 → 3.2.2x1/x2/x3: гарантія OHLCV frames для PDH/PDL+SESSION + all-strict PASS

- Що зроблено →
 	- 3.2.2x1 (replay): `tools/replay_snapshot_to_viewer.py` формує `ohlcv_frames_by_tf` для Levels-V1 як “останній N complete барів до asof_ts” без lookahead.
  		- Мінімальні обсяги: `5m>=600`, `1h>=72`, `4h>=48`.
 	- 3.2.2x2 (offline HTTP): у `UI_v2/viewer_state_server.py` піднято дефолт `DEFAULT_OHLCV_LIMIT` до `600`.
 	- 3.2.2x3 (live): у `app/smc_producer.py` додано формування `asset["ohlcv_frames_by_tf"]` з `UnifiedDataStore` (kill-switch у `config/config.py`).
 	- SESSION strict: прибрано fallback на попередні дні у `build_session_high_low_candidates_v1`, щоб `window_ts` завжди відповідав `session_window(asof_ts)`.
- Тести/перевірки →
 	- Baseline harness з **усіма strict + require-present** (PDH/PDL + EDH/EDL + SESSION + RANGE + EQ), 25 знімків:

```text
python tools/replay_snapshot_to_viewer.py --path datastore/xauusd_bars_5m_snapshot.jsonl --limit 800 --window 800 --publish-once --tv-like --snapshot-key ai_one:ui:smc_viewer_snapshot --channel ai_one:ui:smc_viewer_extended --publish-once-asof-ms 1766612700000

python tools/levels_baseline_harness.py --base-url http://127.0.0.1:8080 --symbol XAUUSD --tfs 5m 1h 4h --samples 25 --ohlcv-limit 600 --strict-3-2-2b-pdhpdl --strict-3-2-2b-pdhpdl-require-present --strict-3-2-2c-edhedl --strict-3-2-2c-edhedl-require-present --strict-3-2-3-session --strict-3-2-3-session-require-present --strict-3-2-4-range --strict-3-2-4-range-require-present --strict-3-2-5-eq --strict-3-2-5-eq-require-present
```

	- Результат: OK baseline збережено в `reports/levels_baseline/20251227_081631_XAUUSD`; `validation_issues.md` не створено.

- Ризики/нотатки →
 	- Якщо останній день у снапшоті — holiday/market-closure, prev-day/session readiness може природно не виконуватись. Для таких QA кейсів фіксуй `--publish-once-asof-ms` на торговий день.

## 2025-12-27 — 3.3a: контракт levels_selected_v1 + секція selected у harness (без алгоритмів)

- Зміни →
 	- Додано контракт `levels_selected_v1` у `SmcViewerState` (optional, non-breaking), тип `LevelSelectedV1`.
 	- Додано stub у `UI_v2/viewer_state_builder.py`: `_build_levels_selected_v1(...) -> []` (на 3.3a завжди 0 selected).
 	- Розширено `tools/levels_baseline_harness.py`: збір `selected_v1` у baseline.json + `selected_geometry_hash` + summary-секція `selected_v1`.

- QA/гейти →
 	- Baseline harness (25 snapshots, all-strict: PDH/PDL + EDH/EDL + SESSION + RANGE + EQ) → PASS.
 	- `levels_selected_v1`: count min=0 max=0 avg=0.00, без регресій baseline/shadow/candidates.
 	- Артефакт: `reports/levels_baseline/20251227_082410_XAUUSD` (baseline.json, baseline_summary.md).

- Ризики/нотатки →
 	- Це лише контракт + інструментарій; selection-логіки та UI cutover немає (буде в 3.3b–d).

## 2025-12-27 09:51:52 → Процес: log-first як правило №1 (підтверджено)

- Що зроблено →
 	- Підтверджено процес: **кожна** дія/зміна в репозиторії супроводжується записом у `Log.md` (без виключень), пріоритет логів вищий за тести/відповідь у чаті.
 	- Формат запису: дата/час → що зроблено → де зроблено → причина → тести/перевірки → ризики/нотатки/очікуваний результат.
 	- Записи додаються **знизу** (кожен новий запис нижче попередніх).
 	- У чаті відповідь може бути мінімальною або відсутньою; основний канал аудиту — `Log.md`.

- Де зроблено →
 	- `Log.md` (цей запис як фіксація правила/процесу).

- Причина →
 	- Вимога користувача: забезпечити відтворюваність, діагностику та контроль змін у часі; уникати “тихих” правок без пояснення.

- Тести/перевірки →
 	- N/A (процесна домовленість, без змін коду).

- Ризики/нотатки/очікуваний результат →
 	- Очікування: швидше знаходження причин регресій, чіткіший контекст рішень, простіша відладка.
 	- Ризик: незначне збільшення часу на мікро-правки через дисципліну логування (прийнятно).

## 2025-12-27 10:00:01 → 3.3a: levels_selected_v1 як контейнер (stub=0) + PASS на 25 snapshots

- Що зроблено →
 	- Приведено реалізацію `levels_selected_v1` до **кроку 3.3a** (без алгоритмів selection/merge/caps/distance/freeze): builder повертає порожній список `[]`, поле в payload не додається.
 	- Оновлено юніт-тест під 3.3a: тепер очікуємо, що `levels_selected_v1` може бути відсутнім/порожнім, при цьому `levels_candidates_v1` лишається присутнім.

- Де зроблено →
 	- UI_v2/viewer_state_builder.py: `_build_levels_selected_v1(...) -> []`.
 	- tests/test_ui_v2_viewer_state_builder.py: тест `test_build_viewer_state_levels_selected_v1_is_empty_on_3_3a`.

- Причина →
 	- За планом 3.3 спочатку фіксуємо контракт + plumbing (3.3a) і лише потім переходимо до merge (3.3b). Це знижує ризик і дає чистий базис для гейтів.

- Тести/перевірки →
 	- pytest (таргетно): `pytest tests/test_ui_v2_viewer_state_builder.py` → PASS.
 	- Baseline harness (25 snapshots, all-strict candidates) → PASS; `levels_selected_v1` у всіх знімках = 0.
 	- Артефакт: `reports/levels_baseline/20251227_085935_XAUUSD`.

- Ризики/нотатки/очікуваний результат →
 	- Очікувано: UI не змінюється (cutover не робимо), `levels_selected_v1` поки служить лише контейнером/контрактом.
 	- Ризик: наступний крок 3.3b потребуватиме нових strict-гейтів (merge 1:1 з candidates) та оновлення тестів/хорнеса відповідно.

## 2025-12-27 — 3.3b: levels_selected_v1 як 1:1 merge з candidates (rank/reason/selected_at_close_ts)

- Що зроблено →
 	- Реалізовано крок **3.3b** у `UI_v2/viewer_state_builder.py`: `levels_selected_v1` тепер формується як **детермінований 1:1 merge** з `levels_candidates_v1`.
  		- Геометрія/лейбли/джерело переносяться 1:1 з candidates.
  		- Додано selection meta:
   			- `rank` (пер-TF, 1..N без пропусків),
   			- `reason=["MERGE_FROM_CANDIDATE_V1"]`,
   			- `selected_at_close_ts` (секунди; беремо з `candidate.asof_ts`, fallback — `payload_meta.replay_cursor_ms`).
  		- `distance_at_select` на цьому кроці лишається `None` (caps/distance/prio/freeze — наступні кроки 3.3c–d).

- Де зроблено →
 	- UI_v2/viewer_state_builder.py: `_build_levels_selected_v1(...)`.
 	- tests/test_ui_v2_viewer_state_builder.py: оновлено тест під 3.3b.

- Тести/перевірки →
 	- pytest (таргетно): `pytest tests/test_ui_v2_viewer_state_builder.py` → PASS.

- Ризики/нотатки/очікуваний результат →
 	- Це **ще не UI cutover**: UI як і раніше не повинен рендерити selected напряму до завершення 3.3d.
 	- Наступний QA-крок: baseline harness із `--strict-3-3b-merge --strict-3-3b-merge-require-present` (25 snapshots) для підтвердження 1:1 інваріантів на інтеграційному потоці.

## 2025-12-27 09:11:28 → 3.3b: PASS (offline UI + baseline harness, strict merge, 25 знімків)

- Контекст → офлайн UI_v2 (http://127.0.0.1:8080), дані беруться з активного snapshot потоку (Redis key налаштовано поза harness).
- Команда (факт запуску) →

```text
python tools/levels_baseline_harness.py --base-url http://127.0.0.1:8080 --symbol XAUUSD --samples 25 --interval-sec 0.7 --strict-3-3b-merge --strict-3-3b-merge-require-present
```

- Результат →
 	- OK: baseline збережено в `reports/levels_baseline/20251227_091128_XAUUSD`.
 	- `validation_issues.md` не створено → strict-гейт 3.3b (1:1 merge candidates→selected + require-present) пройдено.

## 2025-12-27 — 3.3c: Selection policy по TF (caps + distance + пріоритети) + strict-гейт caps

- Що зроблено →
 	- Додано SSOT таблицю політик selection `LEVELS_SELECTED_POLICY_V1` (3.3c0): caps по TF, правила пріоритетів та гейти по distance.
 	- Реалізовано 3.3c1: `levels_selected_v1` тепер формується як selection (не 1:1 merge) з урахуванням:
  		- caps: 5m (lines<=3, bands<=2), 1h/4h (lines<=6, bands<=2), 1m — selected вимкнено.
  		- distance-гейтів: `<= 2.5 * ATR_5m` для 5m, `<= 1.5 * DR_4h` (або fallback `<= 6 * ATR_5m`) для 1h/4h.
  		- пріоритетів: PDH/PDL (always), потім active session → ED → RANGE.
 	- Додано strict-гейт у baseline harness: `--strict-3-3-selected-caps` (валідація caps по TF на кожному snapshot).
 	- Оновлено юніт-тести під 3.3c інваріанти (caps + rank 1..N + selected_at_close_ts).

- Де зроблено →
 	- UI_v2/viewer_state_builder.py: policy + selection (3.3c0/3.3c1).
 	- tools/levels_baseline_harness.py: strict прапор `--strict-3-3-selected-caps` + валідатор caps.
 	- tests/test_ui_v2_viewer_state_builder.py: новий тест на caps + коректний `selected_at_close_ts`.

- Тести/перевірки →
 	- pytest (таргетно): `pytest tests/test_ui_v2_viewer_state_builder.py` → PASS.
 	- Baseline harness (25 snapshots) зі strict caps → PASS (після republish snapshot; див. нижче).

- Інтеграційний прогін (важливо: stale snapshot) →
 	- Перші спроби strict caps **падали**, бо офлайн UI віддавав **старий snapshot** з Redis (ще зі старою логікою 3.3b merge):
  		- Артефакт FAIL: `reports/levels_baseline/20251227_092117_XAUUSD/validation_issues.md`.
  		- Артефакт FAIL (інший порт офлайн UI): `reports/levels_baseline/20251227_092227_XAUUSD/validation_issues.md`.
  		- Симптом: `TF=5m lines=8 > 3` (і аналогічно для 1h/4h).
 	- Діагноз → harness не оновлює snapshot у Redis; якщо UI працює в offline-режимі, то для інтеграційного гейта потрібен **republish** snapshot новим кодом.
 	- Після republish snapshot (publish-once) → strict caps **PASS**:
  		- OK: baseline збережено в `reports/levels_baseline/20251227_092413_XAUUSD`.

- Ризики/нотатки/очікуваний результат →
 	- Це **ще не UI cutover**: UI не має починати рендерити selected напряму до завершення 3.3d (freeze on close).
 	- Операційний ризик: при перевірках через офлайн UI треба контролювати, що snapshot у Redis актуальний (інакше отримаємо хибні FAIL/OK).

## 2025-12-27 — 3.3d: freeze-on-close для levels_selected_v1 + strict-гейт стабільності

- Що зроблено →
 	- Реалізовано **freeze-on-close** для `levels_selected_v1` у `UI_v2.viewer_state_builder`:
  		- на close: формуємо selected і кешуємо;
  		- на preview: повертаємо попередній close-стан з кешу (без перерахунку), щоб прибрати preview-vs-close фліккер.
 	- Додано strict-гейт у baseline harness: `--strict-3-3d-freeze-on-close`.
  		- Якщо `selected_at_close_ts` не змінюється для TF, то `selected_v1.items` мають бути ідентичними між snapshot'ами.
 	- Відновлено коректну прив’язку strict-перевірок 3.2.* у harness: PDH/ED/SESSION/RANGE/EQ перевіряються в циклі по TF (а не по випадковому “останньому” tf).

- Де зроблено →
 	- UI_v2/viewer_state_builder.py: `ViewerStateCache.last_levels_selected_v1` + freeze логіка (preview→кеш, close→оновлення).
 	- tools/levels_baseline_harness.py: прапор `--strict-3-3d-freeze-on-close` + cross-snapshot перевірка.
 	- tests/test_ui_v2_viewer_state_builder.py: новий тест `test_build_viewer_state_levels_selected_v1_freezes_on_close_3_3d`.

- Тести/перевірки →
 	- pytest (таргетно): `pytest tests/test_ui_v2_viewer_state_builder.py` → PASS.

- Нотатки/обмеження →
 	- Freeze-on-close працює лише коли `build_viewer_state(..., cache=ViewerStateCache())` використовується як спільний кеш між snapshot'ами (як у офлайн UI/HTTP серверах).
 	- UI cutover все ще **заборонено** до окремого підтвердження 3.3d на інтеграційному baseline harness (25 snapshots) у твоєму потоці.

## 2025-12-27 12:22:01 → 3.3e: Візуальні контрольні кадри selected (перед UI cutover)

- Мета →
 	- Підтвердити “вигляд трейдерського графіка” через payload `levels_selected_v1`, **без будь-яких змін UI-логіки**.
 	- Зібрати 1–2 контрольні кадри (TF=5m) + коротке пояснення `reason[]`.

- Що зроблено →
 	- Додано tool-скрипт експорту кадрів: `tools/export_levels_selected_frames.py`.
 	- Скрипт читає `/smc-viewer/snapshot` і зберігає:
  		- `selected_5m.json` (1–2 кадри у вигляді списку frames),
  		- `selected_summary.md` (пояснення `reason[]` + список items).
 	- UI cutover не виконується; `levels_shadow_v1` лишається як тимчасовий A/B референс у payload (окремо, без змін у цій хвилі).

- Команда (факт запуску) →

```text
python tools/export_levels_selected_frames.py --base-url http://127.0.0.1:8083 --symbol XAUUSD --frames 2 --interval-sec 0.7
```

- Артефакти →
 	- `reports/levels_selected_frames/20251227_122201_XAUUSD/selected_5m.json`
 	- `reports/levels_selected_frames/20251227_122201_XAUUSD/selected_summary.md`

- Ризики/нотатки →
 	- Якщо офлайн UI віддає stale snapshot з Redis, кадри можуть не відповідати актуальному коду (у такому разі треба зробити republish snapshot і повторити експорт).

## 2025-12-27 12:23:16 → 3.3d: PASS (offline UI + baseline harness, strict caps + strict freeze, 25 знімків)

- Контекст → офлайн UI_v2 (http://127.0.0.1:8083), дані беруться з активного snapshot у Redis (ключ налаштовано поза harness).

- Команда (факт запуску) →

```text
python tools/levels_baseline_harness.py --base-url http://127.0.0.1:8083 --symbol XAUUSD --samples 25 --interval-sec 0.7 --strict-3-3-selected-caps --strict-3-3d-freeze-on-close
```

- Результат →
 	- OK: baseline збережено в `reports/levels_baseline/20251227_122316_XAUUSD`.
 	- `validation_issues.md` не створено → strict-гейти 3.3c caps + 3.3d freeze-on-close пройдено.

---

## 2025-12-27 — Зведення артефактів Levels-V1 (ключові папки)

- 3.3b PASS (strict merge, 25 snapshots) → `reports/levels_baseline/20251227_091128_XAUUSD`
 	- Дивитись: `baseline_summary.md` (інваріанти/хеші), `baseline.json` (повні snapshots).

- 3.3c FAIL (stale snapshot, caps violations) →
 	- `reports/levels_baseline/20251227_092117_XAUUSD/validation_issues.md`
 	- `reports/levels_baseline/20251227_092227_XAUUSD/validation_issues.md`
 	- Дивитись: конкретні повідомлення `TF=... lines=... > cap`.

- 3.3c PASS (після republish snapshot, caps OK) → `reports/levels_baseline/20251227_092413_XAUUSD`
 	- Дивитись: `baseline_summary.md` + відсутність `validation_issues.md`.

- 3.3d PASS (strict caps + strict freeze-on-close, 25 snapshots) → `reports/levels_baseline/20251227_122316_XAUUSD`
 	- Файли: `baseline.json`, `baseline_summary.md`.
 	- Очікування: у summary/перевірках немає `validation_issues.md`.

- 3.3e (візуальні контрольні кадри selected_5m) → `reports/levels_selected_frames/20251227_122201_XAUUSD`
 	- Файли: `selected_5m.json` (1–2 кадри), `selected_summary.md` (reason[] + список items).

## 2025-12-27 13:50:52 → 3.3f: strict-гейт композиції selected (TF=5m) у baseline harness

- Що зроблено →
 	- Додано новий strict-гейт **3.3f** для TF=5m: `--strict-3-3f-selected-composition`.
 	- Валідатор перевіряє композицію `levels_selected_v1` (TF=5m):
  		- caps: `bands<=2`, `lines<=3` (без “other kinds”),
  		- pinned active session pair: якщо у candidates є пара активної сесії (ASH/ASL або LSH/LSL або NYH/NYL) і вона проходить gate → selected має містити обидві,
  		- pinned PDH/PDL: якщо PDH/PDL є у candidates і проходять gate → selected має містити хоча б одну,
  		- slot-specific `reason[]` для ключових типів (SESSION/DAILY/RANGE/EQ) — щоб у payload було прозоро, “чому” рівень взяли.
 	- Активну сесію визначаємо з `payload_ts` за `SMC_SESSION_WINDOWS_UTC` (config).
 	- Gate для “in gate” у strict-валидації: `abs(price-level) <= 2.5 * ATR_5m` (ATR рахується з OHLCV, який harness бере по HTTP).

- Де зроблено →
 	- tools/levels_baseline_harness.py:
  		- додано валідатор `validate_selected_composition_5m_v1(...)` + допоміжні функції парсингу часу/ATR;
  		- додано CLI-прапор `--strict-3-3f-selected-composition` і підключено перевірку на кожному snapshot.

- Причина →
 	- Під 3.3f (slot-композиція selection) потрібен інтеграційний гейт, який гарантує “трейдерський мінімум без шуму”, але з day/session маяками.

- Тести/перевірки →
 	- Статичні перевірки: Pylance не показує помилок у tools/levels_baseline_harness.py після додавання валідатора.
 	- Інтеграційний прогін зі strict 3.3f ще не зафіксовано в логах (очікується наступним кроком).

- Команда (для наступного інтеграційного прогону, 25 snapshots) →

```text
python tools/levels_baseline_harness.py --base-url http://127.0.0.1:8083 --symbol XAUUSD --samples 25 --interval-sec 0.7 --strict-3-3-selected-caps --strict-3-3d-freeze-on-close --strict-3-3f-selected-composition
```

- Ризики/нотатки →
 	- Якщо офлайн UI віддає stale snapshot з Redis (старий payload), strict-гейти можуть давати хибні FAIL/OK → перед прогоном треба контролювати republish snapshot.
 	- Якщо OHLCV endpoint недоступний/порожній, gate `2.5*ATR_5m` не буде обчислений → вимоги pinned session/PDH/PDL “в gate” можуть стати надто м’якими (ризик пропуску регресії).

## 2025-12-27 14:06:18 → 3.3g: Distance-гейти “як у трейдера” (м’які, але детерміновані)

- Що зроблено →
 	- Переведено distance gate з “жорсткого відсікання” у **детерміновану м’яку модель**:
  		- `hard gate`: все, що дуже далеко — відсікаємо (`OUT`),
  		- `soft gate`: проміжна зона (`SOFT`) — кандидат дозволений, але позначається явно.
 	- Для TF=5m:
  		- hard: `<= 2.5 * ATR_5m`
  		- soft: `<= 4.0 * ATR_5m`
 	- Для TF=1h/4h (режим `dr4h_or_atr5m`):
  		- hard: `<= 1.5 * DR_4h` (або fallback `<= 6.0 * ATR_5m`)
  		- soft: `<= 2.0 * DR_4h` (або fallback `<= 8.0 * ATR_5m`)
 	- Вбудовано в 3.3f slot-композицію:
  		- SESSION та DAILY можуть підхоплюватись з `SOFT` (трейдерський контекст),
  		- RANGE та EQ bands лишаються тільки з `hard` (щоб не роздувати шум).
 	- Додано маркери в `reason[]`:
  		- `DISTANCE_SOFT_OK` — якщо рівень узято з soft-зони,
  		- `DISTANCE_PINNED_OVERRIDE` — для PDH/PDL на 1h/4h, якщо вони pinned, але поза hard gate.

- Де зроблено →
 	- UI_v2/viewer_state_builder.py:
  		- оновлено `LEVELS_SELECTED_POLICY_V1` (додано soft множники),
  		- реалізовано bucket-логіку `IN/SOFT/OUT` у `select_levels_for_tf_v1(...)`.
 	- tests/test_ui_v2_viewer_state_builder.py:
  		- додано тест `test_levels_selected_v1_distance_soft_allows_session_pair_3_3g`.

- Тести/перевірки →
 	- pytest (таргетно): `pytest tests/test_ui_v2_viewer_state_builder.py` → PASS.

- Ризики/нотатки →
 	- Це зміна selection-поведінки: у деяких режимах рівні SESSION/DAILY можуть з’являтись “трохи далі”, але це контрольовано через `DISTANCE_SOFT_OK`.
 	- Для інтеграційних прогонів через offline UI все ще критично уникати stale snapshot у Redis (інакше хибні FAIL/OK).

## 2025-12-27 15:02:41 — Levels-V1 / Крок 4.0 (L4): UI cutover на `levels_selected_v1` (SSOT)

- Мета →
 	- Перейти від legacy-відбору рівнів з `liquidity.pools` у фронтенді до “тупого рендера” вже відібраних рівнів з builder (`levels_selected_v1`).

- Що зроблено →
 	- UI_v2 тепер вміє рендерити `viewer_state.levels_selected_v1` як окремий шар:
  		- `kind=line`: dashed сегмент + бейдж на шкалі;
  		- `kind=band`: baseline-бокс + бейдж (центр).
 	- Логіка “що показати як level” у UI обходиться, якщо selected доступний (SSOT = builder).

- Де зроблено →
 	- UI_v2/web_client/chart_adapter.js
  		- додано `setLevelsSelectedV1(levels, renderTf)` + очистку оверлеїв у `clearAll()`.
 	- UI_v2/web_client/app.js
  		- якщо `levels_selected_v1` присутній і не порожній → використовуємо `chart.setLevelsSelectedV1(...)` і вимикаємо legacy pools-лінії;
  		- якщо selected відсутній → fallback на попередній шлях `setLiquidityPools(...)`.

- Ризики/нотатки →
 	- Якщо офлайн UI працює на stale snapshot з Redis або бекенд тимчасово не віддає `levels_selected_v1`, UI залишиться на legacy (це очікуваний safe fallback, щоб не отримати “порожній графік”).

## 2025-12-27 15:09:12 — Levels-V1 / Крок 4.1: One-layer enforcement у UI (тільки `levels_selected_v1`)

- Мета →
 	- UI рендерить рівно один шар рівнів: `viewer_state.levels_selected_v1` (SSOT).
 	- На TF=5m: інваріант `lines<=3`, `bands<=2`, без дублікатів на шкалі.
 	- Для TF=1m: рендеримо selected від 5m (бо 1m selected вимкнений дизайном).

- Що зроблено →
 	- Вибір джерела рівнів робиться один раз у `updateChartFromViewerState(...)`:
  		- якщо `levels_selected_v1` доступний для потрібного TF → обовʼязково чистимо legacy pools і рендеримо тільки selected;
  		- інакше — fallback на legacy.
 	- Додано TF-мапінг `1m → 5m` для selected.
 	- Додано фільтр у chart adapter:
  		- `setLevelsSelectedV1(levels, renderTf)` фільтрує по `owner_tf == renderTf`, робить dedup по `id`, і на 5m застосовує caps.
 	- Додано console-гейт (1 рядок на оновлення):
  		- `levels_selected_v1_rendered: lines=X bands=Y tf=5m`
  		- якщо caps перевищено на 5m — додатковий `console.warn`.

- Де зроблено →
 	- UI_v2/web_client/app.js
 	- UI_v2/web_client/chart_adapter.js

## 2025-12-27 15:12:40 — Levels-V1 / Run: baseline harness зі strict-гейтами (після 4.1)

- Команда (PowerShell) →

```text
python tools/levels_baseline_harness.py --base-url http://127.0.0.1:8083 --symbol XAUUSD --samples 25 --interval-sec 0.7 --strict-3-3-selected-caps --strict-3-3d-freeze-on-close --strict-3-3f-selected-composition
```

- Результат →
 	- OK (exit code 0): strict-гейти `caps + freeze-on-close + selected composition` не впали на 25 snapshot.

- Артефакти →
 	- `reports/levels_baseline/20251227_122316_XAUUSD/baseline_summary.md`
 	- `reports/levels_baseline/20251227_122316_XAUUSD/baseline.json`

- Нотатка →
 	- Окремо перевірити, що `kind=band` (EQH/EQL) на графіку реально видно як box/заливку (top/bot), а не сприймається як ще одна “лінія”.

## 2025-12-27 15:18:10 — Levels-V1 / 4.1: вилучення legacy UI-логіки levels (one-layer truth)

- Що змінено →
 	- Прибрано fallback на legacy-рівні з `liquidity.pools`: UI більше не робить selection у фронтенді.
 	- У `chart_adapter.js` legacy selection/рендер pools-рівнів відключено (safe no-op), щоб не існувало “подвійної правди”.

- Гейт →
 	- Візуально UI має виглядати так само, як після 4.0/4.1 (бо selection вже SSOT у builder).

## 2025-12-27 15:27:55 — UI_v2: фікс «синіх зон на пів екрану» (null→0 у band)

- Симптом →
 	- На перемикачі “пули” з’являлись/зникали 1–2 великі сині заливки на пів екрану.

- Причина →
 	- У фронтенді числа для `levels_selected_v1` нормалізувались через `Number(...)`.
 	- Для `null` це дає `0`, що є finite → band випадково проходив фільтр і малювався як зона від 0 до реальної ціни (гігантський прямокутник).

- Виправлення →
 	- Нормалізація чисел тепер не перетворює `null/undefined` у 0: `top/bot/price` стають `null`, і band рендериться тільки якщо обидва значення реально задані.
 	- Додатково `renderBand` у chart_adapter не приймає `null` як 0.

- Де зроблено →
 	- UI_v2/web_client/app.js
 	- UI_v2/web_client/chart_adapter.js

## 2025-12-27 15:33:40 — UI_v2: `levels_selected_v1` band тепер без заливки (2 лінії замість box)

- Контекст →
 	- Навіть з коректними `top/bot` band у вигляді BaselineSeries виглядає як “синя зона на пів екрану” і перекриває свічки.

- Зміна →
 	- `kind=band` для `levels_selected_v1` більше не рендериться як синя заливка/box.
 	- Замість цього малюються 2 dashed лінії: нижня межа (`... L`) і верхня межа (`... H`).

- Де зроблено →
 	- UI_v2/web_client/chart_adapter.js

- Очікуваний результат →
 	- Перемикач шару (історично “пули”) більше не створює великих синіх прямокутників; рівень band лишається читабельним без перекриття графіка.
