"""Юніт-тести каркасу FVG/Imbalance_v1."""

from __future__ import annotations

import pandas as pd
import pytest

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import SmcStructureState
from smc_zones.fvg_detector import detect_fvg_zones


def test_detects_bullish_fvg_gap() -> None:
    bars = [
        {"high": 100.0, "low": 99.2, "timestamp": pd.Timestamp("2025-01-01T00:00:00Z")},
        {
            "high": 101.0,
            "low": 100.1,
            "timestamp": pd.Timestamp("2025-01-01T00:05:00Z"),
        },
        {
            "high": 103.0,
            "low": 102.7,
            "timestamp": pd.Timestamp("2025-01-01T00:10:00Z"),
        },
    ]
    structure = SmcStructureState(
        primary_tf="5m",
        bias="LONG",  # type: ignore[arg-type]
        meta={"primary_bars": bars, "atr_last": 1.0},
    )
    cfg = SmcCoreConfig()

    zones = detect_fvg_zones(structure, cfg)

    assert len(zones) == 1
    zone = zones[0]
    assert zone.direction == "LONG"
    assert zone.role == "PRIMARY"
    assert zone.price_min == pytest.approx(100.0)
    assert zone.price_max == pytest.approx(102.7)
    assert zone.meta["gap"] == pytest.approx(2.7)


def test_detects_bearish_fvg_gap() -> None:
    bars = [
        {
            "high": 205.0,
            "low": 204.5,
            "timestamp": pd.Timestamp("2025-01-02T00:00:00Z"),
        },
        {
            "high": 204.8,
            "low": 203.9,
            "timestamp": pd.Timestamp("2025-01-02T00:05:00Z"),
        },
        {
            "high": 203.2,
            "low": 202.1,
            "timestamp": pd.Timestamp("2025-01-02T00:10:00Z"),
        },
    ]
    structure = SmcStructureState(
        primary_tf="5m",
        bias="LONG",  # type: ignore[arg-type]
        meta={"primary_bars": bars, "atr_last": 0.8},
    )
    cfg = SmcCoreConfig()

    zones = detect_fvg_zones(structure, cfg)

    assert len(zones) == 1
    zone = zones[0]
    assert zone.direction == "SHORT"
    # bias LONG, тож роль повинна бути COUNTERTREND
    assert zone.role == "COUNTERTREND"
    assert zone.price_min == pytest.approx(203.2)
    assert zone.price_max == pytest.approx(204.5)


def test_skip_small_gap_when_threshold_not_met() -> None:
    bars = [
        {
            "high": 300.0,
            "low": 299.0,
            "timestamp": pd.Timestamp("2025-01-03T00:00:00Z"),
        },
        {
            "high": 299.8,
            "low": 298.9,
            "timestamp": pd.Timestamp("2025-01-03T00:05:00Z"),
        },
        {
            "high": 299.6,
            "low": 298.8,
            "timestamp": pd.Timestamp("2025-01-03T00:10:00Z"),
        },
    ]
    structure = SmcStructureState(
        primary_tf="5m",
        bias="NEUTRAL",  # type: ignore[arg-type]
        meta={"primary_bars": bars, "atr_last": 2.0},
    )
    cfg = SmcCoreConfig()

    zones = detect_fvg_zones(structure, cfg)

    assert zones == []
