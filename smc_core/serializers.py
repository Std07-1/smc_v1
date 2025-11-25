"""Серіалізатори SMC-core для plain JSON представлення."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from smc_core.smc_types import SmcHint


def to_plain_smc_hint(hint: SmcHint | None) -> dict[str, Any] | None:
    """Конвертує SmcHint у JSON-friendly dict (рекурсивно)."""

    if hint is None:
        return None
    plain = _to_plain_value(hint)
    return plain if isinstance(plain, dict) else {"value": plain}


def _to_plain_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.name
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _to_plain_value(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: _to_plain_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_plain_value(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, type):
        return getattr(value, "__name__", str(value))
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return str(value)
