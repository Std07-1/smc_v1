# Мінімальний DoD для Stage6 після P0–P1 (2025-12-21)

Цей DoD — **мінімальний**, щоб підтвердити, що Stage6 придатний для трейдерського використання: raw детермінований, CONFLICT знижується логікою (P0), фліпи контролюються анти-фліпом (P1), а в payload/QA видно «чому».

## 1) Raw детермінований (SSOT)

Вимога:

- Однаковий snapshot → однаковий raw (`scenario_id/direction/confidence/why/key_levels/telemetry`).

Перевірка:

- Тест: `tests/test_smc_stage6_scenario.py::test_stage6_raw_is_deterministic_and_key_levels_present`.

Команда:

- `; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" -m pytest -q tests/test_smc_stage6_scenario.py -k deterministic`

## 2) `key_levels` заповнені для валідного рішення

Вимога:

- Для рішення `4_2/4_3` ключові рівні присутні (мінімум: `range_high/range_low/hold_level_up/hold_level_dn`).

Перевірка:

- Той самий тест вище (assert на наявність ключів, якщо сценарій не `UNCLEAR`).

## 3) CONFLICT падає суттєво (P0)

Вимога:

- `CONFLICT` знижується не «порогами», а взаємовиключною логікою `hold_above` vs `failed_hold`.

Перевірка (QA):

- Порівняти новий репорт із попереднім (та сама команда/параметри), дивитись:
  - `unclear_reason_counts['CONFLICT']`
  - `raw UNCLEAR rate` та `stable UNCLEAR rate`

Команда (приклад XAUUSD):

- `; function с { } ; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" -m tools.qa_stage6_scenario_stats --path datastore/xauusd_bars_5m_snapshot.jsonl --steps 500 --warmup 220 --horizon-bars 60 --tp-atr 1.0 --sl-atr 1.0 --out reports/stage6_stats_xauusd_h60_v4.md`

## 4) Flips різко менше, але 4_2→4_3 не затиснутий TTL (P1)

Вимога:

- Flips загалом нижчі (за рахунок pending/confirm/TTL).
- Водночас `4_2→4_3` може пройти навіть при активному TTL, якщо є `hard_invalidation:*`.

Перевірка:

- Юніт-тести:
  - `tests/test_smc_stage6_hysteresis.py::test_stage6_hard_invalidation_can_bypass_ttl_42_to_43_hold_above`
  - `tests/test_smc_stage6_hysteresis.py::test_stage6_hard_invalidation_43_to_unclear_on_bos_down_no_failed_hold`

Команда:

- `; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" -m pytest -q tests/test_smc_stage6_hysteresis.py`

QA-видимість:

- У репорті `tools/qa_stage6_scenario_stats` дивитись секції:
  - `Flips` → `flip_pairs`, `flip_reasons`
  - `Приклади (exemplars)` → `flip_reason=hard_invalidation:*`

## 5) Payload прозорий для UI

Вимога:

- У payload видно:
  - raw: `raw_why/key_levels/gates/unclear_reason`.
  - stable: `scenario_id/confidence`, `pending`, `ttl`, `flip.reason`.

Перевірка:

- Візуально у UI_v2 (offline) або у репорті QA (exemplars), де друкуються:
  - `raw_why`, `raw_key_levels`, `gates`, `stable pending`, `flip_reason`.

## 6) "Чому" видно в QA для успіхів і провалів

Вимога:

- У QA репорті є конкретні кейси (exemplars) для:
  - flips,
  - raw/stable `UNCLEAR`.

Перевірка:

- `tools/qa_stage6_scenario_stats` генерує секцію `Приклади (exemplars)`.
- Для кожного exemplar видно `raw_why` + `raw_key_levels` + `gates/reason` + `flip_reason/pending`.
