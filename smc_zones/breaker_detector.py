"""Заглушковий детектор breaker-блоків."""

from __future__ import annotations

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import SmcInput, SmcZone


def detect_breakers(
    snapshot: SmcInput, cfg: SmcCoreConfig, base_zones: list[SmcZone]
) -> list[SmcZone]:
    """Поки що повертає порожній список, фіксуємо лише інтерфейс."""

    _ = (snapshot, cfg, base_zones)
    return []
