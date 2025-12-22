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
