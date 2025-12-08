# OB_v1 + event_history — статус на 2025-12-07

## Тести

- `python -m pytest tests/test_smc_orderblock_basic.py -vv`
- Покрито сценарії: базові LONG/SHORT, ролі PRIMARY/COUNTERTREND, вимога BOS, ремап копій ніг та нові кейси `test_orderblock_uses_event_history` / `test_orderblock_expires_with_ttl`.

## QA 5m (tools/run_smc_5m_qa.py)

- Дані: останні 500 закритих 5m барів XAUUSD/XAGUSD/EURUSD зі `datastore/`.
- Звіт: `reports/smc_qa_5m_summary.json`.
- Результати:
  - XAUUSD — 0 зон: на поточних MAJOR-порогах немає свіжого BOS у межах 7-денного TTL; це очікувано й підтверджує, що 1m шум не створює OB.
  - XAGUSD — 2 PRIMARY SHORT зони зі старінням 835–1035 хвилин; обидва break-и ще всередині TTL.
  - EURUSD — 0 зон: за останні 500 барів відсутні BOS, що проходять `ob_leg_min_atr_mul`; підтверджує інерційність профілю.
- Distance QA (2025-12-07):
  - `ob_max_active_distance_atr = 2.0` → `active_zones_total = 0`, обидві XAG PRIMARY SHORT зони відсічені (max distance ≈13.3 ATR).
  - `ob_max_active_distance_atr = 3.5` → без змін (досі відсікаються, бо поріг менший за 13.3 ATR).
  - `ob_max_active_distance_atr = 15.0` → `active_zones_total = 2`, фільтр перестає впливати і залишає запас до зафіксованого максимуму.

## Зафіксований конфіг

- `structure_event_history_max_minutes = 10080`, `structure_event_history_max_entries = 500`.
- `ob_leg_min_atr_mul = 0.8`, `ob_leg_max_bars = 40`, `ob_prelude_max_bars = 6`.
- `ob_body_domination_pct = 0.65`, `ob_body_min_pct = 0.25`.
- `ob_max_active_distance_atr = 15.0` (distance-фільтр активних зон; будь-яка зміна потребує нового QA).
- Будь-яка зміна вимагає нового QA й рішення Stage4.

## Висновок

- Event history працює як довга пам'ять BOS/CHOCH: тести відновлюють OB із попереднього снапшота та перевіряють TTL-експірацію.
- 5m QA підтвердило, що MAJOR-пороги дають обмежену кількість PRIMARY зон (XAG), а XAU/EUR залишаються «тихими» без свіжого break — це відповідає вимозі не занижувати пороги.
- Golden-кейси для BOS/CHOCH зафіксовані на зрізах `smc_xau_5m_2000bars_10_14` та `smc_xau_5m_2000bars_17_21`: 14.11 містить 3 BOS SHORT, а 21.11 — 2 BOS SHORT і 1 CHOCH LONG, що описано в `docs/smc_structure_stage2.md`.
- Distance-фільтр: новий параметр `ob_max_active_distance_atr` (зафіксований на 15 ATR) відсікає OB з `active_zones`, якщо їхній центр далі від останнього close, ніж задана кількість ATR; при None поведінка повністю збігається з freeze-профілем OB_v1.
- Далі: за потреби додати price-distance фільтр для активації зон та перейти до FVG/Breaker/POI на основі цього статусу.
