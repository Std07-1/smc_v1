# UPDATE.md

Журнал змін (оновлюється **кожного разу**, коли я роблю будь-які правки у репозиторії).

## Формат запису (конвенція)

Кожен запис має містити:

- **Дата/час** (локально) + коротка назва зміни.
- **Що змінено**: 3–10 пунктів по суті.
- **Де**: ключові файли/модулі.
- **Тести/перевірка**: що саме запускалось і результат.
- **Примітки/ризики** (за потреби): що може вплинути на рантайм.

---

## 2025-12-16 — UI_v2 (Web): декластеризація рівнів + стабільні BOS/CHOCH

**Що змінено**

- Виправлено `safeUnixSeconds()` для ISO-рядків (через `Date.parse()`), щоб події/діапазони не зникали через `NaN` у time.
- Для liquidity pools у фронтенді підхоплено `strength` та `touches` (якщо є у payload), без змін бекенду.
- BOS/CHOCH: маркери стали детермінованими (case-insensitive), position/shape залежать від `direction`, CHOCH не плутається з BOS.
- BOS/CHOCH: додано snap часу події до найближчої існуючої свічки (із відсіканням, якщо занадто далеко від барів).
- BOS/CHOCH: вимкнено рендер «трикутників» (overlay), залишено лише текстові markers над свічкою.
- BOS: маркери уніфіковано в синій колір (щоб не виглядали як «червоні квадратики» біля тексту).
- Виправлено «самоплив» графіка вправо: при `setBars()` viewport зберігається, якщо користувач не знаходиться на правому краї (follow).
- Додано декластеризацію liquidity pools/zones перед рендером:
  - pools: дедуп близьких рівнів, ліміт локальних ліній (≤6), 2 ключові рівні з axisLabel, «глобальні» рівні лише як axisLabel.
  - zones: фільтр по вікну фокусу, ліміт ≤3, тонкі зони рендеряться як один рівень.

**Де**

- UI_v2/web_client/app.js
- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_fxcm_ws_server.py tests/test_ui_v2_static_http.py` → `11 passed`.

---

## 2025-12-16 — UI_v2 (Web): палітри для «A по даних» зон (NY/Tokyo/London)

**Що змінено**

- Додано палітри для data-driven high/low box («A по даних») залежно від активної сесії:
  - New York — зелений
  - Tokyo — синій
  - London — оранжевий
- Колір застосовується як **заливка** (без ліній) через BaselineSeries options.
- Виправлено autoscale для high/low box: тепер правий price scale враховує `low/high`, щоб зона не виглядала «напівзаповненою».

**Де**

- UI_v2/web_client/app.js
- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_fxcm_ws_server.py tests/test_ui_v2_static_http.py` → `11 passed`.

---

## 2025-12-16 — UI_v2 (Web): сесії (UTC) як блоки + персист шарів + Baseline для range box

**Що змінено**

- Додано шар **«Сесії (UTC)»** (Asia/London/New York) з перемикачем у меню «Шари» та в мобільному drawer.
- Перероблено рендер сесій: замість «полос» (histogram по кожному бару) малюємо **суцільні блоки** на окремій шкалі 0..1 (не залежить від ціни інструмента).
- Додано “A по даних” для поточної сесії: UI бере `high/low` з `fxcm:status.session.symbols[]` (per Symbol/TF) і малює **бокс між low↔high** (без ліній) через BaselineSeries.
- Додано WS endpoint `/fxcm/status` у FXCM WS-міст, щоб web UI міг отримувати `fxcm:status` у public/same-origin режимі.
- Виправлено “box” для діапазонів у структурі: `setRanges()` переведено з 2×AreaSeries на **BaselineSeries**, щоб зона була між `min↔max`, а не «до нуля».
- Виправлено керування шаром сесій: `setSessionsEnabled()` експортується з chartController та застосовується одразу після ініціалізації графіка.
- Додано персист `layersVisibility` у `localStorage`, щоб перемикачі шарів (включно з сесіями) **не збивались після рестарту/рефреша**.

**Де**

- UI_v2/web_client/index.html
- UI_v2/web_client/styles.css
- UI_v2/web_client/app.js
- UI_v2/web_client/chart_adapter.js
- UI_v2/fxcm_ohlcv_ws_server.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py tests/test_ui_v2_fxcm_ws_server.py` → `11 passed`.

---

## 2025-12-16 — UI_v2 (Web): «A по даних» без ліній + один шар сесій + фікс vertical-pan

**Що змінено**

- Для «A по даних» (high/low box) часові межі сесії знову рахуються **по фіксованому UTC-розкладу** (Asia/London/NY), а не з `fxcm:status.current_open_utc/current_close_utc`.
- Прибрано накладання «двох версій» сесій: старий кольоровий фон Asia/London/NY вимкнено; лишився лише data-driven high/low box під тим самим перемикачем.
- Прибрано горизонтальні лінії у high/low box: вимкнено baseline/series lines (`baseLineVisible=false`, `lineVisible=false`, прозорі line colors як страховка).
- Виправлено проблему «стеля/підлога» при вертикальному drag по графіку: синхронізовано `autoscaleInfoProvider` для `candles/liveCandles/sessionRangeBox`, щоб manual range не “склеювався” з автоскейлом інших серій.

**Де**

- UI_v2/web_client/app.js
- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py tests/test_ui_v2_fxcm_ws_server.py` → `11 passed`.

---

## 2025-12-13 — S2/S3: `fxcm:commands` + стабільний payload + тести requester-а

**Що змінено**

- Зафіксовано дефолтний канал команд для FXCM-конектора: `fxcm:commands` (без fallback на `ai_one:admin:commands`).
- Уніфіковано S2-логіку в pure-функцію `classify_history()` (insufficient/stale_tail) та вирівняно ключ `last_open_time_ms`.
- Оновлено S3 requester: стабільна JSON-схема команди з блоками `s2{...}` та `fxcm_status{...}`, INFO-лог у заданому форматі.
- Додано/закріплено reset “active issue”: при переході history_state в `ok` requester очищає rate-limit,
  щоб при наступному погіршенні можна було одразу знову відправити команду.
- Додано мінімальну документацію контракту S2/S3.

**Де**

