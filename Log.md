# Log змін (AiOne_t / smc_v1)

Цей файл — журнал змін у репозиторії. Формат записів: дата/час → що зроблено → де зроблено → причина → тести/перевірки → ризики/нотатки.

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
	- **Примітка**: `axisPressedMouseMove.price = true` також означає, що drag по price-axis може активувати built-in логіку шкали. Поточний кастомний код блокує лише `handleScroll.pressedMouseMove` під час нашого vertical-pan, але не вимикає `handleScale.axisPressedMouseMove.price` глобально.
	- **Тести/перевірки**: не запускались (зміна лише документаційна, код не чіпали).
	- **Ризики/нотатки**:
		- Цей запис — “зріз стану” перед будь-якими правками.
		- Якщо будемо робити мін-фікс, треба зберегти UX: wheel у pane лишається за бібліотекою, а wheel у price-axis має бути детерміновано перехоплений без одноразових пропусків.

- **UI_v2: P0 фікс “перший wheel не проскакує в built-in scale”**
	- **Проблема**: у `setupPriceScaleInteractions()` подію wheel глушили (preventDefault/stop*) лише після `getEffectivePriceRange()`. Якщо `getEffectivePriceRange()` на першій взаємодії повертає `null`, wheel проходить у built-in масштабування lightweight-charts → разовий “ривок/розмаз”.
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
