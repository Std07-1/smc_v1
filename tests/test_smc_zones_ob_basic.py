"""Юніт-тести для Order Block детектора (Етап 4.2, OB_v1)."""

from __future__ import annotations

import pandas as pd

import smc_zones
from smc_core.config import SmcCoreConfig
from smc_core.smc_types import (
    SmcInput,
    SmcStructureEvent,
    SmcStructureLeg,
    SmcStructureState,
    SmcSwing,
    SmcZone,
    SmcZoneType,
)


def test_orderblock_detects_primary_long_zone() -> None:
    cfg = SmcCoreConfig()
    snapshot, structure = _build_snapshot_and_structure(bias="LONG", include_event=True)

    zones_state = smc_zones.compute_zones_state(snapshot, structure, None, cfg)

    assert len(zones_state.zones) == 1
    zone = zones_state.zones[0]
    assert zone.zone_type is SmcZoneType.ORDER_BLOCK
    assert zone.direction == "LONG"
    assert zone.role == "PRIMARY"
    assert zone.entry_mode == "BODY_05"
    assert zones_state.meta["orderblocks_primary"] == 1
    assert zones_state.meta["active_zone_count"] == 1


def test_orderblock_requires_bos_or_choch() -> None:
    cfg = SmcCoreConfig()
    snapshot, structure = _build_snapshot_and_structure(
        bias="LONG", include_event=False
    )

    zones_state = smc_zones.compute_zones_state(snapshot, structure, None, cfg)

    assert zones_state.zones == []
    assert zones_state.meta["orderblocks_total"] == 0


def test_orderblock_marks_countertrend_role() -> None:
    cfg = SmcCoreConfig()
    snapshot, structure = _build_snapshot_and_structure(
        bias="SHORT", include_event=True
    )

    zones_state = smc_zones.compute_zones_state(snapshot, structure, None, cfg)

    assert len(zones_state.zones) == 1
    zone = zones_state.zones[0]
    assert zone.role == "COUNTERTREND"
    assert zone.direction == "LONG"


def test_orderblock_skips_small_body_prelude() -> None:
    cfg = SmcCoreConfig()
    snapshot, structure = _build_snapshot_and_structure(
        bias="LONG", include_event=True, weaken_prelude=True
    )

    zones_state = smc_zones.compute_zones_state(snapshot, structure, None, cfg)

    assert zones_state.zones == []


def test_active_zone_distance_filter_disabled_matches_all() -> None:
    cfg = SmcCoreConfig(ob_max_active_distance_atr=None)
    frame = _build_close_frame()
    structure = SmcStructureState(meta={"atr_last": 1.0})
    origin_time = frame.index[-1]
    zones = [
        _manual_zone(origin_time, 101.0, "near"),
        _manual_zone(origin_time, 110.0, "far"),
    ]

    filtered, distance_meta = smc_zones._select_active_zones(
        zones, frame, structure, cfg
    )

    assert filtered == zones
    assert distance_meta["filtered_out_by_distance"] == 0


def test_active_zone_distance_filter_removes_far_orderblocks() -> None:
    cfg = SmcCoreConfig(ob_max_active_distance_atr=3.0)
    frame = _build_close_frame()
    structure = SmcStructureState(meta={"atr_last": 1.0})
    origin_time = frame.index[-1]
    zones = [
        _manual_zone(origin_time, 101.0, "near"),
        _manual_zone(origin_time, 110.0, "far"),
    ]

    filtered, distance_meta = smc_zones._select_active_zones(
        zones, frame, structure, cfg
    )

    assert len(filtered) == 1
    assert filtered[0].zone_id == "near"
    assert distance_meta["filtered_out_by_distance"] == 1
    max_distance_atr = distance_meta["max_distance_atr"]
    assert isinstance(max_distance_atr, (int, float))
    assert max_distance_atr > 5.0