- config/config.py
- app/fxcm_history_state.py
- app/fxcm_warmup_requester.py
- app/smc_producer.py
- tests/test_s2_history_state.py
- tests/test_s3_warmup_requester.py
- docs/uds_smc_update_2025-12-13.md

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_s2_history_state.py tests/test_s3_warmup_requester.py` → `8 passed`.

---

## 2025-12-14 — Public Viewer (UI_v2) на ПК: same-origin фронт + Docker nginx allowlist + tunnel

**Що змінено**

- У UI_v2 фронтенді прибрано жорсткі дефолти `127.0.0.1:8080/8081/8082`: тепер HTTP працює через `window.location.origin`, WS — через `ws://|wss://` + `window.location.host` (same-origin).
- FXCM dev WS міст (8082) вимкнений за замовчуванням у публічному режимі, щоб не було нескінченних reconnect’ів; дозволяється лише на `localhost/127.0.0.1` або з явним `?fxcm_ws=1`.
- Додано периметр для публічного доступу без VPS: `deploy/viewer_public/` (Docker Compose) з `nginx` allowlist + rate-limit та `cloudflared` tunnel.
- У nginx allowlist додано статику за розширеннями (js/css/…); API/WS прокситься лише по потрібних маршрутах; усе інше → 404.
- Для WS proxy додано `proxy_read_timeout`/`proxy_send_timeout` 3600s; також приховується `Access-Control-Allow-Origin` з бекенду (same-origin).
- Додано короткий Troubleshooting у runbook (найчастіші фейли: `0.0.0.0`, статика allowlist, WS upgrade, token).
- Виправлено nginx конфіг на формат `conf.d/default.conf` (замість main `nginx.conf`), щоб уникнути restart-loop контейнера.
- Переведено `cloudflared` на Cloudflare Quick Tunnel без домену/токена (публічний URL `https://*.trycloudflare.com` береться з логів).
- Уточнено `UI_v2/web_client/README.md`: FXCM WS міст (8082) — локальний dev-інтерфейс і не має використовуватись у публічному режимі.

**Де**

- UI_v2/web_client/app.js
- UI_v2/web_client/README.md
- deploy/viewer_public/docker-compose.yml
- deploy/viewer_public/nginx.conf
- deploy/viewer_public/.env.template
- deploy/viewer_public/README.md

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py`.

---

## 2025-12-14 — Документація: синхронізація FXCM контрактів (channels/payload/HMAC/commands)

**Що змінено**

- Уточнено контракт `fxcm:ohlcv`: додано `source` (опційно), описано `complete/synthetic` як опційні бар-поля та правило: UDS зберігає лише complete.
- Зафіксовано правило HMAC: `sig` рахується/перевіряється лише по `{"symbol","tf","bars"}` (root-поля на кшталт `source` не входять у підпис).
- Додано/уточнено огляд каналів `fxcm:status`, `fxcm:price_tik` та `fxcm:commands` (включно з `fxcm_set_universe` як частиною контракту конектора).
- Прибрано двозначність щодо cadence `fxcm:price_tik`: це cadence конектора, а не «таймер оновлення UI».

---

## 2025-12-14 — UI_v2 (Web): десктоп-полірування статусів/графіка

**Що змінено**

- Розділено «транспортний» статус (WS) та стан ринку FX (`market_state`) у два окремі pill-и, щоб уникнути суперечливих повідомлень.
- Прибрано подвійні рамки в зоні графіка: контейнер графіка більше не малює внутрішній бордер.
- Прибрано зайві відступи у non-fullscreen: `card-chart` без padding, щоб графік займав максимум площі в межах єдиної рамки.
- Прибрано «0» бейдж на шкалі обʼєму: `lastValueVisible/priceLineVisible` вимкнені для histogram series.
- Зменшено правий «порожній» відступ у time scale: `rightOffset=0`; `fitContent()` виконується лише один раз на новий датасет.
- Виправлено ситуацію, коли поточна ціна показувалась як `-`: додано fallback на close останньої complete-свічки.
- Додано hover-підказку по свічці (ціна close + обсяг) з затримкою ~1с, щоб дивитись обсяги без шуму на осях.
- Повернуто очікувану поведінку для поля «Ціна»: якщо ціни в payload немає, показуємо порожньо (а не `-`).

---

## 2025-12-15 — SMC pipeline: узгодження FX market_state з ticks_alive

**Що змінено**

- Якщо `fxcm:status` дає суперечливу комбінацію `market=closed` + `price_state=ok` (ticks alive), SMC більше не переходить у `IDLE fxcm_market_closed` при свіжому статусі.
- У console status bar у такій ситуації показуємо `market=open`, щоб не вводити в оману (за умови, що конектор не `down`).

**Де**

- app/smc_producer.py
- app/console_status_bar.py
- tests/test_app_smc_producer_fxcm_idle.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_app_smc_producer_fxcm_idle.py`.

---

## 2025-12-15 — SMC: `ohlcv!=ok` не блокує цикл (live price працює при delayed/lag)

**Що змінено**

- SMC idle-gate більше не блокує цикл при `market=open` + `price=ok`, навіть якщо `ohlcv=delayed/lag/down`.
- `ohlcv` у `fxcm:status` трактуємо як діагностику: фіксуємо причину як `fxcm_ohlcv_<state>_ignored`, але продовжуємо цикл, щоб оновлювати `current_price` з `fxcm:price_tik`.

**Де**

- app/smc_producer.py
- tests/test_app_smc_producer_fxcm_idle.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_app_smc_producer_fxcm_idle.py` → `4 passed`.

---

## 2025-12-15 — Legacy viewer (UI_V2_ENABLED=0): live price з `fxcm:price_tik`

**Що змінено**

- Experimental viewer (SMC Viewer · Extended) тепер додатково підписується на `fxcm:price_tik` і оновлює `Price` між SMC снапшотами.
- Для тикових апдейтів використовуємо останній збережений SMC asset/meta і лише підміняємо поле `viewer_state.price` на `mid` з тика.

**Де**

- UI/ui_consumer_experimental_entry.py
- tests/test_ui_consumer_entry.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_consumer_entry.py` → `4 passed`.

---

## 2025-12-15 — UI_v2: web-only стек + окремий перемикач debug viewer

**Що змінено**

