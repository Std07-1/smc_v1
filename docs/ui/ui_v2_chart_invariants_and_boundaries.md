# UI_v2: інваріанти та межі відповідальності графіка (SSOT)

**Статус:** SSOT (канонічний документ для UI_v2 графіка)

**Ціль:** зафіксувати правила (інваріанти) та межі відповідальності для взаємодій графіка, щоб майбутні правки не ламали критичні UX-фікси (wheel/drag/scale/tooltip).

**Scope:** лише фронтенд UI_v2, модуль [UI_v2/web_client/chart_adapter.js](../../UI_v2/web_client/chart_adapter.js).

**Не входить у scope:** бекенд-джерела даних, UDS, контракти FXCM/Redis, алгоритміка SMC.

---

## 1) Словник (канонічні терміни)

- **manualRange** — ручний діапазон по Y (ціна), який керується нашим vertical-pan / wheel-zoom. Реалізовано як `priceScaleState.manualRange`.
- **lastAutoRange** — останній валідний autoscale-діапазон, який зберігаємо для старту manual-взаємодій без «стрибка». Реалізовано як `priceScaleState.lastAutoRange`.
- **effectiveRange** — «поточна правда» для взаємодій (manualRange → lastAutoRange → fallback від `coordinateToPrice`). Реалізовано у `getEffectivePriceRange()`.
- **built-in scaling** — вбудований скейл lightweight-charts, який ми частково лишаємо увімкненим (для pane/time), але маємо детерміновано блокувати для price-axis у критичних сценаріях.

---

## 2) Межі відповідальності (Boundaries)

### 2.1 Хто за що відповідає

- **[UI_v2/web_client/chart_adapter.js](../../UI_v2/web_client/chart_adapter.js)**
  - Єдине місце, де дозволено керувати:
    - wheel/drag взаємодіями по price-axis та pane;
    - `priceScaleState` (`manualRange`, `lastAutoRange`);
    - правилами перехоплення подій (capture, `preventDefault`, `stopImmediatePropagation`).
  - Має тримати інваріанти нижче.

- **Інші файли UI_v2 (app/router/viewer)**
  - Можуть викликати публічні методи/інтеграційні точки контролера (наприклад `setBars`, `setLiveBar`, `setZones` тощо).
  - НЕ повинні:
    - напряму ставити `chart.applyOptions({...handleScale...})` або перепідписувати wheel/drag поверх контейнера;
    - торкатись внутрішніх змінних `priceScaleState` через хаки/доступ до замикань.

### 2.2 Anti-corruption правила для інтеграції

- Дані, що заходять у `setBars()` / `setLiveBar()`, мають бути вже у канонічному форматі UI (time — секунди, OHLC — числа). Нормалізація допускається лише локально в chart_adapter (як зараз через `normalizeBar`).

---

## 3) Інваріанти шкали ціни (Y) та взаємодій

### 3.1 Ініціалізація / зміна датасету

**Інваріант:** при «перезапуску» датасету (символ/TF/бекфіл/ресет) не можна змішувати старий ручний Y-range з новими даними.

- У `setBars()` при порожньому масиві або при `looksLikeNewDataset`:
  - обовʼязково скидаємо `manualRange` через `resetManualPriceScale({ silent: true })`;
  - обовʼязково скидаємо `lastAutoRange = null`.

Це зафіксовано в коді в `setBars()`.

### 3.2 Єдине джерело правди для Y-range

**Інваріант:** якщо активний `manualRange`, то ВСІ серії, що сидять на правій шкалі, мають повертати ОДНАКОВИЙ `priceRange` через `autoscaleInfoProvider`.

Причина: інакше lightweight-charts «склеює» діапазони різних серій і виникає ефект «стеля/підлога».

Кодова опора:

- `makePriceScaleAutoscaleInfoProvider(trackAutoRange)`
- `overlayAutoscaleInfoProvider()`

### 3.3 Хто має право оновлювати lastAutoRange

