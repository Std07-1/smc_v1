"""Unit-тести для orderblock_detector (етап 4.2)."""

from __future__ import annotations

from typing import Literal

import pandas as pd

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import (
    SmcInput,
    SmcStructureEvent,
    SmcStructureLeg,
    SmcStructureState,
    SmcSwing,
    SmcZoneType,
)
from smc_zones.orderblock_detector import detect_order_blocks

BASE_TS = pd.Timestamp("2024-01-01T00:00:00Z")


def _ts(idx: int) -> pd.Timestamp:
    return BASE_TS + pd.Timedelta(minutes=5 * idx)


def _make_frame(values: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    rows = []
    for idx, (open_v, high_v, low_v, close_v) in enumerate(values):
        rows.append(
            {
                "open_time": _ts(idx),
                "open": open_v,
                "high": high_v,
                "low": low_v,
                "close": close_v,
            }
        )
    return pd.DataFrame(rows)


def _make_snapshot(values: list[tuple[float, float, float, float]]) -> SmcInput:
    frame = _make_frame(values)
    return SmcInput(
        symbol="TEST", tf_primary="5m", ohlc_by_tf={"5m": frame}, context={}
    )


def _swing(idx: int, price: float, kind: Literal["HIGH", "LOW"]) -> SmcSwing:
    return SmcSwing(index=idx, time=_ts(idx), price=price, kind=kind, strength=3)


SmcStructureLegLabel = Literal["HH", "HL", "LH", "LL", "UNDEFINED"]
SmcBias = Literal["LONG", "SHORT", "NEUTRAL"]


def _structure(
    legs: list[tuple[SmcSwing, SmcSwing, SmcStructureLegLabel]],
    events: list[SmcStructureEvent],
    bias: SmcBias,
) -> SmcStructureState:

    swings = {leg[0].index: leg[0] for leg in legs}
    for _, swing_to, _ in legs:
        swings[swing_to.index] = swing_to
    leg_objs = []
    for swing_from, swing_to, label in legs:
        leg_objs.append(
            SmcStructureLeg(from_swing=swing_from, to_swing=swing_to, label=label)
        )
    # align events with actual leg instances
    remapped_events: list[SmcStructureEvent] = []
    for event in events:
        source_leg = getattr(event, "source_leg", None)
        if source_leg is None:
            continue
        matching_leg = next(
            (
                leg
                for leg in leg_objs
                if leg.from_swing.index == source_leg.from_swing.index
                and leg.to_swing.index == source_leg.to_swing.index
            ),
            None,
        )
        if matching_leg is None:
            continue
        remapped_events.append(
            SmcStructureEvent(
                event_type=event.event_type,
                direction=event.direction,
                price_level=event.price_level,
                time=event.time,
                source_leg=matching_leg,
            )
        )
    return SmcStructureState(
        primary_tf="5m",
        swings=list(swings.values()),
        legs=leg_objs,
        events=remapped_events,
        bias=bias,
        meta={"bias": bias, "atr_last": 1.2},
    )


def test_orderblock_long_basic() -> None:
    values = [
        (101.0, 101.2, 100.3, 100.6),
        (100.6, 100.7, 99.5, 99.8),
        (99.8, 100.0, 98.9, 99.1),
        (99.1, 99.2, 98.6, 98.8),
        (98.8, 100.8, 98.7, 100.4),
        (100.4, 102.2, 100.3, 101.9),
        (101.9, 103.5, 101.7, 103.0),
    ]
    snapshot = _make_snapshot(values)
    leg = (_swing(3, 98.6, "LOW"), _swing(6, 103.5, "HIGH"), "HL")
    bos_event = SmcStructureEvent(
        event_type="BOS",
        direction="LONG",
        price_level=103.0,
        time=_ts(6),
        source_leg=SmcStructureLeg(from_swing=leg[0], to_swing=leg[1], label="HL"),
    )
    structure = _structure([leg], [bos_event], bias="LONG")

    zones = detect_order_blocks(snapshot, structure, SmcCoreConfig())

    assert len(zones) == 1
    zone = zones[0]
    assert zone.zone_type is SmcZoneType.ORDER_BLOCK
    assert zone.direction == "LONG"
    assert zone.role == "PRIMARY"
    assert zone.meta["quality"] == "STRONG"
    assert zone.entry_mode in {"BODY_05", "BODY_TOUCH", "WICK_05", "WICK_TOUCH"}


def test_orderblock_short_basic() -> None:
    values = [
        (105.0, 105.3, 104.9, 105.2),
        (105.2, 105.5, 105.0, 105.4),
        (105.4, 105.6, 105.2, 105.5),
        (105.5, 105.7, 104.4, 104.8),
        (104.8, 105.0, 103.8, 103.9),
        (103.9, 104.1, 102.5, 102.8),
        (102.8, 103.0, 101.5, 101.9),
    ]
    snapshot = _make_snapshot(values)
    leg = (_swing(2, 105.5, "HIGH"), _swing(6, 101.5, "LOW"), "LH")
    bos_event = SmcStructureEvent(
        event_type="BOS",
        direction="SHORT",
        price_level=101.9,
        time=_ts(6),
        source_leg=SmcStructureLeg(from_swing=leg[0], to_swing=leg[1], label="LH"),
    )
    structure = _structure([leg], [bos_event], bias="SHORT")

    zones = detect_order_blocks(snapshot, structure, SmcCoreConfig())

    assert len(zones) == 1
    zone = zones[0]
    assert zone.direction == "SHORT"
    assert zone.role == "PRIMARY"
    assert zone.meta["quality"] == "STRONG"


def test_orderblock_weak_without_bos() -> None:
    values = [
        (101.0, 101.2, 100.3, 100.6),
        (100.6, 100.7, 99.5, 99.8),
        (99.8, 100.0, 98.9, 99.1),
        (99.1, 99.2, 98.6, 98.8),
        (98.8, 100.8, 98.7, 100.4),
        (100.4, 102.2, 100.3, 101.9),
        (101.9, 103.5, 101.7, 103.0),
    ]
    snapshot = _make_snapshot(values)
    leg = (_swing(3, 98.6, "LOW"), _swing(6, 103.5, "HIGH"), "HL")
    structure = _structure([leg], [], bias="LONG")

    zones = detect_order_blocks(snapshot, structure, SmcCoreConfig())

    assert len(zones) == 1
    zone = zones[0]
    assert zone.role == "NEUTRAL"
    assert zone.meta["quality"] == "WEAK"


def test_orderblock_role_aligns_with_bias() -> None:
    values = [
        (101.0, 101.2, 100.3, 100.6),
        (100.6, 100.7, 99.5, 99.8),
        (99.8, 100.0, 98.9, 99.1),
        (99.1, 99.2, 98.6, 98.8),
        (98.8, 100.8, 98.7, 100.4),
        (100.4, 102.2, 100.3, 101.9),
        (101.9, 103.5, 101.7, 103.0),
        (103.0, 103.2, 101.4, 101.8),
        (101.8, 101.9, 99.8, 100.2),
        (100.2, 100.4, 98.9, 99.4),
    ]
    snapshot = _make_snapshot(values)
    long_leg = (_swing(3, 98.6, "LOW"), _swing(6, 103.5, "HIGH"), "HL")
    short_leg = (_swing(6, 103.5, "HIGH"), _swing(9, 98.9, "LOW"), "LH")
    long_event = SmcStructureEvent(
        event_type="BOS",
        direction="LONG",
        price_level=103.0,
        time=_ts(6),
        source_leg=SmcStructureLeg(
            from_swing=long_leg[0], to_swing=long_leg[1], label="HL"
        ),
    )
    short_event = SmcStructureEvent(
        event_type="BOS",
        direction="SHORT",
        price_level=99.4,
        time=_ts(9),
        source_leg=SmcStructureLeg(
            from_swing=short_leg[0], to_swing=short_leg[1], label="LH"
        ),
    )
    structure = _structure(
        [long_leg, short_leg], [long_event, short_event], bias="LONG"
    )

    zones = detect_order_blocks(snapshot, structure, SmcCoreConfig())

    assert len(zones) == 2
    long_zone = next(z for z in zones if z.direction == "LONG")
    short_zone = next(z for z in zones if z.direction == "SHORT")
    assert long_zone.role == "PRIMARY"
    assert short_zone.role == "COUNTERTREND"


def test_orderblock_detects_bos_with_leg_copy() -> None:
    values = [
        (100.0, 100.2, 99.4, 99.8),
        (99.8, 100.1, 98.8, 99.0),
        (99.0, 100.5, 98.6, 100.1),
        (100.1, 102.4, 100.0, 101.8),
    ]
    snapshot = _make_snapshot(values)
    leg = (_swing(1, 98.8, "LOW"), _swing(3, 102.4, "HIGH"), "HL")
    bos_event = SmcStructureEvent(
        event_type="BOS",
        direction="LONG",
        price_level=101.8,
        time=_ts(3),
        source_leg=SmcStructureLeg(
            from_swing=_swing(1, 98.8, "LOW"),
            to_swing=_swing(3, 102.4, "HIGH"),
            label="HL",
        ),
    )
    # Події містять копію ноги, але _structure має зіставити її з leg_objs
    structure = _structure([leg], [bos_event], bias="LONG")

    zones = detect_order_blocks(snapshot, structure, SmcCoreConfig())

    assert zones, "Очікуємо знайдену зону"
    zone = zones[0]
    assert zone.role == "PRIMARY"
    assert zone.meta["has_bos"] is True
