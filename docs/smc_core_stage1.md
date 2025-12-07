# SMC-core • Етап 1 (каркас + API)

**Статус:** завершено 2025-12-06. Цей документ фіксує фактичний стан реалізації першого етапу, який створює незалежний SMC API без втручання у Stage2/Stage3.

## 1. Мета та обмеження

- Винести SMC у власні пакети (`smc_core/*`, `smc_structure/*`) із чітким API.
- Працювати лише в QA/backtest режимах; бойовий Stage1/Stage2/Stage3 не змінюється.
- Зафіксувати типи/enum'и та сигнатуру `SmcCoreEngine.process_snapshot(...)`, які витримають подальші етапи (structure/liquidity/zones/fusion).
- Не додавати реальну детекцію OB/FVG тощо (вони з'являються на наступних етапах), але забезпечити порожні стани й заглушки.

## 2. Структура каталогів

- `smc_core/`
  - `config.py` — датаклас `SmcCoreConfig` і константа `SMC_CORE_CONFIG` (read-only пороги: `min_swing_bars`, `min_range_bars`, `eq_tolerance_pct`, `ote_min/max`, ліміти для OB тощо).
  - `engine.py` — клас `SmcCoreEngine`; метод `process_snapshot(snapshot: SmcInput) -> SmcHint` послідовно викликає `smc_structure`, `smc_liquidity`, `smc_zones` та збирає `SmcHint` з порожнім `signals`.
  - Додаткові службові модулі (наприклад, `input_adapter.py`, `serializers.py`, `liquidity_bridge.py`) уже існують і не змінюють контракт Stage3.
- `smc_structure/`
  - Наразі містить базову реалізацію (swings → legs → trend → BOS/CHOCH → range → OTE) і служить єдиною залежністю Stage1, але для Етапу 1 їх можна було б залишити заглушками; фактично реалізовані обчислення сумісні з API каркаса.

## 3. Типи та контракти (`smc_core/smc_types.py`)

- **Вхід:** `SmcInput` (symbol, `tf_primary`, `ohlc_by_tf: dict[str, pd.DataFrame]`, `context: dict[str, Any]` для HTF трендів, whale метрик, PDH/PDL/PDN тощо).
- **Стани:** `SmcStructureState`, `SmcLiquidityState`, `SmcZonesState` (усі dataclass з `meta` для телеметрії).
- **Зони та POI:** `SmcZone`, `SmcPoi` з ID, ціновими межами, `entry_mode`, `bias_at_creation`.
- **Сигнали:** `SmcSignal` (direction, `SmcSignalType`, confidence, посилання на POI).
- **Агрегат:** `SmcHint` (structure/liquidity/zones/signals/meta). `signals` поки завжди порожній, але поле існує.
- **Enum'и:**
  - `SmcTrend = {UP, DOWN, RANGE, UNKNOWN}`
  - `SmcRangeState = {NONE, INSIDE, DEV_UP, DEV_DOWN}`
  - `SmcZoneType = {ORDER_BLOCK, BREAKER, IMBALANCE, POI, FTA, OTHER}`
  - `SmcLiquidityType = {EQH, EQL, TLQ, SLQ, SFP, WICK}`
  - `SmcSignalType = {CONTINUATION, REVERSAL, REVERSION, SCALP, OTHER}`

Контракт `SmcCoreEngine.process_snapshot` вже використовує ці типи, тож наступні етапи можуть розширювати внутрішню логіку без зміни сигнатури чи структури `SmcHint`.

## 4. Конфігурація

- Файл `config/config.py` містить прапор `SMC_BACKTEST_ENABLED` (дефолт `False`).
- `tools/smc_snapshot_runner.py` читає прапор і відмовляється працювати без `--force` при вимкненому режимі.
- Ніякого мутабельного state усередині `SmcCoreConfig`: виключно константи, що забезпечують детермінізм QA.

## 5. Інтеграція у backtest/QA

- Раннер `tools/smc_snapshot_runner.py` формує `SmcInput` через `build_smc_input_from_store(...)`, створює `SmcCoreEngine` і викликає `process_snapshot` для історичного зрізу. Результат лише друкується/логиться; Stage2/Stage3 не читають його.
- Будь-яка інша QA-логіка може орієнтуватися на той же підхід: один engine на процес + виклик на кожен snapshot.

## 6. Тестове покриття Етапу 1

- `tests/test_smc_types.py` — smoke на всі ключові dataclass'и та enum'и.
- `tests/test_smc_core_contracts.py` — гарантує, що `SmcCoreEngine.process_snapshot` повертає валідний `SmcHint` на мінімальних OHLC; перевіряє наявність структури, ліквідності, зон і meta (`last_price`).

## 7. Негативні рамки (те, чого немає на Етапі 1)

- Жодних реальних алгоритмів HH/LL, OB, FVG, SFP (окрім вже існуючої базової структури — вона сумісна, але не обов'язкова для API).
- Жодних змін Stage1/Stage2/Stage3 контрактів чи UI payload.
- Жодних Prometheus метрик або додаткових каналів Stage1.

## 8. Подальші кроки

- Етап 2+ наповнюють уже зафіксовані контракти: structure → liquidity → zones → fusion.
- Коли з'являться справжні сигнали, поле `SmcHint.signals` готове приймати їх без API break.
- `SMC_BACKTEST_ENABLED` залишаємо головним вимикачем доти, доки не буде готова інтеграція Stage2/Stage3.
