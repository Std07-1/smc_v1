"""Заглушка агрегації зон у POI / FTA."""

from __future__ import annotations

from smc_core.smc_types import SmcPoi, SmcZone


def build_poi_candidates(zones: list[SmcZone]) -> list[SmcPoi]:
    """Поки що повертаємо порожній список, фіксуючи точку розширення API."""

    _ = zones
    return []