- `UI_V2_ENABLED` тепер керує лише UI_v2 web-стеком (HTTP/WS) і не використовується як «перемикач типів viewer».
- Прибрано автозапуск `UI_v2.debug_viewer_v2` з пайплайна (UI_v2 стає чисто веб-шаром).
- Додано окремий ENV-прапорець `DEBUG_VIEWER_ENABLED=1|0` для запуску console viewer `SMC Viewer · Extended` незалежно від `UI_V2_ENABLED`.

**Де**

- app/main.py
- tests/test_app_main_ui_toggle.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_app_main_ui_toggle.py` → `2 passed`.

---

## 2025-12-15 — UI_v2: прибирання debug/rich артефактів (prod cleanup)

**Що змінено**

- Прибрано з `UI_v2` консольні/дев-модулі: `debug_viewer_v2.py`, `rich_viewer.py`, `rich_viewer_extended.py`.
- Видалено застарілі конфіг-поля `UI_V2_DEBUG_VIEWER_ENABLED` та `UI_V2_DEBUG_VIEWER_SYMBOLS`.
- Видалено тести, що були привʼязані до rich/debug viewer.
- Оновлено документацію `UI_v2` під web-only роль.

**Де**

- UI_v2/**init**.py
- UI_v2/README.md
- config/config.py
- tests/test_ui_v2_debug_viewer_v2.py (видалено)
- tests/test_ui_v2_rich_viewer_extended.py (видалено)

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_app_main_ui_toggle.py tests/test_ui_v2_viewer_state_builder.py tests/test_ui_v2_viewer_state_server.py tests/test_ui_v2_viewer_state_ws_server.py tests/test_ui_v2_smc_viewer_broadcaster.py tests/test_ui_v2_smc_viewer_broadcaster_metrics.py tests/test_ui_v2_static_http.py tests/test_ui_v2_fxcm_ws_server.py tests/test_ui_v2_ohlcv_provider.py` → `22 passed`.

---

## 2025-12-15 — UI_v2 (Web): realtime `complete=false` свічки + лаг по live freshness + флаг `fxcm_apply_complete`

**Що змінено**

- У web UI “Лаг (с)” тепер показує **свіжість live-стріму** (ticks/OHLCV), якщо FXCM WS увімкнено й live події приходять; інакше — fallback на `meta.fxcm.lag_seconds`.
- Додано флаг `fxcm_apply_complete=1|0` (query param) для керування тим, чи треба **одразу** прибирати live overlay при приході `complete=true`.
- Тиковий WS також вважаємо “live” (впливає на live-індикатор і лаг), щоб не зависати у `LIVE: OFF`, якщо OHLCV live тимчасово тихий.

**Де**

- UI_v2/web_client/app.js
- UI_v2/web_client/README.md

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py`.

---

## 2025-12-15 — UI_v2 (Web): FXCM OHLCV WS сумісність `timeframe` + volume від `tick_count`

**Що змінено**

- FXCM WS міст для `/fxcm/ohlcv` тепер приймає `timeframe` як синонім `tf`, щоб не “губити” повідомлення з Redis `fxcm:ohlcv`, якщо конектор шле іншу назву поля.
- У web UI для live OHLCV обсяг/інтенсивність беремо з `volume`, а якщо його немає — з `tick_count` (fallback), щоб гістограма обсягів реально малювалась.

**Де**

- UI_v2/fxcm_ohlcv_ws_server.py
- UI_v2/web_client/app.js

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py`.

---

## 2025-12-15 — UI_v2 (Web): діагностика WS close (code/reason) + стійкість до Redis hiccups

**Що змінено**

- У браузері (app.js) лог для `WS onclose` тепер показує `code/wasClean/reason`, щоб швидко відрізняти 1011 (internal) від handshake/мережевих розривів.
- WS сервер `ViewerStateWsServer` став більш стійким до тимчасових винятків Redis/pubsub: не валимо весь handler 1011 при разовому `get_message()`/send фейлі.

**Де**

- UI_v2/web_client/app.js
- UI_v2/viewer_state_ws_server.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py`.

---

## 2025-12-15 — UI_v2 (Web): /favicon.ico без 404 (No Content)

**Що змінено**

- HTTP сервер UI_v2 тепер відповідає `204 No Content` на `GET /favicon.ico`, щоб браузер не засмічував консоль 404-ками в публічному режимі.

**Де**

- UI_v2/viewer_state_server.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py`.

---

## 2025-12-15 — UI_v2 (Web): live price від ticks + volume fallback при `volume=0`

**Що змінено**

- Виявлено реальний кейс FXCM: у `fxcm:ohlcv` live-бар може мати `volume=0.0` і водночас `tick_count>0`.
  У UI тепер беремо **перше додатне** значення серед `volume/tick_count/...`, щоб гістограма обсягів не була завжди нульова.
- Додано додатковий fallback: якщо FXCM live-бар не містить volume/tick_count, UI накопичує локальний `tick_count` з тикового WS і підставляє його як інтенсивність (щоб не було миготіння і щоб volume було на 5m).
- Стабілізовано видимість volume-гістограми при масштабуванні: для histogram більше не використовується volume-залежна прозорість (бруски не «провалюються» в майже невидимі).
- Стабілізовано volume при горизонтальному скролі: autoscale volume-шкали тепер фіксується по глобальному max обсягу (не по видимому фрагменту).
- Уточнено autoscale volume: max для шкали береться по всьому датасету з robust-кепом по квантилю (p98), щоб одиночні спайки не сплющували решту обсягів.
- Ціна у summary/мобільному UI тепер оновлюється від тикового WS (`/fxcm/ticks`) і вважається “свіжою” до `FXCM_LIVE_STALE_MS`.
- Live overlay (candles) тепер показує live price на шкалі/лейблі синхронно зі свічкою (а не лише по закритій свічці).
- Тимчасово додано поле у шапку summary: `VOL src` (показує `tick_count` або `volume`) для швидкої діагностики.

**Де**

