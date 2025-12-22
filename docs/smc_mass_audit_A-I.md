# Масивний аудит SMC (кейси A–I) • v0.1 • 2025-12-21

Цей документ формалізує «проходи» аудиту як: **гіпотеза → метрика/зріз → критерій успіху → який патч/де правити**.

## Загальні правила вимірювань (SSOT)

**Джерело правди:** journal + frames, які генеруються реплеєм.

1) Записати журнал (events+frames):

- `; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" -m tools.replay_snapshot_to_viewer --path xauusd_bars_5m_snapshot.jsonl --limit 1200 --window 1200 --sleep-ms 0 --tv-like --with-preview --journal-dir reports/smc_journal_p0_runX`

2) Згенерувати звіт + CSV:

- `; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" -m tools.smc_journal_report --dir reports/smc_journal_p0_runX --symbol XAUUSD --csv-dir reports/smc_journal_p0_runX/_csv | Out-File -Encoding utf8 reports/smc_journal_p0_runX/report_XAUUSD.md`

**Порівняння до/після:** робимо два прогони `run_before` та `run_after` з однаковими `--path/--limit/--window/--tv-like/--with-preview`.

**Базові CSV, на які спираємося:**

- `_csv/created_per_hour.csv`
- `_csv/removed_reason_sub.csv`
- `_csv/quality_matrix.csv`
- (frames) `preview_vs_close_summary` в markdown-звіті

## Кейс A — Шум: надто багато формувань (pools/zones)

### Гіпотеза

`pool`/`WICK_CLUSTER` churn зумовлений перерахунком «вікнами» (rebucket), а preview створює додатковий шум.

### Що міряти (до/після)

1) **created_per_hour (піки)**

- Дані: `created_per_hour.csv`
- Розрізи: `entity=pool`, додатково `type=WICK_CLUSTER`, `compute_kind=preview|close`.
- Інтерпретація: піки «сотні pool/год» = шум/перерахунок, а не нові значущі рівні.

2) **removed_reason_sub частки**

- Дані: `removed_reason_sub.csv` (або секція `removed_reason_sub` у markdown)
- Цільові підпричини: `rebucket_time_window`, `flicker_short_lived`.

3) **touch ratio**

- Дані: `quality_matrix.csv` (або похідне)
- Сигнал проблеми: `touched/created` низький при високому created.

### Критерій успіху (чернетка)

- `created_per_hour(pool, close)` зменшується в рази (і зникають години з «сотнями»).
- Частка removed `rebucket_time_window + flicker_short_lived` падає суттєво (наприклад, < 40% сумарно).
- `touched_rate(pool)` росте (напр. з ~8% до >15–20%).

### Що міняти (важелі) і де

1) **Preview ≠ truth**

- Не публікувати `SFP/WICK_CLUSTER` на preview (close-only).
- Реалізовано: `SmcCoreConfig.liquidity_preview_include_sfp_and_wicks=False` + передача `smc_compute_kind`.

2) **Top‑K / cap по типах**

- WICK_CLUSTER top‑K на бік (наприклад 2–3), SFP top‑K (3–6), EQ top‑K (12).
- Реалізовано: cap-и в `SmcCoreConfig` + `throttle_pools()`.

3) **Hysteresis/min_age (ще не зроблено)**

- Pool має прожити ≥2–3 **close** барів перед тим, як потрапити в active.
- Варіанти реалізації:
  - (A) у `lifecycle_journal`: “pending_created” до досягнення віку (потім `created_confirmed`),
  - (B) у `smc_liquidity`: маркувати `meta.pending=True` і фільтрувати в UI/паблішері.

## Кейс B — Preview lifecycle: removed на preview не є «правдою»

### Гіпотеза

Preview дає тимчасові зникнення/появи; якщо фіналити `removed` на preview — журнал та UI «брешуть» і створюють уявний churn.

### Що міряти

- `removed` на `compute_kind=preview` (має прагнути до нуля для фінальних подій).
- `preview_vs_close_summary` по сутностях (особливо pool): Jaccard mean має зрости.

### Критерій успіху

- `removed(preview)` для core сутностей = 0 (або лише debug-типи, якщо ми їх лишаємо).
- Jaccard(pool) росте помітно.

### Що міняти

- Реалізовано: `SmcLifecycleJournal` не емiтить removed на preview.
- Далі (опційно): grace/confirmation на close для нестабільних сутностей.

