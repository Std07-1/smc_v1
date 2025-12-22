# Playbook: SMC контекст (Stage6) у UI_v2 — як відтворюємо та як показуємо

Дата: 2025-12-21

Цей документ — «страховка після відкату»:
що саме ми називаємо **SMC контекстом**, як він **обчислюється** (core → stable),
як **пакуємо** у `viewer_state`, як **рендеримо** у UI_v2, і як **відтворюємо офлайн**
(replay/QA) так, щоб картинка була порівнювана з live.

> Ключове правило: **Stage6 — не торговий сигнал**, а «пояснювач режиму ринку» (контекст), який стабілізується анти‑фліпом поза SMC-core.

---

## 1) Що таке SMC контекст (Stage6)

### 1.1 Вихідні значення

Stage6 класифікує сценарій як одне з:

- `4_2`
- `4_3`
- `UNCLEAR` (чесне «не знаю», з явною причиною)

Плюс напрямок:

- `LONG` / `SHORT` / `NEUTRAL`

І «впевненість» (confidence), нормалізована приблизно в діапазоні `~0.50..0.95` (для валідних рішень).

### 1.2 Де живе логіка

- **Raw (детермінований) Stage6** живе в SMC-core і формується при кожному `process_snapshot`.
- **Stable (анти‑фліп, TTL, pending/confirm)** живе поза core в `app.SmcStateManager`.

Файли (SSOT):

- `smc_core/stage6_scenario.py` — детермінована класифікація `4_2/4_3/UNCLEAR`, гейти, `why[]`, `key_levels`, `telemetry`.
- `smc_core/engine.py` — додає Stage6 як `signals=[to_signal_dict(decision)]`.
- `app/smc_state_manager.py` — анти‑фліп/гістерезис: stable/raw/pending + пояснення.

---

## 2) Як формується Stage6 у бекенді (live пайплайн)

### 2.1 TF‑правда (контекст потрібен)

У проді Stage6 очікує **мультитаймфреймовий** вхід:

- primary для compute: `5m` (структура/ліквідність/зони)
- exec: `1m` (Stage5 execution, micro‑підтвердження)
- HTF context: `1h`, `4h` (HTF‑Lite: DR/ATR/bias)

SSOT конфіг:

- `config/config.py` → `SMC_TF_PLAN` та `SMC_RUNTIME_PARAMS`.

### 2.2 Побудова `SmcInput`

- `smc_core/input_adapter.py::build_smc_input_from_store()`
  - читає кілька TF з `UnifiedDataStore`
  - нормалізує датафрейми (timestamp з `open_time` у мс)
  - **best‑effort додає сесійний контекст** (Asia/London/NY) у `SmcInput.context`

Окремо (для QA/офлайн):

- `smc_core/input_adapter.py::build_smc_input_from_frames()` — той самий контракт, але для готових фреймів (без `UnifiedDataStore`).

### 2.3 Обчислення SMC-core + Stage6 raw

- `smc_core/engine.py::SmcCoreEngine.process_snapshot(snapshot)`
  1) `smc_structure` → structure_state
  2) `smc_liquidity` → liquidity_state
  3) `smc_zones` → zones_state
  4) `smc_execution` (Stage5, soft‑fail)
  5) `smc_core.stage6_scenario.decide_42_43(...)` → `Stage6Decision`
  6) `signals=[to_signal_dict(decision)]`

Raw Stage6 вбудований у `smc_hint.signals[]` як JSON-friendly dict.

### 2.4 Stable Stage6 (анти‑фліп) у `SmcStateManager`

- `app/smc_producer.py` після отримання `smc_hint` (у plain вигляді) викликає:
  - `SmcStateManager.apply_stage6_hysteresis(symbol, plain_hint, ...)`

Вхідні рейки з конфігу:

- `config/config.py` → `SMC_RUNTIME_PARAMS['stage6']`:
  - `ttl_sec`: мін. пауза між змінами stable
  - `confirm_bars`: скільки циклів новий сценарій має протриматись
  - `switch_delta`: на скільки новий confidence має бути вищим
  - `micro_confirm_*`: Stage5 execution **тільки як підтвердження**, не як вибір `4_2/4_3`

Вихід: dict, який мерджиться у `asset.stats` (ключі `scenario_*`).

---

## 3) Як Stage6 потрапляє у viewer_state (контракт)

### 3.1 SSOT контракт

- `core/contracts/viewer_state.py`:
  - `SmcViewerState.scenario: SmcViewerScenario`
  - Докстрінг підкреслює: **не торговий «сигнал»**.

### 3.2 Мапінг з `asset.stats` у `viewer_state.scenario`

- `UI_v2/viewer_state_builder.py::build_viewer_state()`:
  - якщо в `asset.stats` є `scenario_id`, формує `scenario` блок
  - кладе туди stable + raw + pending + анти‑фліп пояснення

Важливо:

- `why` та `raw_why` обрізаються до перших 5 елементів (щоб payload лишався легким).

---

## 4) Як Stage6 показуємо у UI_v2 (де/стилі/поведінка)

### 4.1 Де саме у DOM

- `UI_v2/web_client/index.html`
  - Панель: `<aside id="stage6-panel" class="stage6-panel" ...>` (всередині `.chart-overlay-shell`, поверх графіка)
  - Кнопка згортання/розгортання: `<button id="stage6-toggle-btn" class="stage6-panel__toggle">S6</button>`
    - знаходиться у `.chart-overlay-actions__right` (праворуч над графіком)

### 4.2 CSS (точні параметри)

