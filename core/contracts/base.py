"""Базові контракти (Contract-first) для payload між шарами.

Це мінімальний каркас для версіонування та обгортки payload.
Нічого доменного тут не додаємо: лише спільна форма, яку можуть
використовувати Stage*/UI/інтеграції.
"""

from __future__ import annotations

# ── Imports ───────────────────────────────────────────────────────────────
from typing import Any, TypedDict

# ── Versioning ────────────────────────────────────────────────────────────

SCHEMA_VERSION: str = "core.contracts.v1"


class SchemaVersioned(TypedDict):
    """Мінімальний міксин для payload з версією схеми."""

    schema_version: str


class Envelope(TypedDict):
    """Базовий конверт для передачі payload між модулями.

    Поля:
    - schema_version: версія контракту (напр. `core.contracts.v1`);
    - payload_ts_ms: час формування payload (UTC, мс);
    - payload: сам payload як JSON-friendly dict.

    Чому так:
    - schema_version дозволяє робити backward-compatible еволюцію;
    - payload_ts_ms — універсальний часовий індекс для дебагу/метрик.
    """

    schema_version: str
    payload_ts_ms: int
    payload: dict[str, Any]
