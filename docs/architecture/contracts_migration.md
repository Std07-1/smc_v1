# Міграція контрактів (Contract-first) • план

Дата старту: 2025-12-16

> ⚠️ **Статус (2025-12-18):** план завершено. `core/contracts/compat.py`, `UI_v2/schemas.py` та `data/fxcm_schema.py` видалені (E3/E4). Канонічні контракти імпортуємо тільки з `core/contracts/*`.

## Актуально зараз (канонічні імпорти)

- FXCM TypedDict контракти + назви каналів: `core/contracts/fxcm_channels.py`
- FXCM soft-validate: `core/contracts/fxcm_validate.py`
- FXCM telemetry (pydantic): `core/contracts/fxcm_telemetry.py`
- Viewer/UI TypedDict: `core/contracts/viewer_state.py`

Цей документ описує поетапну міграцію контрактів (schemas/payload) до SSOT, без лому існуючих консюмерів.

## Контекст

Сьогодні контракти розподілені між:

- (історично) `data/fxcm_schema.py` (TypedDict + легка валідація hot path) — **видалено**
- `data/fxcm_models.py` (pydantic-моделі телеметрії)
- (історично) `UI_v2/schemas.py` (TypedDict для UI viewer та Redis payload) — **видалено**
- `UI/publish_smc_state.py` (продюсер SMC-only payload як dict)
- `smc_core/smc_types.py` + `smc_core/serializers.py` (внутрішній канон + plain JSON)

Ціль SSOT: мати **1 місце** для імпорту типів/контрактів і контрольовану еволюцію `schema_version`.

## Хвилі

## C1 (compat layer, без лому)

(Історично) додавали `core/contracts/compat.py` — набір **тимчасових alias'ів** до існуючих типів.

- Новий код (або тести) може імпортувати типи так:
  - `from core.contracts.compat import UiSmcStatePayload, FxcmOhlcvMessage, SmcViewerState`
- Старий код не чіпаємо.

Примітка:

- У `compat.py` імпорти зроблені як best-effort: якщо модуль недоступний — type деградує до `Any`.
- Це дозволяє уникати runtime-ломів у випадку часткових переносів.

## C2 (канонізація)

Мета: перенести **канонічні** контракти в стабільне SSOT місце, без залежності `core/*` від UI/data.

Варіанти (узгодити окремо):

- Якщо контракт є truly cross-module (SMC↔UI, FXCM↔SMC): `core/contracts/*`.
- Якщо контракт є внутрішнім для SMC-core: `smc_core/contracts/*` (або `smc_core/smc_types.py` + явні plain-схеми).

Після переносу:

- старі модулі лишаються як thin wrappers (deprecated) або re-export, щоб не ламати імпорти.

## C3 (cleanup)

Після того, як:

- усі консюмери перейшли на канонічні імпорти;
- `schema_version` вирівняний і описаний у docs;

…можна видаляти:

- (вже зроблено) `core/contracts/compat.py`
- застарілі дублікати схем/назв у старих модулях (тільки після grep по репо).

## Мапа імен (C1)

UI_v2:

- `UI_v2.schemas.SmcHintPlain` → `core.contracts.compat.SmcHintPlain`
- `UI_v2.schemas.UiSmcStatePayload` → `core.contracts.compat.UiSmcStatePayload`
- `UI_v2.schemas.SmcViewerState` → `core.contracts.compat.SmcViewerState`

FXCM TypedDict:

- `data.fxcm_schema.FxcmOhlcvMessage` → `core.contracts.compat.FxcmOhlcvMessage`
- `data.fxcm_schema.FxcmPriceTickMessage` → `core.contracts.compat.FxcmPriceTickMessage`
- `data.fxcm_schema.FxcmAggregatedStatusMessage` → `core.contracts.compat.FxcmAggregatedStatusMessage`

FXCM pydantic:

- `data.fxcm_models.FxcmHeartbeat` → `core.contracts.compat.FxcmHeartbeat`
- `data.fxcm_models.FxcmMarketStatus` → `core.contracts.compat.FxcmMarketStatus`
- `data.fxcm_models.FxcmAggregatedStatus` → `core.contracts.compat.FxcmAggregatedStatus`

## Рішення (C2): канон schema_version для UI SMC payload

Канонічне значення `meta.schema_version` для UI SMC payload:

- Канон: `smc_state_v1`
- Legacy alias: `1.2`

Правило:

- Емісію не змінюємо масово.
- Консюмери приймають обидва значення.
- За потреби консюмери нормалізують `1.2` → `smc_state_v1`.

SSOT-реалізація:

- `core/contracts/smc_state.py`: `normalize_smc_schema_version()` та `is_supported_smc_schema_version()`.