- UI_v2/web_client/app.js
- UI_v2/web_client/chart_adapter.js

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_ui_v2_static_http.py`.

---

## 2025-12-15 — SMC: толерантність `stale_tail` у вихідні + requester/порти UI_v2

**Що змінено**

- Додано helper `_history_ok_for_compute(..., allow_stale_tail)` і дозволено `stale_tail` як OK лише коли фід деградований: `market!=open` або `ohlcv_state in {delayed, down}`; додано прапорець `meta.s2_stale_tail_expected`.
- У S3 requester: для `stale_tail` на `1m` відправляємо `fxcm_warmup` (а не `fxcm_backfill`), щоб не слати команду, яку конектор може не підтримувати.
- У `app.main`: якщо порти UI_v2 (HTTP/WS/FXCM WS) зайняті, пайплайн більше не завершується — логуються попередження і процес продовжує працювати.
- Додано утиліту діагностики `tools.debug_fxcm_channels` (NUMSUB + лічильники повідомлень за заданий інтервал).
- UI_v2: виправлено побудову WS base URL для dev-режиму (HTTP на :8080 → WS на :8081), додано fallback для `file://`, та підтримку відкриття UI через приватну LAN IP (RFC1918) без вимкнення FXCM dev WS.
- FXCM інжестор: `fxcm:status.ohlcv=down` більше не блокує запис; якщо конектор надсилає лише `complete=false`, інжестор фіналізує попередній live-бар при появі нового `open_time` і пише його в UDS (щоб UI мав історію свічок).

**Де**

- app/smc_producer.py
- app/fxcm_warmup_requester.py
- app/main.py
- config/config.py
- tools/debug_fxcm_channels.py
- data/fxcm_ingestor.py
- UI_v2/web_client/app.js
- tests/test_app_smc_producer_history_gate.py
- tests/test_s3_warmup_requester.py
- tests/test_fxcm_schema_and_ingestor_contract.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_app_smc_producer_history_gate.py tests/test_s3_warmup_requester.py tests/test_app_console_status_bar.py tests/test_app_smc_producer_fxcm_idle.py` → `17 passed`.
- Запущено таргетно: `pytest tests/test_fxcm_schema_and_ingestor_contract.py tests/test_ingestor.py tests/test_fxcm_ingestor_universe_filter.py` → `21 passed`.

---

## 2025-12-15 — FXCM контракт: тести complete/synthetic + HMAC extra fields + gap-check (--hours)

**Що змінено**

- Уточнено/розширено контрактні тести FXCM інжестора: live-бар (`complete=false`) не пишеться в UDS; synthetic з `complete=true` пишеться.
- Додано тест на forward-compatibility підпису: HMAC лишається валідним при появі додаткових/невідомих полів усередині `bars[*]`.
- QA gap-check: після звірки репозиторію виявлено, що вже існує універсальна утиліта `tools/uds_ohlcv_gap_check.py` (UDS + режим `--snapshot-file`).
  Щоб не дублювати функціонал, додано зручний режим `--hours` (останні N годин від кінця історії) саме в існуючу утиліту.
  Дублюючий `tools/qa_check_1m_gaps.py` прибрано.

**Де**

- tests/test_fxcm_schema_and_ingestor_contract.py
- tests/test_ingestor.py
- tools/uds_ohlcv_gap_check.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_fxcm_schema_and_ingestor_contract.py tests/test_ingestor.py` → `21 passed`.

---

## 2025-12-15 — SMC: прибрано S2-блокування (always-on) + S3 requester просить останні 300 барів

**Що змінено**

- Прибрано жорсткий S2-gate в `smc_producer`: цикл більше не робить early-continue з `cycle_reason=smc_insufficient_data` через `insufficient/stale_tail`.
- `process_smc_batch` переведено в деградований режим: якщо OHLCV немає/замало,
  все одно публікуємо `current_price` з тика (`price_stream`) і прозорий `signal`
  (`SMC_NO_OHLCV` / `SMC_WARMUP`), щоб UI не був порожнім.
- Вирівняно логіку lookback: `smc_producer` тримає `min_bars/target_bars` у межах
  `SMC_RUNTIME_PARAMS.limit` (типово 300) і не «висить» на великих `contract_min_bars`.
- S3 requester більше не намагається витягувати великі обʼєми історії по контракту:
  тепер для команд warmup/backfill просить «останні N барів» (N береться з
  `SMC_RUNTIME_PARAMS.limit`, дефолт 300) і додає поле `lookback_bars`
  (залишено `lookback_minutes` для сумісності).
- Оновлено тести S3 requester під нову семантику (300 барів).

**Де**

- app/smc_producer.py
- app/fxcm_warmup_requester.py
- tests/test_s3_warmup_requester.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_s3_warmup_requester.py tests/test_s2_history_state.py` → `8 passed`.

**Примітки/ризики**

- Це змінює поведінку “готовності”: тепер SMC працює always-on і може публікувати
  деградовані стани без OHLCV. Для повних SMC hints все одно потрібна історія
  (її має забезпечити конектор/UDS).

---

## 2025-12-15 — Логи/консоль: прибрано RichHandler + вимкнено status bar

**Що змінено**

- Прибрано Rich-based логування (`RichHandler`) у ключових модулях (Data/UI/SMC core helpers) — залишились прості стандартні логи через `logging.StreamHandler()`.
- Rich Live console status bar прибрано: `run_console_status_bar()` тепер no-op; при цьому `build_status_snapshot()` залишено для тестів і можливих інтеграцій.
- `app/rich_console.py` більше не тягне `rich` і лишається lightweight shim для сумісності старих імпортів.

**Де**

- app/console_status_bar.py
- app/rich_console.py
- data/fxcm_ingestor.py
- data/fxcm_price_stream.py
- data/unified_store.py
- UI/publish_smc_state.py
- UI/ui_consumer_experimental_entry.py
- smc_structure/event_history.py
- smc_zones/breaker_detector.py
- smc_zones/fvg_detector.py
- smc_zones/orderblock_detector.py
- utils/utils.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_app_console_status_bar.py tests/test_utils_rich_console.py tests/test_ingestor.py` → `16 passed`.

---

## 2025-12-14 — Документація: UI_v2 + FXCM (live-bar, volume, джерело істини)

**Що змінено**

- Зафіксовано нюанс UI_v2: volume-серія є, але в основному UI-шляху live-бар з FXCM WS будується без `volume`, тому live-volume може бути нульовим; dev стенд (`chart_demo.js`) передає `volume`.
- Додано явні посилання на “джерело істини” контрактів у цьому репо: `data/fxcm_schema.py` + `tests/test_fxcm_schema_and_ingestor_contract.py`.
- Додано посилання в кореневий README на `docs/fxcm_contract_audit.md`, щоб не перечитувати код під час звірки інтеграції.