**Інваріант:** `lastAutoRange` оновлюється лише з autoscale базової **історичної** серії (candles), а не з live-серії або оверлеїв.

Причина: live-серія часто містить 1 свічку / мікро-рухи, і якщо вона «перетре» `lastAutoRange`, перша ручна взаємодія (zoom/pan) може стартувати з неправильного діапазону → різкий Y-стрибок.

Кодова опора:

- `const priceScaleAutoscaleInfoProvider = makePriceScaleAutoscaleInfoProvider(true);`
- `const livePriceScaleAutoscaleInfoProvider = makePriceScaleAutoscaleInfoProvider(false);`
- `liveCandles.applyOptions({ autoscaleInfoProvider: livePriceScaleAutoscaleInfoProvider });`

### 3.4 Перехоплення wheel на price-axis має бути детермінованим

**Інваріант:** wheel по price-axis має перехоплюватись у capture-фазі (`{ passive: false, capture: true }`) і глушити built-in scaling так, щоб не було «разового проскакування» після refresh/зміни TF.

Мінімальні вимоги до реалізації:

- listener на `container` у capture (`WHEEL_OPTIONS`);
- `event.preventDefault()` + `stopImmediatePropagation()` (якщо доступно) + `stopPropagation()`;
- якщо метрики/діапазон ще не готові — дія може бути відкладена на 1 кадр, але built-in не має «встигнути».

Кодова опора:

- `setupPriceScaleInteractions()` (wheel, hit-test axis/pane, RAF defer)

### 3.5 effectiveRange має бути стабільним та безпечним

**Інваріант:** `getEffectivePriceRange()` має повертати діапазон у такому порядку пріоритетів:

1) `manualRange` (якщо активний)
2) `lastAutoRange` (якщо відомий)
3) fallback через `candles.coordinateToPrice()` (лише якщо є валідні метрики pane)

Якщо метрик немає — повертаємо `null` і НЕ робимо неконтрольованих змін у шкалі.

Кодова опора:

- `getEffectivePriceRange()`

---

## 4) Заборонені зміни (guard-rails)

- Не додавати нові wheel/drag listeners на контейнер графіка, які конкурують з `setupPriceScaleInteractions()`.
- Не оновлювати `lastAutoRange` з:
  - live-серії,
  - оверлеїв,
  - або будь-яких «хаків» з DOM/координат без чіткої причини.
- Не вимикати capture-перехоплення wheel по price-axis без заміни на еквівалентний deterministic механізм.
- Не «підкручувати» `handleScale.*`/`handleScroll.*` з інших модулів без ревʼю цих інваріантів.

---

## 5) Мінімальний чекліст ревʼю (перед злиттям UI змін)

- Чи не зʼявився новий шлях, де `manualRange` лишається активним при зміні датасету/TF?
- Чи гарантується, що `lastAutoRange` не перетирається live-оновленнями?
- Чи wheel по price-axis не може «проскакувати» в built-in scale при холодному старті (метрики=0)?
- Чи немає нового коду, який напряму змінює `chart.applyOptions({...})` для scale/scroll поза chart_adapter?

---

## 6) Мінімальні сценарії перевірки (manual smoke)

- Refresh/F5 → одразу wheel по price-axis: без разового Y-ривка.
- Зміна TF → одразу wheel по price-axis: без разового Y-ривка.
- Wheel у pane без Shift: працює як раніше (time zoom/scroll бібліотеки).
- Shift+wheel у pane: вертикальний pan працює, сторінка не скролиться.

---

## 7) Примітка про тести (цільова піраміда)

Цей документ — база для тестів. Наступним кроком має бути:

- **Unit/logic (≈80%)**: тестувати transitions `manualRange/lastAutoRange/effectiveRange`, hit-test та відкладений wheel без реального браузера (може вимагати мінімальної екстракції чистих функцій).
- **Playwright E2E (≈20%)**: 1–3 smoke сценарії з реальними wheel/drag подіями (після refresh/зміни TF).
