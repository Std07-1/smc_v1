# UI_v2 SMC audit (as-is) — A–E (2025-12-25)

Цей документ описує **як система працює зараз** (truth → viewer_state → UI_v2), без пропозицій змін. Мета: дати SSOT-карту для корекцій “без шуму”.

## Скоуп

- Об’єкти A–E:
  - A) **Levels** (ключові рівні/“pools” у liquidity): EQH/EQL, PDH/PDL, BSL/SSL, RANGE, session H/L.
  - B) **Pools** (liquidity pools: усі типи + життєвий цикл).
  - C) **POI Zones** (OB/Breaker/FVG + POI selection).
  - D) **Structure** (BOS/CHOCH, swings/legs/ranges/OTE як бекенд truth + UI фільтри).
  - E) **Magnets/Targets** (магніти та liquidity_targets).

## Dataflow (end-to-end)

```mermaid
flowchart LR
  FXCM[FXCM ticks/bars] --> UDS[UnifiedDataStore]
  UDS --> Producer[app/smc_producer.py]
  Producer --> State[UiSmcStatePayload (Redis)]
  State --> Broadcaster[UI_v2/smc_viewer_broadcaster.py]
  Broadcaster --> Builder[UI_v2/viewer_state_builder.py]
  Builder --> Viewer[SmcViewerState (Redis snapshot)]
  Viewer --> Web[UI_v2/web_client/app.js]
  Web --> Render[UI_v2/web_client/chart_adapter.js]

  subgraph SMC_core
    Engine[smc_core/engine.py] --> Structure[smc_structure]
    Engine --> Liquidity[smc_liquidity]
    Engine --> Zones[smc_zones]
    Engine --> Exec[smc_execution]
  end

  Producer --> Engine
  Engine --> Producer
```

Ключові “шви”:

- SMC truth формується в `smc_core/engine.py` у порядку: structure → liquidity → zones → execution.
  - Див. [smc_core/engine.py](../../smc_core/engine.py#L25).
- Presentation-layer стабілізація/капи/TTL живе в `UI_v2/viewer_state_builder.py`.
  - Див. [UI_v2/viewer_state_builder.py](../../UI_v2/viewer_state_builder.py#L90).
- UI_v2 має **додаткові гейти/квоти** (особливо `?trader_view=1`) у `chart_adapter.js`.
  - Див. [UI_v2/web_client/chart_adapter.js](../../UI_v2/web_client/chart_adapter.js#L340).

## Контракт (SSOT)

- `SmcViewerState` та `UiSmcStatePayload` описані в:
  - [core/contracts/viewer_state.py](../../core/contracts/viewer_state.py#L117)
- Версія схеми:
  - [core/contracts/viewer_state.py](../../core/contracts/viewer_state.py#L18)

## A) Levels (key levels)

### Що це “по факту зараз”

- У UI_v2 “levels” = **підмножина liquidity pools** (рівні/лінії), які UI нормалізує та пріоритезує.
- Truth-джерело: liquidity state (`smc_liquidity`) + його `pools`/`meta`.
  - Вхід у пайплайн: [smc_liquidity/**init**.py](../../smc_liquidity/__init__.py#L25)

### Як потрапляє в UI

1) `smc_liquidity` будує pools (EQ/PD/session/range/wicks/… залежно від конфігу).
2) `UI_v2/viewer_state_builder.py`:
   - у preview (`smc_compute_kind=preview`) **pools не показує взагалі** (anti-flicker правило).
     - Див. [UI_v2/viewer_state_builder.py](../../UI_v2/viewer_state_builder.py#L339).
   - у close режимі робить top-K selection і TTL “прихованих” pools.
     - cap: [UI_v2/viewer_state_builder.py](../../UI_v2/viewer_state_builder.py#L44)
     - hidden TTL: [UI_v2/viewer_state_builder.py](../../UI_v2/viewer_state_builder.py#L62)
3) `UI_v2/web_client/chart_adapter.js`:
   - normal view: пріоритезує **ключові** типи (EQ/PD/BSL/SSL/Range/session) і малює їх компактно.
     - нормалізація/типи: [UI_v2/web_client/chart_adapter.js](../../UI_v2/web_client/chart_adapter.js#L2840)
   - trader view: жорсткий whitelist типів рівнів.
     - прапор: [UI_v2/web_client/chart_adapter.js](../../UI_v2/web_client/chart_adapter.js#L340)

### Важливі edge cases

- Якщо payload часто в `preview`, то:
  - close_step у cache **не росте** → newborn gating може тримати зони/пули “порожніми” довше.
  - Див. [UI_v2/viewer_state_builder.py](../../UI_v2/viewer_state_builder.py#L126-L135).
- Якщо lifecycle-поля є в payload, UI може **ховати** “вже відпрацьовані” рівні.
  - `isPoolInactive`: [UI_v2/web_client/chart_adapter.js](../../UI_v2/web_client/chart_adapter.js#L2980)

## B) Pools (liquidity pools)

### Truth-джерело

- Обчислення liquidity state:
  - [smc_liquidity/**init**.py](../../smc_liquidity/__init__.py#L25)
- Preview suppression SFP/WICK_CLUSTER за замовчуванням:
  - [smc_liquidity/**init**.py](../../smc_liquidity/__init__.py#L53-L56)
- Глобальний domain cap у конфігу:
  - [smc_core/config.py](../../smc_core/config.py#L92)

### Presentation caps (viewer_state)

- UI cap pools (після ранжування):
  - [UI_v2/viewer_state_builder.py](../../UI_v2/viewer_state_builder.py#L44)
- Hidden TTL (щоб “не зникало одразу” і не ламало довіру):
  - [UI_v2/viewer_state_builder.py](../../UI_v2/viewer_state_builder.py#L62)

## C) POI Zones (OB/Breaker/FVG + POI selection)

### Truth-джерела зон

- `smc_zones.compute_zones_state(...)`:
  - [smc_zones/**init**.py](../../smc_zones/__init__.py#L44)
- Active zones cap per side (domain):
  - [smc_zones/**init**.py](../../smc_zones/__init__.py#L41)

### “Які зони бачить UI” — вибір джерела

UI_v2 **може рендерити різні масиви** з `state.zones.raw`:

- debug (`?debug_zones=1`): використовує `raw.zones`.
- trader view (`?trader_view=1`): пріоритет `raw.poi_zones → raw.active_zones → raw.zones`.
- normal: пріоритет `raw.active_zones → raw.poi_zones → raw.zones`.

Див. мапінг і вибір `zonesSource`:

- [UI_v2/web_client/app.js](../../UI_v2/web_client/app.js#L436-L452)

### Критичні поля для UI (і фолбеки)

- `origin_time`:
  - потрібен для DOM-лейблів та TF-truth gate; якщо у зоні немає, UI робить best-effort з альтернатив.
  - Див. [UI_v2/web_client/app.js](../../UI_v2/web_client/app.js#L477-L490)
- `poi_type`:
  - у `raw.poi_zones` часто немає; UI підставляє best-effort (інакше trader_view може відфільтрувати POI до нуля).
  - Див. [UI_v2/web_client/app.js](../../UI_v2/web_client/app.js#L540-L555)
- `filled_pct`:
  - нормалізація до 0..100 у UI.
  - Див. [UI_v2/web_client/app.js](../../UI_v2/web_client/app.js#L511)

### UI gates: “TF truth” (щоб не малювати те, чого ще не могло бути)

У `chart_adapter.js` зона проходить TF-truth gate, якщо:

- `origin_time % tfSec == 0`
- `origin_time <= lastTfClose` (останній повний close для view TF)

Див. перевірки:

- [UI_v2/web_client/chart_adapter.js](../../UI_v2/web_client/chart_adapter.js#L4312-L4335)

### Trader view: квоти/дистанції (POI)

- POI distance gates:
  - FVG: `<= 1.5 ATR`
  - OB/Breaker: `<= 2.75 ATR`
- POI квоти:
  - `total=3`, `max_fvg=1`, `max_ob_breaker=1`

Див. константи:

- [UI_v2/web_client/chart_adapter.js](../../UI_v2/web_client/chart_adapter.js#L359-L363)

## D) Structure (events / swings / ranges / OTE)

### Truth-джерело

- `smc_structure.compute_structure_state(...)`:
  - [smc_structure/**init**.py](../../smc_structure/__init__.py#L28)

### Presentation у UI

- trader view обмежує історію structure markers (TTL + cap):
  - TTL bars: [UI_v2/web_client/chart_adapter.js](../../UI_v2/web_client/chart_adapter.js#L355)
  - max events: [UI_v2/web_client/chart_adapter.js](../../UI_v2/web_client/chart_adapter.js#L356)

## E) Magnets / Targets

### Truth-джерело

- Targets формуються в liquidity pipeline і живуть у `liquidity.meta["liquidity_targets"]`.
- Базовий вхід: [smc_liquidity/**init**.py](../../smc_liquidity/__init__.py#L25)

### Execution прив’язка (in_play)

- Execution state (Stage5) використовує POI/targets, коли “in_play” у радіусі ATR.
  - Виклик: [smc_execution/**init**.py](../../smc_execution/__init__.py#L59)
  - Параметр: [smc_core/config.py](../../smc_core/config.py#L75)

## Preview vs Close (семантика)

- `smc_compute_kind` приходить у `smc_hint.meta` і прокидується в `viewer_state_builder`.
  - Див. [smc_core/engine.py](../../smc_core/engine.py#L94-L96)
- У preview:
  - `viewer_state_builder` **не інкрементить** `cache.close_step`.
    - [UI_v2/viewer_state_builder.py](../../UI_v2/viewer_state_builder.py#L129-L135)
  - pools **не показує взагалі**.
    - [UI_v2/viewer_state_builder.py](../../UI_v2/viewer_state_builder.py#L339-L343)

## Restart / replay (що міняється)

- `UI_v2/smc_viewer_broadcaster.py` тримає `cache_by_symbol` (in-memory) і передає `ViewerStateCache()` у `build_viewer_state`.
  - Див. [UI_v2/smc_viewer_broadcaster.py](../../UI_v2/smc_viewer_broadcaster.py#L134-L171)
- Після рестарту процесу broadcaster:
  - cache скидається → `close_step` знову 0 → newborn gating (zones/pools) може тимчасово показувати “порожньо”.
  - гейти newborn: [UI_v2/viewer_state_builder.py](../../UI_v2/viewer_state_builder.py#L53-L54)

## Інвентар параметрів (SSOT таблиця)

| Параметр | Де задано (default) | Де використовується (ключові місця) | Роль/ефект |
|---|---|---|---|
| `eq_tolerance_pct=0.12` | [smc_core/config.py](../../smc_core/config.py#L14) | liquidity EQ clustering (пули/магніти) | “склейка” рівнів як EQ |
| `fvg_min_gap_atr=0.5` | [smc_core/config.py](../../smc_core/config.py#L48) | FVG detector | мінімальний gap для FVG |
| `fvg_min_gap_pct=0.0015` | [smc_core/config.py](../../smc_core/config.py#L49) | FVG detector | альтернативний поріг gap у % |
| `fvg_max_age_minutes=4320` | [smc_core/config.py](../../smc_core/config.py#L50) | FVG TTL | “старіння” imbalance |
| `max_zone_span_atr=2.0` | [smc_core/config.py](../../smc_core/config.py#L57) | POI/zone filters | відсів “занадто широких” зон |
| `zone_merge_iou_threshold=0.6` | [smc_core/config.py](../../smc_core/config.py#L64) | domain-level merge зон | злиття перекритих зон |
| `_ACTIVE_ZONES_CAP_PER_SIDE=3` | [smc_zones/**init**.py](../../smc_zones/__init__.py#L41) | active_zones selection | cap на LONG/SHORT зони |
| `exec_in_play_radius_atr=0.9` | [smc_core/config.py](../../smc_core/config.py#L75) | execution gating | вмикає micro-events біля POI/targets |
| `liquidity_pools_max_total=64` | [smc_core/config.py](../../smc_core/config.py#L92) | liquidity throttle | domain cap pools |
| `liquidity_preview_include_sfp_and_wicks=False` | [smc_core/config.py](../../smc_core/config.py#L118) | preview suppression | preview менше шуму |
| `MAX_POOLS=8` | [UI_v2/viewer_state_builder.py](../../UI_v2/viewer_state_builder.py#L44) | viewer_state pools cap | presentation cap pools |
| `MIN_CLOSE_STEPS_BEFORE_SHOW_ZONES=1` | [UI_v2/viewer_state_builder.py](../../UI_v2/viewer_state_builder.py#L53) | newborn gating зон | затримка показу зон |
| `MIN_CLOSE_STEPS_BEFORE_SHOW_POOLS=2` | [UI_v2/viewer_state_builder.py](../../UI_v2/viewer_state_builder.py#L54) | newborn gating pools | затримка показу pools |
| `ZONES_MERGE_IOU_THRESHOLD=0.75` | [UI_v2/viewer_state_builder.py](../../UI_v2/viewer_state_builder.py#L58) | presentation merge зон | зменшення фліккера (UI merge) |
| `POOLS_HIDDEN_TTL_CLOSE_STEPS=8` | [UI_v2/viewer_state_builder.py](../../UI_v2/viewer_state_builder.py#L62) | pools hidden TTL | анти “зникло і повернулось” |
| `TRADER_VIEW_ENABLED` | [UI_v2/web_client/chart_adapter.js](../../UI_v2/web_client/chart_adapter.js#L340) | UI gates/квоти | режим “мінімум шуму” |
| `TRADER_STRUCTURE_TTL_BARS=120` | [UI_v2/web_client/chart_adapter.js](../../UI_v2/web_client/chart_adapter.js#L355) | structure markers | TTL подій структури |
| `TRADER_MAX_STRUCTURE_EVENTS=6` | [UI_v2/web_client/chart_adapter.js](../../UI_v2/web_client/chart_adapter.js#L356) | structure markers | cap подій структури |
| `TRADER_POI_MAX_DISTANCE_ATR_FVG=1.5` | [UI_v2/web_client/chart_adapter.js](../../UI_v2/web_client/chart_adapter.js#L359) | POI selection UI-side | gate POI FVG |
| `TRADER_POI_MAX_DISTANCE_ATR_OB_BREAKER=2.75` | [UI_v2/web_client/chart_adapter.js](../../UI_v2/web_client/chart_adapter.js#L360) | POI selection UI-side | gate POI OB/BRK |
| `TRADER_MAX_POI_TOTAL=3` | [UI_v2/web_client/chart_adapter.js](../../UI_v2/web_client/chart_adapter.js#L361) | POI selection UI-side | total cap |
| `TRADER_MAX_FVG=1` | [UI_v2/web_client/chart_adapter.js](../../UI_v2/web_client/chart_adapter.js#L362) | POI selection UI-side | cap FVG |
| `TRADER_MAX_OB_BREAKER=1` | [UI_v2/web_client/chart_adapter.js](../../UI_v2/web_client/chart_adapter.js#L363) | POI selection UI-side | cap OB/BRK |
| `TRADER_FAR_KEY_MAX_DISTANCE_ATR=8` | [UI_v2/web_client/chart_adapter.js](../../UI_v2/web_client/chart_adapter.js#L366) | FAR_KEY selection | gate далеких маяків |
| `SMC_TF_PLAN` | [config/config.py](../../config/config.py#L351) | tf_plan у meta | SSOT TF для engine/viewer |
| `stage6.micro_dmax_atr=0.80` | [config/config.py](../../config/config.py#L369-L383) | Stage6 (SmcStateManager) | гейти micro confirm |

## Топ-10 джерел “шуму” (де з’являється)

1) Різниця між preview/close (дві версії truth) → фліккер.
2) Надмірний domain cap (`liquidity_pools_max_total`) + слабкі ранжування → багато дрібних pools.
3) UI cap `MAX_POOLS=8` → “важливе може не влізти”, якщо ранжування не збалансоване.
4) POI без `origin_time` → зони не проходять TF-truth gate / DOM-лейбли зникають.
5) POI без `poi_type` → trader_view може відфільтрувати POI до нуля.
6) Нестабільні ідентифікатори зон/кластерів → “no-repaint” не працює і геометрія стрибає.
7) Відсутні lifecycle-поля (taken/swept/mitigated/invalidated) → UI не може прибрати “відпрацьоване”.
8) Перемикання TF без канонічного `origin_time` (не кратний TF) → truth gate відсікає.
9) Рестарт broadcaster → cache.close_step=0 → newborn gating дає “порожньо” перші кроки.
10) Змішані назви типів pools (legacy/варіанти) → нормалізація в UI може не зловити.

## Де “патчити”, якщо треба (тільки карта місць)

- Truth (SMC-core): `smc_core/config.py`, `smc_liquidity/*`, `smc_zones/*`, `smc_structure/*`, `smc_execution/*`.
- Presentation (Python): `UI_v2/viewer_state_builder.py`.
- UI gates/рендер: `UI_v2/web_client/app.js`, `UI_v2/web_client/chart_adapter.js`.
