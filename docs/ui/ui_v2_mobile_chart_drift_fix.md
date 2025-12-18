# UI_v2 (mobile): «графік пливе вниз» — канонічний фікс

> Канонічна сторінка. Старий шлях: `docs/ui_v2_mobile_chart_drift_fix.md` (тепер — stub).

# UI_v2 (mobile): «графік пливе вниз» — канонічний фікс

## Симптом

- На мобільному (особливо Android) у режимі **«Графік»** полотно/контейнер чарта з часом «дрейфує вниз».
- Часто супроводжується "стрибаючою" висотою екрана через адресний рядок/toolbar.

## Коренева причина

- `100vh/100dvh` і/або `innerHeight` на мобільних браузерах не є стабільними: висота viewport змінюється при появі/зникненні адресного рядка.
- Додатково, у UI_v2 `.card-chart` переноситься між слотами (`#overview-chart-slot` / `#chart-slot`), і без коректного `flex: 1` у слота чарт може не мати стабільної висоти.
- Якщо висота контейнера чарта задається у `%` або «плаває», `ResizeObserver` у `chart_adapter.js` може постійно підлаштовувати розмір — візуально це виглядає як дрейф.

## Рішення (стандарт для mobile)

1) Прив’язати висоту сторінки до **реального** viewport через `visualViewport.height`:

- JS оновлює CSS-змінну `--app-vh` (у px) з `window.visualViewport.height` (fallback: `innerHeight`).
- Підписатись на:
  - `window.visualViewport.resize`
  - `window.visualViewport.scroll`
  - - `window.resize/orientationchange` як fallback

2) Для мобільного chart-режиму використати **px-висоту** для контейнера чарта:

- `--chart-height: var(--mobile-chart-height, ...)` (у px)
- `--mobile-chart-height` рахується як `vh - headerH - bottomH` (без додаткових reserve)

3) Обов’язково забезпечити стабільний flex-контур:

- `body` як flex-колонка: header → main → bottom-nav
- `#chart-slot` має бути `display: flex; flex: 1; min-height: 0;`
- Вимкнути `transition: height` у `.chart-demo-container` на мобільному chart, щоб не провокувати "jitter".

## Де в коді

- CSS:
  - `@media (max-width: 768px)` у `UI_v2/web_client/styles.css`
  - `height: var(--app-vh, 100dvh)`
  - `body.ui-view-chart { --chart-height: var(--mobile-chart-height, 520px); }`
  - flex-правила для `#chart-slot` та контейнерів чарта

- JS:
  - `updateMobileChartHeightVar()` у `UI_v2/web_client/app.js`
  - слухачі `visualViewport.resize/scroll` у `initUiViews()`

## Швидка регресійна перевірка

- На мобільному відкрити view `chart` і покрутити сторінку/почекати 5–10с → чарт не «сповзає» вниз.
- Показати/сховати адресний рядок (scroll) → висота чарта стабільно підлаштовується без дрейфу.
- Перемкнути Overview ↔ Chart → чарт не перепідключається і коректно робить resize.