**Де**

- UI_v2/web_client/README.md
- docs/fxcm_tick_agg_update_2025-12-13.md
- docs/fxcm_contract_audit.md
- docs/fxcm_integration.md
- README.md

**Тести/перевірка**

- Не запускалось (зміни лише в документації).

---

## 2025-12-14 — Документація: актуалізація `stage1_pipeline.md` під SMC-only runtime

**Що змінено**

- Переписано `docs/stage1_pipeline.md` як довідник реального `app.main` пайплайна (SMC-only): прибрано застарілий Stage1 моніторинг (`AssetMonitorStage1`, `screening_producer`) та `_await_fxcm_history()`.
- Додано посилання на джерело істини FXCM-контрактів у цьому репо: `data/fxcm_schema.py` + `tests/test_fxcm_schema_and_ingestor_contract.py`.
- Оновлено діагностику: актуальні log-теги та канали (`fxcm:*`, `ui.metrics`).

**Де**

- docs/stage1_pipeline.md

**Тести/перевірка**

- Не запускалось (зміни лише в документації).

---

## 2025-12-14 — UI_v2: live-volume для FXCM live-барів + опційний same-origin WS

**Що змінено**

- `UI_v2/web_client/app.js`: у `handleOhlcvWsPayload()` live-бар тепер прокидає `volume` у `setLiveBar(...)`, щоб live-volume histogram міг малюватися, якщо `bar.volume` присутній у повідомленні.
- `UI_v2/web_client/app.js`: додано прапор `?fxcm_ws_same_origin=1` для підключення до FXCM WS у same-origin (коли `/fxcm/*` прокситься через nginx), замість жорсткого `:8082`.
- `UI_v2/web_client/app.js`: додано легкий індикатор `LIVE: ON/OFF` (ON якщо бачили `complete=false` за останні ~5s).
- Документація: уточнено runbook і описано мінімальний шлях доставки live-барів у прод-режимі через reverse-proxy.
- Додано окремий runbook: `docs/runbook_tradingview_like_live_public_domain.md`.

**Де**

- UI_v2/web_client/app.js
- UI_v2/web_client/README.md
- deploy/viewer_public/nginx.conf
- deploy/viewer_public/README.md

**Тести/перевірка**

- Не запускалось (JS/UI зміни + документація).

---

## 2025-12-14 — UI_v2: мобільний 2-екранний режим (Overview/Chart) без reconnect

**Що змінено**

- Додано mobile-first UX: 2 екрани **Overview** та **Chart** з нижньою навігацією (bottom-nav) і drawer “Фільтри”.
- Головна вимога збережена: **один** WS/HTTP пайплайн і **один** чарт — перемикання екранів робиться лише через show/hide + перенос існуючого `.card-chart` між слотами та `scheduleChartResize()`.
- У шапці Overview додано компактні поля (symbol/price/Δ%) та дубль індикатора WS-статусу.
- В Overview додано компактний список останніх BOS/CHOCH (до 5) без нових джерел даних (береться з поточного viewer_state).
- Drawer синхронізує шари (events/pools/ote/zones) і таймфрейм (1m/5m) з існуючими desktop-контролами.
- У Chart-екрані на мобілці приховано важкі таблиці/панелі для максимально "чистого" графіка (керування — через drawer).
- У Chart-екрані на мобілці прибрано рамку/фон контейнера графіка (мінімалістичний вигляд).
- У desktop-шапці зроблено компактніший блок керування ("Символ/Таймфрейм") і прибрано кнопки "Оновити snapshot" та "Перепідключити".
- У desktop-шапці відформатовано `payload ts` у зрозумілий локальний формат (DD.MM HH:MM:SS).
- Summary зроблено компактнішим; зменшено відступи/проміжки між основними блоками (щільніше компонування аж до «майже впритул»).
- Зменшено відступ між desktop-шапкою та Summary (щільність як між блоками).
- Summary додатково ущільнено приблизно до ~50% від попереднього розміру (padding/gap/типографіка/бейджі).
- У Summary прибрано заголовок (без "Коротко"), лейбли залишено короткими українськими.
- У блоці Price Chart фільтри шарів (BOS/CHOCH, Pools, OTE, Zones) сховано під кнопку-стрілочку прямо на графіку (верхній кут).
- У блоці Price Chart контроль "Висота" перенесено на графік: тонкий вертикальний слайдер зліва без підписів.
- У блоці графіка прибрано заголовок "Price Chart", щоб звільнити місце під полотно.
- Додано іконку fullscreen поруч із кнопкою шарів; стандартизовано розміри оверлей-іконок:
  кнопки 32×32, іконки 16×16.
- Блок "OHLCV Debug" приховано (не займає місце), бо він більше не потрібен користувачу.
- Панелі Structure Events / OTE Zones / Liquidity Pools / Zones: контент більше не вилазить за межі картки;
  таблиці та заголовки зроблено компактнішими (A/B/C типографіка).