- `UI_v2/web_client/styles.css` (секція “Stage6: компактна SMC-панель”):
  - позиція: `position: absolute; top: 8px; right: 8px; z-index: 30`
  - ширина: `width: 420px; max-width: min(520px, 46vw)`
  - фон/прозорість: `background: rgba(12, 16, 32, 0.08)`
  - рамка: `border: 1px solid rgba(31, 40, 51, 0.15)`
  - blur: `backdrop-filter: blur(2px)`
  - значення: `.stage6-panel__value { font-size: 0.72rem; font-weight: 650; color: #9da5b4; }`

Collapsed режим:

- клас `.stage6-panel--collapsed` прибирає фон/рамку/вміст (`.grid` не показується)
- додатково JS ставить/знімає `hidden` у панелі

### 4.3 Persist (щоб відновлювалося після перезавантаження)

- `UI_v2/web_client/app.js`
  - localStorage ключ: `smc_viewer_stage6_collapsed`
  - функції: `persistStage6Collapsed()`, `applyStage6Collapsed()`

UX:

- коли панель згорнута, кнопка `S6` перетворюється на компактний маркер:
  - стрілка напрямку + confidence (наприклад `↑0.84`)

### 4.4 Які рядки показуємо і з яких полів

- `UI_v2/web_client/app.js::renderStage6Panel(scenario, helpers)`

Рендер:

- **Режим**: stable `scenario_id` → «людська назва», tooltip показує код
- **stable_conf**: stable confidence + progress‑bar (ширина = confidence * 100%)
- **Що думаю зараз**: raw `raw_scenario_id` + raw confidence + `raw_unclear_reason` (якщо є)
- **Pending**: `pending_id` + `(pending_count/confirm_required)`
- **Чому**: `why[]` (коротко), tooltip — до 12 рядків
- **DR/PD/ATR14**: береться з `scenario.raw_key_levels.smc.htf` (або `key_levels` fallback)
- **Sweep/Hold/Failed hold**: `scenario.*.smc.facts.*` (людський формат)
- **Targets/POI**: `targets_near` / `poi_active` (лічильник + короткий preview)
- **TTL/blocked/override**: `scenario.anti_flip` + `scenario.last_eval` (діагностика анти‑фліпу)

“No context” блок:

- якщо контексту немає (гейти/UNCLEAR), UI показує:
  - `Немає контексту: <reason>`
  - і ховає grid (`#stage6-grid`), залишаючи `#stage6-nodata`.

---

## 5) Як відтворюємо офлайн (replay) і як НЕ зламати “картинку”

### 5.1 Базовий сценарій (UI офлайн)

1) Підняти UI_v2 offline сервер:

```powershell
; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" tools/run_ui_v2_offline.py
```

2) Прогнати replay снапшоту:

```powershell
; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" -m tools.replay_snapshot_to_viewer --path datastore/xauusd_bars_5m_snapshot.jsonl --limit 300 --window 300 --sleep-ms 25
```

3) Відкрити в браузері:

- `http://127.0.0.1:8080/?symbol=XAUUSD`

### 5.2 Важливе попередження про TF‑контекст

Stage6 (і частина “HTF‑відчуття” зон/пулів) залежить від **multi‑TF**:

- без `1h/4h` Stage6 буде часто `UNCLEAR` з `NO_HTF_FRAMES`
- без `1m` Stage5 execution‑підтвердження буде відсутнім

Тому офлайн‑replay, який годує SMC-core **лише одним TF** (`ohlc_by_tf={tf: frame}`), дає іншу картинку:

- HTF‑рівні можуть не з’являтися
- шум на 1m/5m буде виглядати «кашею»
- Stage6 частіше падатиме у `UNCLEAR`

Для порівнюваного replay треба будувати `SmcInput` як у проді:

- використовувати `smc_core.input_adapter.build_smc_input_from_frames()`
- подавати `ohlc_by_tf` мінімум з `5m + 1h + 4h` (і бажано `1m`)

### 5.3 Якщо “панель пропала”

- Натисни кнопку `S6` праворуч над графіком.
- Якщо стан «залип» після експериментів — очисти localStorage ключ:
  - `smc_viewer_stage6_collapsed`

---

## 6) Мінімальні перевірки після відкату (щоб швидко відновити)

### 6.1 Тести Stage6

- Детермінізм raw + наявність ключових рівнів:

```powershell
; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" -m pytest -q tests/test_smc_stage6_scenario.py -k deterministic
```

- Анти‑фліп/TTL:

```powershell
; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" -m pytest -q tests/test_smc_stage6_hysteresis.py
```

### 6.2 QA репорт для швидкої статистики

```powershell
; function с { } ; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" -m tools.qa_stage6_scenario_stats --path datastore/xauusd_bars_5m_snapshot.jsonl --steps 120 --warmup 220 --horizon-bars 60 --tp-atr 1.0 --sl-atr 1.0 --out reports/stage6_stats_xauusd_h60_restore_check.md --exemplars 12
```

---

## 7) Контрольні файли (швидкий “покажчик”)

- Core Stage6: `smc_core/stage6_scenario.py`
- Оркестрація + включення Stage6: `smc_core/engine.py`
- Anti‑flip/stable: `app/smc_state_manager.py`
- Виклик anti‑flip: `app/smc_producer.py`
- Контракт: `core/contracts/viewer_state.py` (`SmcViewerScenario`)
- Builder viewer_state: `UI_v2/viewer_state_builder.py`
- UI рендер: `UI_v2/web_client/app.js::renderStage6Panel`
- UI DOM: `UI_v2/web_client/index.html` (`#stage6-panel`, `#stage6-toggle-btn`)
- UI стилі: `UI_v2/web_client/styles.css` (секція Stage6)
