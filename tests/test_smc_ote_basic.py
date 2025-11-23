"""Перевірки OTE-зон на базових прикладах."""

from __future__ import annotations

import pandas as pd
import pytest

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import SmcStructureLeg, SmcSwing, SmcTrend
from smc_structure import ote_engine


def test_build_ote_zone_for_bullish_leg() -> None:
    swing_low = SmcSwing(
        index=0,
        time=pd.Timestamp("2024-01-01T00:00:00Z"),
        price=100.0,
        kind="LOW",
        strength=2,
    )
    swing_high = SmcSwing(
        index=1,
        time=pd.Timestamp("2024-01-01T01:00:00Z"),
        price=120.0,
        kind="HIGH",
        strength=2,
    )
    leg = SmcStructureLeg(from_swing=swing_low, to_swing=swing_high, label="HH")

    cfg = SmcCoreConfig(
        leg_min_amplitude_atr_m1=0.0,
        bos_min_move_pct_m1=0.0,
        bos_min_move_atr_m1=0.0,
    )
    atr_series = pd.Series([1.0, 1.0])
    zones = ote_engine.build_ote_zones([leg], SmcTrend.UP, cfg, atr_series)

    assert len(zones) == 1
    zone = zones[0]
    assert zone.direction == "LONG"
    assert zone.ote_min == pytest.approx(104.2)
    assert zone.ote_max == pytest.approx(107.6)


def test_build_ote_zone_for_bearish_leg() -> None:
    swing_high = SmcSwing(
        index=10,
        time=pd.Timestamp("2024-01-01T05:00:00Z"),
        price=130.0,
        kind="HIGH",
        strength=3,
    )
    swing_low = SmcSwing(
        index=11,
        time=pd.Timestamp("2024-01-01T06:00:00Z"),
        price=100.0,
        kind="LOW",
        strength=3,
    )
    leg = SmcStructureLeg(from_swing=swing_high, to_swing=swing_low, label="LL")

    cfg = SmcCoreConfig(
        leg_min_amplitude_atr_m1=0.0,
        bos_min_move_pct_m1=0.0,
        bos_min_move_atr_m1=0.0,
    )
    atr_series = pd.Series([1.0] * 12)
    zones = ote_engine.build_ote_zones([leg], SmcTrend.DOWN, cfg, atr_series)

    assert len(zones) == 1
    zone = zones[0]
    assert zone.direction == "SHORT"
    assert zone.ote_min == pytest.approx(118.6)
    assert zone.ote_max == pytest.approx(123.7)


def test_no_short_ote_in_uptrend_when_trend_only() -> None:
    swing_low = SmcSwing(
        index=0,
        time=pd.Timestamp("2024-01-01T00:00:00Z"),
        price=100.0,
        kind="LOW",
        strength=2,
    )
    swing_high = SmcSwing(
        index=1,
        time=pd.Timestamp("2024-01-01T00:30:00Z"),
        price=120.0,
        kind="HIGH",
        strength=2,
    )
    pullback = SmcSwing(
        index=2,
        time=pd.Timestamp("2024-01-01T01:00:00Z"),
        price=112.0,
        kind="LOW",
        strength=2,
    )
    continuation = SmcSwing(
        index=3,
        time=pd.Timestamp("2024-01-01T01:30:00Z"),
        price=130.0,
        kind="HIGH",
        strength=2,
    )
    legs = [
        SmcStructureLeg(from_swing=swing_low, to_swing=swing_high, label="HH"),
        SmcStructureLeg(from_swing=swing_high, to_swing=pullback, label="HL"),
        SmcStructureLeg(from_swing=pullback, to_swing=continuation, label="HH"),
    ]
    cfg = SmcCoreConfig(
        leg_min_amplitude_atr_m1=0.0,
        bos_min_move_pct_m1=0.0,
        bos_min_move_atr_m1=0.0,
        ote_trend_only_m1=True,
        ote_max_active_per_side_m1=1,
    )
    atr_series = pd.Series([1.0] * 4)

    zones = ote_engine.build_ote_zones(legs, SmcTrend.UP, cfg, atr_series)

    assert len(zones) == 1
    assert zones[0].direction == "LONG"