- Прибрано візуальне дублювання «двох паличок» біля контролу висоти: лівий бордер контейнера графіка сховано.
- Виправлено відображення контролу висоти: замість «подвійного» вертикального range у деяких браузерах використано стабільний rotate-варіант.
- Підсилено видимість вертикального слайдера висоти (контраст треку/повзунка + легка підкладка/hover), щоб було зрозуміло що це контроль.
- Вертикальний слайдер висоти: зроблено трохи сірішим і зміщено ближче до низу (прив'язка по `bottom`, без виходу за рамку).
- Вертикальний слайдер висоти: піднято трохи вище та прибрано фон/рамку контейнера (прозорий фон; видно лише шкалу й бігунок).
- UI_v2 (chart): додано невеликий нижній padding контейнера графіка, щоб не обрізалась нижня time scale.
- UI_v2 (fullscreen): виправлено «пливе графік» через лейаут — у fullscreen повністю приховано контроль висоти
  і дозволено контейнеру чарта рости в flex (через flex-обгортку), щоб не було обрізання/дрейфу.
- UI_v2 (fullscreen/desktop): режим `.card-chart--fullscreen` зроблено edge-to-edge: `inset:0`, без рамок/паддінгів/box-shadow,
  прибрано подвійну рамку (border/radius) у внутрішньому контейнері графіка.
- UI_v2 (mobile): переведено макет на flex-колонку (шапка → чарт (flex:1) → bottom-nav), щоб чарт реально займав екран
  і не було великої «порожнечі» під ним; зменшено висоту mobile header та bottom-nav.
- UI_v2 (mobile): висоту для евристики `--mobile-chart-height` беремо з `visualViewport.height` (fallback `innerHeight`),
  щоб на Android адресний рядок менше ламав розрахунки.
- UI_v2 (mobile/chart): прибрано «пливе вниз» — `#chart-slot` зроблено flex:1, бо `.card-chart` переноситься всередину слота;
  також прибрано `transition: height` у chart-контейнері на мобілці.
- UI_v2 (mobile/chart): зафіксовано канонічний фікс «пливе вниз» через `visualViewport` → `--app-vh` та px-висоту
  `--mobile-chart-height` (підписки на `visualViewport.resize/scroll`), щоб прибрати дрейф при зміні адресного рядка/toolbar.
- Підкручено тему графіка (фон/сітка/шкали/кросхейр/кольори свічок і volume) у бік «TV-like», без буквального 1:1 копіювання.

**Де**

- UI_v2/web_client/index.html
- UI_v2/web_client/styles.css
- UI_v2/web_client/app.js

**Тести/перевірка**

- Не запускалось (UI зміни). Ручна перевірка: відкриття `/`, перемикання Overview↔Chart без перепідключень та з коректним resize чарта.

---

## 2025-12-13 — Rich status bar: S2/S3 поля + індикатор конектора (conn)

**Що змінено**

- У Rich status bar додано рядок `conn`: показує свіжість `fxcm:status` (age) та стан `ok/lag/down` з підсвіткою.
- Додано рядок `s2`: лічильники проблем історії (insufficient/stale_tail/unknown) + активний символ/стан ("поточна тема").
- Додано рядок `s3`: індикатор requester-а (on/off), канал, лічильники, та остання відправлена команда (type/symbol/tf/reason/age).
- SMC producer тепер кладе S2 summary у `meta`, щоб status bar показував це навіть коли SMC у WARMUP/IDLE.

**Де**

- app/console_status_bar.py
- app/fxcm_warmup_requester.py
- app/smc_producer.py
- tests/test_app_console_status_bar.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_app_console_status_bar.py` → `5 passed`.

## 2025-12-13 — Shared Rich Console + FXCM WS bridge (UI_v2) + розширення тестів

**Що змінено**

- Уніфіковано Rich Console: замінили локальні `Console(stderr=True)` на спільний singleton (`get_rich_console()`),
  щоб прибрати артефакти Rich Live/логів у PowerShell/VS Code.
- Додано shim `app/rich_console.py` для сумісності імпортів (канонічний console в `utils/rich_console.py`).
- Додано WS-проксі для FXCM у UI_v2: трансляція `fxcm:ohlcv` та `fxcm:price_tik` у браузер (`/fxcm/ohlcv`, `/fxcm/ticks`).
- Посилено/уточнено юніт-тести: WS query parsing, роздача статики (content-type + path traversal),
  стабільність publish при короткій недоступності Redis, точність тестів логування/пайплайн-мети.

**Де**

- utils/rich_console.py
- app/rich_console.py
- data/unified_store.py
- data/fxcm_price_stream.py
- smc_structure/event_history.py
- smc_zones/breaker_detector.py
- smc_zones/fvg_detector.py
- smc_zones/orderblock_detector.py
- UI_v2/fxcm_ohlcv_ws_server.py
- tests/test_utils_rich_console.py
- tests/test_ui_v2_fxcm_ws_server.py
- tests/test_ui_v2_static_http.py
- tests/test_publish_smc_state.py
- tests/test_app_main_universe_fast_symbols.py
- tests/test_app_smc_producer_pipeline_meta.py

**Тести/перевірка**

- Запущено таргетно:
  `pytest tests/test_utils_rich_console.py tests/test_ui_v2_fxcm_ws_server.py tests/test_ui_v2_static_http.py`
  `tests/test_publish_smc_state.py tests/test_app_main_universe_fast_symbols.py tests/test_app_smc_producer_pipeline_meta.py`
  → `19 passed`.

---

## 2025-12-13 — IDLE режим SMC по `fxcm:status` ("система чекає/спить, але статус видно")

**Що змінено**

- Додано політику "IDLE" для SMC-циклу: коли ринок закритий або фід деградований, важкі обчислення SMC пропускаються.
- При IDLE система **залишається живою** й продовжує публікувати стан/метадані (щоб UI/оператор бачив статус), а цикл робить `sleep`.
- Додано причини (reason) для прозорості: окремо для `market=closed`, `price!=ok`, `ohlcv!=ok`, а також "ok".

**Де**

- app/smc_producer.py

**Тести/перевірка**

- Запущено таргетні тести пайплайн-метаданих/локальної логіки SMC producer: `11 passed` (файл(и): `tests/test_app_smc_producer_pipeline_meta.py`, `tests/test_app_smc_producer_pipeline_local.py`).

**Примітки/ризики**

- Це **не** стоп процесу: лише гейтінг важких циклів. Слухач `fxcm:status` та публікація стану мають залишатися активними.

---

## 2025-12-13 — Rich Live status bar у консолі для SMC пайплайна

**Що змінено**

- Додано консольний "живий" status bar (Rich Live), який оновлюється в одному рядку та не конфліктує з логами RichHandler у PowerShell/VS Code.
- Status bar читає SMC snapshot із Redis (`REDIS_SNAPSHOT_KEY_SMC`) і показує базові стани: `pipeline_state`, FXCM market/price/ohlcv та Redis up/down.
- Додано перемикач через ENV: `SMC_CONSOLE_STATUS_BAR=0` вимикає панель.
- Додано TTY-перевірку **саме по stderr** (бо і Live, і RichHandler пишуть у stderr) + ранній вихід без polling, якщо stderr не TTY.
- У `app.main` використовується **спільний** `Console(stderr=True)` для RichHandler і Live (менше шансів на "затирання" логів).
- Додано явне `redirect_stderr=True` у Rich Live та `force_terminal=True` для спільного Console, щоб панель перерисовувалась на місці (без дублювання блоків) і логи гарантовано друкувались над нею.

**Де**

- app/console_status_bar.py
- app/main.py

**Тести/перевірка**

- Додано тести побудови snapshot: `tests/test_app_console_status_bar.py`.
- Запущено таргетно: `pytest tests/test_app_smc_producer_fxcm_idle.py tests/test_app_console_status_bar.py` → `6 passed`.
- Додатково перевірено: `pytest tests/test_app_console_status_bar.py` → `3 passed`.

---

## 2025-12-13 — Гейтінг запису OHLCV в UDS по статусу ринку/фіду (без падіння процесу)

**Що змінено**

- Повернуто/закріплено поведінку: ingest-процес не завершується, але **не пише** OHLCV в UDS при `market=closed` або коли `price/ohlcv != ok`.
- При "status unknown" (cold-start) ingest дозволений (щоб система стартувала незалежно від порядку подій).

**Де**

- data/fxcm_ingestor.py

**Тести/перевірка**

- Оновлено контрактні тести на кейс `market=closed` → очікуємо **0 записів в UDS** (skip-write).
- Запускались таргетні pytest-тести для контракту інгесту (див. наступний запис про файл тестів).

---

## 2025-12-13 — Розширення Rich status bar: pipeline/cycle + age snapshot

**Що змінено**

- Розширено консольний status bar: тепер показує не лише mode/market/ticks/redis, а й ключові метрики пайплайна.
- Додано: вік останнього SMC snapshot (age), pipeline ready/total/pct, capacity (processed/skipped), cycle seq/duration.
- Додано: компактний блок стану FXCM (proc/price/ohlcv) у вигляді одного рядка.
- Ліміт інформаційних рядків піднято до 8 (залишається один Panel у Live, без спаму логами).

**Де**

- app/console_status_bar.py

**Тести/перевірка**

- Оновлено/додано тести: `tests/test_app_console_status_bar.py`.
- Запущено таргетно: `pytest tests/test_app_console_status_bar.py tests/test_utils_rich_console.py` → `passed`.

---

## 2025-12-13 — Rich status bar: FXCM session (name/state + to_close/to_open)

**Що змінено**

- Додано відображення FXCM session: `session_name:session_state` + таймери `to_close`/`to_open` (якщо доступні).
- Ліміт рядків у панелі збільшено до 10, щоб не відсікати вже додані поля.

**Де**

- app/console_status_bar.py

**Тести/перевірка**

- Оновлено тести: `tests/test_app_console_status_bar.py`.
- Запущено таргетно: `pytest tests/test_app_console_status_bar.py tests/test_utils_rich_console.py` → `passed`.

---

## 2025-12-13 — Rich status bar: підсвітка FXCM session state

**Що змінено**

- Додано підсвітку `session_state` у рядку `sess` (open→green, closed→yellow, error→red), таймери `to_close/to_open` — cyan.
- Зміна лише в рендері (payload/snapshot без змін).

**Де**

- app/console_status_bar.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_app_console_status_bar.py tests/test_utils_rich_console.py` → `passed`.

---

## 2025-12-13 — Rich status bar: явний стан SMC (RUN/IDLE/WARMUP) + poll замість sleep

**Що змінено**

- Додано рядок `smc`, який явно показує стан: `RUN` (SMC рахує), `IDLE` (гейтінг по FXCM), `WARMUP` (недостатньо даних), `WAIT` (невідомо/очікування), + причина.
- Рядок `sleep` перейменовано на `poll` і виправлено формат: тепер для малих інтервалів показує `ms` замість округлення до `0s`.
- Це прибирає плутанину “sleep 0s” і відповідає на питання «SMC зараз спить чи працює».

**Де**

- app/console_status_bar.py

**Тести/перевірка**

- Оновлено тести: `tests/test_app_console_status_bar.py`.
- Запущено таргетно: `pytest tests/test_app_console_status_bar.py tests/test_utils_rich_console.py` → `passed`.

---

## 2025-12-13 — Rich status bar: uptime (робочий час) у рядку `cycle`

**Що змінено**

- У рядок `cycle` додано “робочий час” (uptime) процесу з відступом: `up=...`.
- Формат показує дні, коли тривалість перевищує 23:59 (наприклад `2d 03h12m`).

**Де**

- app/console_status_bar.py

**Тести/перевірка**

- Оновлено тести: `tests/test_app_console_status_bar.py`.
- Запущено таргетно: `pytest tests/test_app_console_status_bar.py` → `passed`.

---

## 2025-12-13 — Rich status bar: sess узгоджено з `market=closed`

**Що змінено**

- Якщо `market=closed`, рядок `sess` більше не показує `open` (примусово `CLOSED`, якщо не error-стан).
- При `market=closed` не показуємо `to_close` (щоб не вводило в оману), лишаємо `to_open`.

**Де**

- app/console_status_bar.py

**Тести/перевірка**

- Оновлено тести: `tests/test_app_console_status_bar.py`.
- Запущено таргетно: `pytest tests/test_app_console_status_bar.py` → `passed`.

---

## 2025-12-13 — Rich status bar: підсвітка FXCM proc/price/ohlcv + менше шуму

**Що змінено**

- У рядку `fxcm` додано підсвітку станів `proc/price/ohlcv`: ok→green, stale/lag→yellow, down/error→red.
- Рядок `lag` більше не показується як `0s` (показуємо лише якщо lag > 0).
- У рядку `smc` підсвічено причину `fxcm_market_closed` (щоб швидко читалось при IDLE).
- Зміни лише у рендері (payload/snapshot без змін).

**Де**

- app/console_status_bar.py

**Тести/перевірка**

- Запущено таргетно: `pytest tests/test_app_console_status_bar.py tests/test_utils_rich_console.py` → `passed`.

---

## 2025-12-13 — Redis FXCM OHLCV ingest: дефолт без лог-спаму

**Що змінено**

- Для інжестора `fxcm:ohlcv` піднято дефолт `log_every_n`: тепер без явного налаштування не логуються кожні 1–2 бари.
- Це зменшує шум у консолі та I/O навантаження при великому universe.

**Де**

- data/fxcm_ingestor.py

**Тести/перевірка**

- Логічна зміна дефолту (поведінка інжесту даних не змінюється). За потреби можна прогнати контракт: `pytest tests/test_fxcm_schema_and_ingestor_contract.py`.

---

## 2025-12-13 — Контракт/схеми FXCM повідомлень + юніт-тести контракту

**Що змінено**

- Додано модуль зі схемами/валідацією для FXCM payload:
  - OHLCV бари (`fxcm:ohlcv`), включно з підтримкою `complete` та forward-compatible extra полів.
  - Тіки (`fxcm:price_tik`).
  - Статус (`fxcm:status`).
- Закріплено контракт інгесту: в UDS потрапляють лише **complete=true** бари; додаткові (мікроструктурні) поля не мають «просочуватись» у канонічний OHLCV у UDS.

**Де**

- data/fxcm_schema.py
- data/fxcm_ingestor.py
- tests/test_fxcm_schema_and_ingestor_contract.py

**Тести/перевірка**

- Додано/оновлено `tests/test_fxcm_schema_and_ingestor_contract.py` (валідація схем + поведінка інгесту на невалідних/неповних барах, гейтінг по статусу).

---

## 2025-12-13 — Юніт-тести idle-рішень SMC (детермінована перевірка reason)

**Що змінено**

- Додано окремий тестовий файл, який перевіряє рішення "бігти/не бігти" для SMC-циклу на базі `fxcm:status`.
- Перевіряються кейси:
  - `market=closed` → IDLE (`fxcm_market_closed`)
  - `market=open`, `price=ok`, `ohlcv=ok` → RUN (`fxcm_ok`)
  - `price=stale` → IDLE (`fxcm_price_stale`)
  - `ohlcv=lag` → IDLE (`fxcm_ohlcv_lag`)

**Де**

- tests/test_app_smc_producer_fxcm_idle.py

**Тести/перевірка**

- `pytest tests/test_app_smc_producer_fxcm_idle.py` → `4 passed`.

---

## 2025-12-xx — (історично в цій сесії) UI live-стрімінг, tick-апдейти, та стійкість до рестартів Redis

> Примітка: цей блок зафіксовано ретроспективно зі стислою деталізацією; точні команди тестів/прогони не відновлюю без логів.

**Що змінено**

- UI_v2 почав отримувати live OHLCV і/або тіки через WS-проксі з Redis (оновлення графіка без ручного refresh).
- Додано частіші оновлення свічки через агрегування тіків між close барами.
- Прибрано потребу в окремому статичному сервері: web-клієнт `UI_v2/web_client` віддається з бекенду.
- Додано backoff/reconnect для pubsub-споживачів, щоб пайплайн не падав при рестарті Redis.

**Де**

- UI_v2/fxcm_ohlcv_ws_server.py
- UI_v2/viewer_state_server.py
- UI_v2/web_client/*
- UI_v2/smc_viewer_broadcaster.py
- UI/publish_smc_state.py

**Тести/перевірка**

- Додавались таргетні тести для критичних змін (деталі — у відповідних тестових файлах у `tests/`).

---

## 2025-12-13 — Tick-agg адаптація: soft-валидація барів + dev chart (volume panel, opacity)

**Що змінено**

- `fxcm:ohlcv` schema: додано per-bar soft-валидацію — некоректні бари відкидаються, а відсутність `complete/synthetic` не вважається помилкою.
- Dev chart playground: додано volume histogram під свічками та opacity/насиченість свічок від нормованого volume (max за останні N барів).
- Gap-check інструмент: додано режим `--snapshot-file` для перевірки пропусків по локальному jsonl snapshot без Redis/UDS.
- Додано коротку документацію про перехід конектора на tick-agg і правила трактування `complete/synthetic`.

**Де**

- data/fxcm_schema.py
- tests/test_fxcm_schema_and_ingestor_contract.py
- UI_v2/web_client/chart_adapter.js
- UI_v2/web_client/chart_demo.js
- tools/uds_ohlcv_gap_check.py
- docs/fxcm_tick_agg_update_2025-12-13.md

**Тести/перевірка**

- Оновлено тести контракту схем: `pytest tests/test_fxcm_schema_and_ingestor_contract.py`.

---

## 2025-12-13 — UI_v2 web_client: README з арх-описом (порти/ендпойнти/Redis/CORS/безпека)

**Що змінено**

- Розширено README для UI_v2 web client як “контекст-дамп” для архітектора.
- Додано опис стеку UI_v2 у рамках `python -m app.main`: broadcaster, HTTP (статика+REST), WS viewer_state, FXCM WS міст.
- Зафіксовано дефолтні порти/ENV-параметри та точні endpoints.
- Додано перелік Redis ключів/каналів (SMC snapshot/state → viewer snapshot/channel; FXCM dev канали).
- Додано зауваження щодо CORS (`Access-Control-Allow-Origin: *`) та відсутності auth/TLS + рекомендації для прод.

**Де**

- UI_v2/web_client/README.md

**Тести/перевірка**

- Без змін у коді рантайму; лише документація.

---

## 2025-12-13 — S2 history_state (insufficient/stale_tail) + S3 warmup/backfill requester (Redis commands)

**Що змінено**

- Додано S2-логіку класифікації історії в UDS для (symbol, tf): `ok | insufficient | stale_tail`.
- У `smc_producer` додано перевірку `stale_tail`: актив із протухлим хвостом не вважається ready; у stats додається блок `history_state/needs_warmup/needs_backfill`.
- Додано S3 воркер requester, який (за флагом) періодично проходить по whitelist з `fxcm_contract` і публікує команди `fxcm_warmup` / `fxcm_backfill` у Redis канал (дефолт `ai_one:admin:commands`) з rate-limit.
- Додано конфіг для S2/S3 у `config.config` (без керування через ENV): enable/poll/cooldown/channel/stale_k.

**Де**

- app/fxcm_history_state.py
- app/fxcm_warmup_requester.py
- app/smc_producer.py
- app/main.py

**Тести/перевірка**

- Додано юніт-тести S2: `tests/test_s2_history_state.py`.
- Додано юніт-тести S3 requester: `tests/test_s3_warmup_requester.py`.

---

## Нагадування (обов’язково далі)

- Кожна нова правка в коді → **новий запис** сюди.
- Кожна нова правка → **таргетні тести** + запис у секції "Тести/перевірка" з результатом.
