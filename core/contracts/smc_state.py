"""Канонічна версія schema_version для UI SMC payload.

Ціль хвилі C2: зафіксувати SSOT для `meta.schema_version` у SMC-state payload,
не змінюючи форму payload та не ламаючи існуючих консюмерів.

Правило:
- Канон: `smc_state_v1`.
- Legacy alias'и (історичні значення) приймаємо на вході, але
  консюмери можуть нормалізувати їх до канону.

Чому саме так:
- Емісія в продюсерах може залишатися legacy (наприклад "1.2") ще певний час.
- Консюмери мають приймати обидва значення без exception та без зміни полів.
"""

from __future__ import annotations

# ── Versioning (SSOT) ─────────────────────────────────────────────────────

SMC_STATE_SCHEMA_VERSION: str = "smc_state_v1"

# Legacy значення, які історично зустрічаються в емісії/конфігах.
SMC_STATE_SCHEMA_VERSION_ALIASES: set[str] = {"1.2"}


# ── Public helpers ───────────────────────────────────────────────────────


def normalize_smc_schema_version(value: str) -> str:
    """Нормалізує schema_version до канонічного значення.

    Поведінка:
    - якщо `value` вже канон → повертаємо канон;
    - якщо `value` є legacy alias → повертаємо канон;
    - інакше → повертаємо `value` без exception.

    Важливо: ця функція не робить валідацію та не кидає помилок —
    вона призначена для безпечної backward-compatible міграції.
    """

    if value == SMC_STATE_SCHEMA_VERSION:
        return SMC_STATE_SCHEMA_VERSION

    if value in SMC_STATE_SCHEMA_VERSION_ALIASES:
        return SMC_STATE_SCHEMA_VERSION

    return value


def is_supported_smc_schema_version(value: str) -> bool:
    """Перевіряє, чи schema_version підтримується як SMC-state."""

    return (
        value == SMC_STATE_SCHEMA_VERSION or value in SMC_STATE_SCHEMA_VERSION_ALIASES
    )
