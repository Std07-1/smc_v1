# Огляд SMC-core

Документ фіксує актуальний стан ядра SMC після етапу «structure + liquidity + SFP/Wick + AMD».
Мета — пояснити роль модуля, формат API та взаємодію з Stage1/UI/Stage2.

## Роль та межі шару

- **SmcCoreEngine** — єдиний вхід для Stage1 та QA-утиліт. Він приймає `SmcInput`,
  послідовно викликає `smc_structure`, `smc_liquidity`, `smc_zones` і повертає `SmcHint`.
- **SmcInput** збирається виключно через `smc_core.input_adapter.build_smc_input_from_store`.
  Джерело даних — `UnifiedDataStore`, тому ядро не виконує зовнішніх I/O-запитів.
- **SmcHint** — стабільний контракт виходу. Нові поля додаємо через `meta` або через
  додаткові state-блоки, не ламаючи існуючу схему.

## Потік даних

1. **Stage1** працює із WS-даними й підтримує `AssetStateManager`. Коли потрібен
   SMC-контекст, Stage1 читає історію з `UnifiedDataStore` та формує `SmcInput`.
2. **SMC-core** виконує pipeline swings → range → liquidity → magnets → AMD. Результат
   зберігається в `SmcHint` і може бути доданий до стану активу (`asset["smc_hint"]`).
3. **UI** читає готовий блок через `_to_plain_smc_liquidity`, без повторних розрахунків.
4. **Stage2/Stage3** поки не приймають рішення на основі SMC, але мають міст
   `smc_core.liquidity_bridge.build_liquidity_hint` (прапори primary-liq, відстань,
   AMD-фаза).

## API, що вважаються стабільними

- `SmcCoreEngine.process_snapshot(snapshot: SmcInput) -> SmcHint`.
- Структура `SmcHint` (`structure`, `liquidity`, `zones`, `signals`, `meta`).
- `SmcStructureState`, `SmcLiquidityState` (з `amd_phase`), `SmcLiquidityPool`,
  `SmcLiquidityMagnet`, `SmcAmdPhase`.
- `smc_core.liquidity_bridge.build_liquidity_hint` — офіційний шлях отримати
  Stage2-friendly телеметрію.

Будь-які зміни цих контрактів потребують окремого плану та документації (див. оновлену
`copilot-memory`).

## Супутні утиліти

- `tools/smc_snapshot_runner.py` — CLI для локального запуску SMC-core на історичних
  даних.
- `docs/smc_structure.md`, `docs/smc_liquidity.md` — деталізують алгоритми
  всередині підмодулів.

## Plain JSON контракт (AssetStateManager snapshot)

- Stage1 (`app/screening_producer`) серіалізує `SmcHint` через
  `smc_core.serializers.to_plain_smc_hint`, тому у `asset["smc_hint"]` зберігається
  вже JSON-friendly dict без dataclass/Enum обʼєктів.
- Той самий dict дублюється в `asset["smc"]`, `smc_structure`, `smc_liquidity`,
  `smc_zones` (за потреби). Навіть без UI-шару будь-який консюмер може взяти
  `state_manager.state` та відправити його у Redis/REST без додаткової обробки.
- Схема 1.2 гарантує, що `Enum` → `.name`, `datetime` → `isoformat()`, множини /
  tuple → списки, а інші типи переводяться у рядки. Це дозволяє зберігати snapshot
  у будь-якому транспорті без повторного серіалізатора.

Приклад останнього стейту активу в Redis (зріз полів):

```json
{
  "symbol": "xauusd",
  "smc_hint": {
    "structure": {
      "bias": "LONG",
      "ote_zones": [
        {"direction": "LONG", "role": "PRIMARY", "ote_min": 2367.2, "ote_max": 2372.5}
      ],
      "meta": {"last_choch_ts": "2025-11-24T14:35:00+00:00"}
    },
    "liquidity": {
      "amd_phase": "ACCUMULATION",
      "pools": [
        {"level": 2375.8, "liq_type": "EQH", "role": "PRIMARY", "meta": {"strength": 0.74}}
      ],
      "meta": {"pool_count": 2, "magnet_count": 1}
    },
    "signals": [],
    "meta": {"snapshot_ts": "2025-11-24T14:36:00+00:00"}
  },
  "smc": {
    "structure": {
      "bias": "LONG",
      "ote_zones": [
        {"direction": "LONG", "role": "PRIMARY", "ote_min": 2367.2, "ote_max": 2372.5}
      ],
      "meta": {"last_choch_ts": "2025-11-24T14:35:00+00:00"}
    },
    "liquidity": {
      "amd_phase": "ACCUMULATION",
      "pools": [
        {"level": 2375.8, "liq_type": "EQH", "role": "PRIMARY", "meta": {"strength": 0.74}}
      ],
      "meta": {"pool_count": 2, "magnet_count": 1}
    },
    "signals": []
  },
  "smc_structure": {"bias": "LONG", "meta": {"last_choch_ts": "2025-11-24T14:35:00+00:00"}},
  "smc_liquidity": {
    "amd_phase": "ACCUMULATION",
    "pools": [
      {"level": 2375.8, "liq_type": "EQH", "role": "PRIMARY", "meta": {"strength": 0.74}}
    ],
    "magnets": [],
    "meta": {"pool_count": 2, "magnet_count": 1}
  },
  "smc_zones": null
}
```

Цей фрагмент описує мінімально необхідний контракт для Stage2. Нові поля можна
додавати у `smc_hint.meta` або підполя state-блоків, не змінюючи наявну структуру.

## Подальші кроки

- Етап 4 (smc_zones + Fusion) стартує лише після фіксації цих контрактів. Нові
  можливості повинні розширювати існуючі структури, не змінюючи семантику полів без
  чіткого плану міграції.
