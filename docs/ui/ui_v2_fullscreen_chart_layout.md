# UI_v2: fullscreen графік — «пливе вниз» (лейаут)

> Канонічна сторінка. Старий шлях: `docs/ui_v2_fullscreen_chart_layout.md` (тепер — stub).

# UI_v2: fullscreen графік — «пливе вниз» (лейаут) • нотатки

Цей документ фіксує повторювану проблему UI_v2: у fullscreen режимі графік може **«стрімко плисти вниз»** або поводитись так, ніби його постійно «підштовхує» верстка.

Мета: мати 1 місце, куди можна повернутись, не перечитуючи історію чату/UPDATE.

---

## Симптоми

- Увімкнули fullscreen для `.card-chart` → графік **дрейфує вниз** навіть без взаємодії.
- Може виглядати як нескінченний reflow/resize: елемент змінює висоту, `ResizeObserver` реагує, графік ресайзиться, і так по колу.
- Додатково може проявлятися як:
  - підрізана нижня time scale;
  - «смикання» висоти графіка.

## Причина (типова)

Найчастіше це **не скрол**, а **лейаут**:

- у fullscreen `.card-chart--fullscreen` стає flex-контейнером, але обгортка чарта (`.chart-overlay-shell`) не бере участі у flex-розкладці;
- `#chart-container` ( `.chart-demo-container` ) має `height: 100%` / padding / overflow, що при `min-height: 0` і nested контейнерах може створювати нестабільне вимірювання `getBoundingClientRect()`;
- паралельно існує control висоти (range), який керує `--chart-height` і може конфліктувати з «автовисотою» fullscreen.

## Правильне рішення (канон)

### 1) У fullscreen повністю прибрати керування висотою

- При вході у fullscreen: `setHeightControlEnabled(false)`.
- У CSS: `.card-chart--fullscreen .chart-height-control { display: none; }`.

Це прибирає конфлікт між "заданою" висотою (`--chart-height`) та fullscreen flex-висотою.

### 2) Дати контейнеру чарта коректно рости у flex

У fullscreen:

- `.chart-overlay-shell` має стати flex-елементом і займати доступний простір:
  - `flex: 1 1 0; min-height: 0; display: flex; flex-direction: column;`
- `#chart-container` (`.chart-demo-container`) має рости **всередині** `.chart-overlay-shell`, без `height: 100%` і без нижнього padding:
  - `flex: 1 1 0; min-height: 0; height: auto; padding-bottom: 0; overflow: hidden;`

### 3) Уникати «силових» scroll-lock хаків як основного лікування

Якщо fullscreen зроблений через CSS `position: fixed`, то **overflow hidden на html/body** може бути корисним,
але "жорсткі" рішення на кшталт `body{position:fixed; top=-scrollY}` і активні блокери wheel/touch/keys —
частіше маскують симптоми, але не прибирають реальну причину (лейаут/resize-loop).

## Де це реалізовано в репо

- CSS fullscreen/flex-правки:
  - `UI_v2/web_client/styles.css`
  - селектори: `.card-chart--fullscreen`, `.card-chart--fullscreen .chart-overlay-shell`, `.card-chart--fullscreen .chart-demo-container`
- JS enable/disable для контролу висоти:
  - `UI_v2/web_client/app.js`
  - функція: `setHeightControlEnabled(enabled)`
  - виклики у: `enterChartFullscreen()` / `exitChartFullscreen()`

## Мінімальний чекліст при регресії

1) Увімкни fullscreen і подивись, чи "пливе" **без** руху миші/колеса.
2) Переконайся, що в fullscreen:
   - `.chart-height-control` реально `display:none`;
   - `.chart-overlay-shell` має `flex: 1` і `min-height: 0`;
   - `.chart-demo-container` має `height:auto`, `flex:1`, `overflow:hidden`, без `padding-bottom`.
3) Якщо все одно пливе — дивись `ResizeObserver` у `chart_adapter.js` (чи немає нескінченного циклу через нестабільні розміри контейнера).

---

Дата фіксації: 2025-12-14.