## Кейс C — Дублікати/overlap (композитинг) у zones

### Гіпотеза

Zones можуть дублюватись/накладатись (майже однакові діапазони), що створює «павутину» у UI.

### Що міряти

- У `quality_matrix`: багато `created` для `zone` при низькому `touched`.
- У frames: велика різниця active_ids між близькими моментами при малому русі ціни.

### Критерій успіху

- Зменшення `created(zone)` без втрати покриття (touched не падає).

### Що міняти

- Overlap-merge політика для зон (одна зона «перемагає», інші йдуть у merged/archived).

## Кейс D — Touch correctness: missed touches (offлайн валідатор)

### Гіпотеза

Частина touched пропускається через неточні правила перетину (level/zone), різні TF, або edge-case з close/open.

### Що міряти

- `touched_late` (після removed) як сигнал помилкових removed або touch detection.
- Окремий валідатор: прогнати бари і перевірити, чи перетинав бар рівень/діапазон, коли об’єкт був active.

### Критерій успіху

- `touched_late` рідкісний і пояснюваний.
- `touch_missed_rate` прагне до нуля на контрольних ділянках.

### Що міняти

- Формалізувати validator на основі frames (active_ids) + OHLCV.

## Кейс E — Ширина зон (OB/FVG): надто широкі діапазони (Випадок D)

### Гіпотеза

OB/FVG можуть інколи будуватись надто широкими, з’їдаючи читабельність і даючи FP.

### Що міряти

- `wide_zone_rate(span_atr)` у `report_XAUUSD` (після кожного патчу).
- Новий зріз: `span_atr_vs_outcomes(touched/mitigated)` у `report_XAUUSD`.
  Це дає кореляцію/бінінг: чи «надширокі» зони частіше/рідше стають touched/mitigated.

### Критерій успіху

- Відсікання хвоста «надшироких» (cap/деградація), без падіння корисних touched.

### Що міняти

- Ввести `max_zone_span_atr`: якщо `span_atr` вище порогу — не пускаємо в `active_zones/POI` (скоріше range/area).
- Для великих зон: даунвейти/відсіювання з top‑K active, щоб не забивали UI.

## Кейс F — Cap eviction: чи ми “викидаємо важливе”

### Гіпотеза

`cap_evicted` видаляє корисні об’єкти, що потім проявляється як `touched_late` або падіння coverage.

### Що міряти

- Частка `removed_reason=evicted_cap` і кореляція з `touched_late`.
- Динаміка `poi_dropped_due_cap` (вже прокидується в ctx у journal).

### Критерій успіху

- `cap_evicted` мінімальний і не б’є по touched.

### Що міняти

- Розумніший пріоритет при cap (не лише “перші N”), або окремі cap-и per type.

## Кейс G — Context flip: churn через bias/range_state

### Гіпотеза

Частина removed відбувається через `context_flip` (bias/range_state), і це може бути або правильна реакція, або занадто чутливо.

### Що міряти

- Частка `reason_sub=context_flip` у removed_reason_sub.
- Розріз по сутностях: чи найбільше страждають pool чи zones.

### Критерій успіху

- `context_flip` лишається, але не домінує у шумних сутностях.

### Що міняти

- Hysteresis для bias/range_state (якщо підтвердиться надчутливість).

## Кейс H — Live/offline parity: однакові входи → однакова поведінка

### Гіпотеза

Роз’їзд live/offline дає “хаос” оверлеїв через різний multi‑TF/session context.

### Що міряти

- Дельта frames при однаковому time window.
- Контрольні поля в meta: наявність `smc_sessions`, HTF frames.

### Критерій успіху

- Реплей відтворює live (в межах допустимих розбіжностей).

### Що міняти

- Уніфікований input adapter (SSOT) + явні гейти.

## Кейс I — Латентність/вартість: “гарячий шлях” не деградує

### Гіпотеза

Зменшення шуму не повинно збільшити compute-time або зробити поведінку недетермінованою.

### Що міряти

- p50/p95 compute_ms (smoke tool, якщо увімкнено), або простий таймінг реплею.
- Розмір payload (кількість об’єктів у state).

### Критерій успіху

- Payload компактніший; compute не гірший.

---

## Примітка про статус реалізації

- Для кейсу A/B частина важелів уже зроблена (throttling + close-only preview extras + preview removed suppression).
- Hysteresis/min_age (A) та overlap-merge/touch-validator (C/D) — наступні хвилі.