def test_active_zone_distance_filter_skips_when_no_atr() -> None:
    cfg = SmcCoreConfig(ob_max_active_distance_atr=2.0)
    frame = _build_close_frame()
    structure = SmcStructureState(meta={})
    origin_time = frame.index[-1]
    zones = [
        _manual_zone(origin_time, 101.0, "near"),
        _manual_zone(origin_time, 110.0, "far"),
    ]

    filtered, distance_meta = smc_zones._select_active_zones(
        zones, frame, structure, cfg
    )

    assert len(filtered) == len(zones)
    assert distance_meta["filtered_out_by_distance"] == 0


def _build_snapshot_and_structure(
    bias: str, include_event: bool, weaken_prelude: bool = False
) -> tuple[SmcInput, SmcStructureState]:
    frame = _build_frame(weaken_prelude=weaken_prelude)
    snapshot = SmcInput(
        symbol="XAUUSD",
        tf_primary="5m",
        ohlc_by_tf={"5m": frame},
        context={},
    )

    swings = _build_swings(frame)
    leg = SmcStructureLeg(from_swing=swings[0], to_swing=swings[1], label="HH")

    events: list[SmcStructureEvent] = []
    if include_event:
        events.append(
            SmcStructureEvent(
                event_type="BOS",
                direction="LONG",
                price_level=swings[1].price,
                time=swings[1].time,
                source_leg=leg,
            )
        )

    structure = SmcStructureState(
        primary_tf="5m",
        swings=list(swings),
        legs=[leg],
        events=events,
        bias=bias,  # type: ignore
        meta={"atr_median": 1.0, "bias": bias},
    )
    return snapshot, structure


def _build_swings(frame: pd.DataFrame) -> tuple[SmcSwing, SmcSwing]:
    timestamps = frame.index
    swing_low = SmcSwing(
        index=1, time=timestamps[1], price=100.0, kind="LOW", strength=2
    )
    swing_high = SmcSwing(
        index=5, time=timestamps[5], price=107.0, kind="HIGH", strength=2
    )
    return swing_low, swing_high


def _build_frame(weaken_prelude: bool) -> pd.DataFrame:
    timestamps = pd.date_range("2025-01-01", periods=8, freq="5min")
    data = {
        "open": [101.0, 99.5, 100.5, 101.5, 102.5, 104.0, 106.0, 107.5],
        "high": [101.4, 100.5, 101.2, 102.5, 103.0, 105.0, 107.2, 108.0],
        "low": [98.8, 98.5, 99.8, 100.8, 101.9, 103.2, 105.5, 106.9],
        "close": [99.0, 100.2, 101.0, 102.0, 103.0, 106.5, 107.0, 107.8],
        "volume": [100, 120, 115, 118, 123, 150, 160, 140],
    }
    if weaken_prelude:
        data["close"][0] = 100.7  # тіло стає занадто малим для OB
    frame = pd.DataFrame(data, index=timestamps)
    return frame


def _build_close_frame() -> pd.DataFrame:
    timestamps = pd.date_range("2025-01-01", periods=4, freq="5min")
    data = {
        "open": [100.0, 100.5, 101.0, 101.5],
        "high": [100.5, 101.0, 101.5, 102.0],
        "low": [99.5, 100.0, 100.5, 101.0],
        "close": [100.2, 100.8, 101.2, 101.5],
        "volume": [100, 110, 105, 120],
    }
    return pd.DataFrame(data, index=timestamps)


def _manual_zone(origin_time: pd.Timestamp, center: float, zone_id: str) -> SmcZone:
    return SmcZone(
        zone_type=SmcZoneType.ORDER_BLOCK,
        price_min=center - 0.5,
        price_max=center + 0.5,
        timeframe="5m",
        origin_time=origin_time,
        direction="LONG",
        role="PRIMARY",
        strength=1.0,
        confidence=0.5,
        components=[],
        zone_id=zone_id,
    )
