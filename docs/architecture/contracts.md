# Контракти (Contract-first)

## Навіщо

Payload між модулями має бути описаний як контракт: **TypedDict** або **dataclass** + поле `schema_version`.
Це дозволяє змінювати payload еволюційно та backward-compatible.

## Базова форма (Envelope)

Базовий конверт описаний у `core/contracts/base.py`:

- `schema_version: str` — версія схеми (наприклад `core.contracts.v1`)
- `payload_ts_ms: int` — час формування payload в UTC (мілісекунди)
- `payload: dict[str, Any]` — payload у JSON-friendly вигляді

## Версіонування

- Будь-яка зміна payload, яка може вплинути на консюмерів, робиться через нову `schema_version`.
- Старі поля не видаляємо одразу: спочатку додаємо нові, підтримуємо обидва, потім видаляємо після міграції.

## Канон для UI SMC-state

Для UI SMC payload (`meta.schema_version`) канонічним значенням є:

- Канон: `smc_state_v1`
- Legacy alias: `1.2`

Правило міграції:

- Масово емісію не змінюємо.
- Консюмери приймають і канон, і legacy alias.
- Для уніфікації в консюмерах дозволена нормалізація `1.2` → `smc_state_v1`.

SSOT-хелпери для цього живуть у `core/contracts/smc_state.py`.

## Де живуть доменні контракти

- Базові спільні форми — у `core/contracts/*`.
- Доменні контракти (наприклад SMC-state для UI) — у відповідних пакетах, але бажано з явним `schema_version`.