def test_ote_roles_follow_bias() -> None:
    swing_low = SmcSwing(
        index=0,
        time=pd.Timestamp("2024-01-01T00:00:00Z"),
        price=100.0,
        kind="LOW",
        strength=2,
    )
    swing_high = SmcSwing(
        index=1,
        time=pd.Timestamp("2024-01-01T00:30:00Z"),
        price=120.0,
        kind="HIGH",
        strength=2,
    )
    swing_lower_high = SmcSwing(
        index=2,
        time=pd.Timestamp("2024-01-01T01:00:00Z"),
        price=110.0,
        kind="HIGH",
        strength=2,
    )
    swing_lower_low = SmcSwing(
        index=3,
        time=pd.Timestamp("2024-01-01T01:30:00Z"),
        price=95.0,
        kind="LOW",
        strength=2,
    )
    long_leg = SmcStructureLeg(from_swing=swing_low, to_swing=swing_high, label="HH")
    short_leg = SmcStructureLeg(
        from_swing=swing_lower_high, to_swing=swing_lower_low, label="LL"
    )
    cfg = SmcCoreConfig(
        leg_min_amplitude_atr_m1=0.0,
        bos_min_move_pct_m1=0.0,
        bos_min_move_atr_m1=0.0,
        ote_max_active_per_side_m1=2,
    )
    atr_series = pd.Series([1.0] * 5)

    zones = ote_engine.build_ote_zones(
        [long_leg, short_leg], SmcTrend.RANGE, cfg, atr_series, bias="LONG"
    )

    assert any(zone.role == "PRIMARY" and zone.direction == "LONG" for zone in zones)
    assert any(
        zone.role == "COUNTERTREND" and zone.direction == "SHORT" for zone in zones
    )


def test_last_choch_time_filters_old_legs() -> None:
    swing_a_low = SmcSwing(
        index=0,
        time=pd.Timestamp("2024-01-01T00:00:00Z"),
        price=100.0,
        kind="LOW",
        strength=2,
    )
    swing_a_high = SmcSwing(
        index=1,
        time=pd.Timestamp("2024-01-01T01:00:00Z"),
        price=120.0,
        kind="HIGH",
        strength=2,
    )
    swing_b_low = SmcSwing(
        index=2,
        time=pd.Timestamp("2024-01-01T02:00:00Z"),
        price=105.0,
        kind="LOW",
        strength=2,
    )
    swing_b_high = SmcSwing(
        index=3,
        time=pd.Timestamp("2024-01-01T03:00:00Z"),
        price=125.0,
        kind="HIGH",
        strength=2,
    )

    leg_old = SmcStructureLeg(from_swing=swing_a_low, to_swing=swing_a_high, label="HH")
    leg_new = SmcStructureLeg(from_swing=swing_b_low, to_swing=swing_b_high, label="HH")

    cfg = SmcCoreConfig(
        leg_min_amplitude_atr_m1=0.0,
        bos_min_move_pct_m1=0.0,
        bos_min_move_atr_m1=0.0,
        ote_max_active_per_side_m1=1,
    )
    atr_series = pd.Series([1.0] * 4)

    last_choch = swing_b_low.time
    zones = ote_engine.build_ote_zones(
        [leg_old, leg_new],
        SmcTrend.UP,
        cfg,
        atr_series,
        bias="LONG",
        last_choch_time=last_choch,
    )

    assert len(zones) == 1
    assert zones[0].leg is leg_new


def test_bias_allows_long_zone_even_if_trend_down() -> None:
    swing_low = SmcSwing(
        index=0,
        time=pd.Timestamp("2024-01-01T00:00:00Z"),
        price=100.0,
        kind="LOW",
        strength=2,
    )
    swing_high = SmcSwing(
        index=1,
        time=pd.Timestamp("2024-01-01T00:30:00Z"),
        price=120.0,
        kind="HIGH",
        strength=2,
    )
    leg = SmcStructureLeg(from_swing=swing_low, to_swing=swing_high, label="HH")
    cfg = SmcCoreConfig(
        leg_min_amplitude_atr_m1=0.0,
        bos_min_move_pct_m1=0.0,
        bos_min_move_atr_m1=0.0,
        ote_trend_only_m1=True,
        ote_max_active_per_side_m1=1,
    )
    atr_series = pd.Series([1.0, 1.0])

    zones = ote_engine.build_ote_zones(
        [leg], SmcTrend.DOWN, cfg, atr_series, bias="LONG"
    )

    assert len(zones) == 1
    assert zones[0].direction == "LONG"


def test_trend_filter_blocks_long_zone_without_bias() -> None:
    swing_low = SmcSwing(
        index=0,
        time=pd.Timestamp("2024-01-01T00:00:00Z"),
        price=100.0,
        kind="LOW",
        strength=2,
    )
    swing_high = SmcSwing(
        index=1,
        time=pd.Timestamp("2024-01-01T00:30:00Z"),
        price=120.0,
        kind="HIGH",
        strength=2,
    )
    leg = SmcStructureLeg(from_swing=swing_low, to_swing=swing_high, label="HH")
    cfg = SmcCoreConfig(
        leg_min_amplitude_atr_m1=0.0,
        bos_min_move_pct_m1=0.0,
        bos_min_move_atr_m1=0.0,
        ote_trend_only_m1=True,
        ote_max_active_per_side_m1=1,
    )
    atr_series = pd.Series([1.0, 1.0])

    zones = ote_engine.build_ote_zones(
        [leg], SmcTrend.DOWN, cfg, atr_series, bias="NEUTRAL"
    )

    assert zones == []
