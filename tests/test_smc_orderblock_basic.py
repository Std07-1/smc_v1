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
from smc_structure.event_history import EVENT_HISTORY, reset_structure_event_history
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
    event_history: list[SmcStructureEvent] | None = None,
) -> SmcStructureState:

    swings = {leg[0].index: leg[0] for leg in legs}
    for _, swing_to, _ in legs:
        swings[swing_to.index] = swing_to
    leg_objs = []
    for swing_from, swing_to, label in legs:
        leg_objs.append(
            SmcStructureLeg(from_swing=swing_from, to_swing=swing_to, label=label)
        )
    remapped_events = _remap_events(events, leg_objs)
    remapped_history = _remap_events(event_history or [], leg_objs)
    return SmcStructureState(
        primary_tf="5m",
        swings=list(swings.values()),
        legs=leg_objs,
        events=remapped_events,
        event_history=remapped_history,
        bias=bias,
        meta={"bias": bias, "atr_last": 1.2},
    )


def _remap_events(
    events: list[SmcStructureEvent], leg_objs: list[SmcStructureLeg]
) -> list[SmcStructureEvent]:
    remapped: list[SmcStructureEvent] = []
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
        remapped.append(
            SmcStructureEvent(
                event_type=event.event_type,
                direction=event.direction,
                price_level=event.price_level,
                time=event.time,
                source_leg=matching_leg,
            )
        )
    return remapped


def _long_setup() -> tuple[
    SmcInput,
    tuple[SmcSwing, SmcSwing, SmcStructureLegLabel],
    SmcStructureEvent,
]:
    values = [
        (101.0, 101.8, 100.4, 100.6),
        (100.6, 101.2, 99.6, 100.1),
        (102.0, 102.4, 97.8, 98.1),
        (98.1, 98.6, 97.4, 98.3),
        (98.3, 100.2, 98.1, 99.8),
        (99.8, 102.6, 99.8, 102.4),
        (102.4, 104.5, 101.9, 104.1),
    ]
    snapshot = _make_snapshot(values)
    swing_from = _swing(3, 97.4, "LOW")
    swing_to = _swing(6, 104.5, "HIGH")
    leg = (swing_from, swing_to, "HL")
    bos_event = SmcStructureEvent(
        event_type="BOS",
        direction="LONG",
        price_level=104.1,
        time=_ts(6),
        source_leg=SmcStructureLeg(
            from_swing=swing_from, to_swing=swing_to, label="HL"
        ),
    )
    return snapshot, leg, bos_event


def _short_setup() -> tuple[
    SmcInput,
    tuple[SmcSwing, SmcSwing, SmcStructureLegLabel],
    SmcStructureEvent,
]:
    values = [
        (98.1, 98.7, 97.5, 98.3),
        (98.3, 99.2, 97.9, 98.8),
        (97.8, 103.6, 97.6, 103.1),
        (103.1, 103.7, 102.4, 102.9),
        (103.3, 102.1, 100.8, 101.2),
        (101.2, 99.4, 97.9, 98.5),
        (98.5, 96.8, 95.9, 96.4),
    ]
    snapshot = _make_snapshot(values)
    swing_from = _swing(3, 103.7, "HIGH")
    swing_to = _swing(6, 95.9, "LOW")
    leg = (swing_from, swing_to, "LH")
    bos_event = SmcStructureEvent(
        event_type="BOS",
        direction="SHORT",
        price_level=96.4,
        time=_ts(6),
        source_leg=SmcStructureLeg(
            from_swing=swing_from, to_swing=swing_to, label="LH"
        ),
    )
    return snapshot, leg, bos_event


def test_orderblock_long_basic() -> None:
    snapshot, leg, bos_event = _long_setup()
    structure = _structure([leg], [bos_event], bias="LONG")

    zones = detect_order_blocks(snapshot, structure, SmcCoreConfig())

    assert len(zones) == 1
    zone = zones[0]
    assert zone.zone_type is SmcZoneType.ORDER_BLOCK
    assert zone.direction == "LONG"
    assert zone.role == "PRIMARY"
    assert zone.meta["reference_event_type"] == "BOS"


def test_orderblock_short_basic() -> None:
    snapshot, leg, bos_event = _short_setup()
    structure = _structure([leg], [bos_event], bias="SHORT")

    zones = detect_order_blocks(snapshot, structure, SmcCoreConfig())

    assert len(zones) == 1
    zone = zones[0]
    assert zone.direction == "SHORT"
    assert zone.role == "PRIMARY"
    assert zone.meta["reference_event_type"] == "BOS"


def test_orderblock_requires_break_event() -> None:
    snapshot, leg, _ = _long_setup()
    structure = _structure([leg], [], bias="LONG")

    zones = detect_order_blocks(snapshot, structure, SmcCoreConfig())

    assert zones == [], "Без BOS/CHOCH зона не створюється"


def test_orderblock_role_aligns_with_bias() -> None:
    snapshot, leg, bos_event = _long_setup()
    primary_structure = _structure([leg], [bos_event], bias="LONG")
    counter_structure = _structure([leg], [bos_event], bias="SHORT")

    primary_zone = detect_order_blocks(snapshot, primary_structure, SmcCoreConfig())[0]
    counter_zone = detect_order_blocks(snapshot, counter_structure, SmcCoreConfig())[0]

    assert primary_zone.role == "PRIMARY"
    assert counter_zone.role == "COUNTERTREND"


def test_orderblock_detects_bos_with_leg_copy() -> None:
    snapshot, leg, bos_event = _long_setup()
    copied_event = SmcStructureEvent(
        event_type=bos_event.event_type,
        direction=bos_event.direction,
        price_level=bos_event.price_level,
        time=bos_event.time,
        source_leg=SmcStructureLeg(
            from_swing=_swing(leg[0].index, leg[0].price, "LOW"),
            to_swing=_swing(leg[1].index, leg[1].price, "HIGH"),
            label=leg[2],
        ),
    )
    structure = _structure([leg], [copied_event], bias="LONG")

    zones = detect_order_blocks(snapshot, structure, SmcCoreConfig())

    assert zones, "Очікуємо знайдену зону"
    zone = zones[0]
    assert zone.role == "PRIMARY"
    assert zone.meta["reference_event_type"] == "BOS"


def test_orderblock_uses_event_history() -> None:
    reset_structure_event_history()
    cfg = SmcCoreConfig(
        structure_event_history_max_minutes=120,
        structure_event_history_max_entries=10,
    )
    snapshot, leg, bos_event = _long_setup()
    EVENT_HISTORY.update_history(
        symbol="TEST",
        timeframe="5m",
        events=[bos_event],
        snapshot_end_ts=bos_event.time,
        retention_minutes=cfg.structure_event_history_max_minutes,
        max_entries=cfg.structure_event_history_max_entries,
    )
    history = EVENT_HISTORY.get_history("TEST", "5m")
    structure = _structure([leg], [], bias="LONG", event_history=history)
    assert not structure.events, "Структура імітує новий снапшот без свіжих подій"

    zones = detect_order_blocks(snapshot, structure, cfg)

    assert len(zones) == 1
    zone = zones[0]
    assert zone.role == "PRIMARY"
    assert zone.reference_event_id is not None
    assert zone.meta.get("reference_event_type") == "BOS"


def test_orderblock_expires_with_ttl() -> None:
    reset_structure_event_history()
    cfg = SmcCoreConfig(
        structure_event_history_max_minutes=30,
        structure_event_history_max_entries=5,
    )
    snapshot, leg, bos_event = _long_setup()
    EVENT_HISTORY.update_history(
        symbol="TEST",
        timeframe="5m",
        events=[bos_event],
        snapshot_end_ts=bos_event.time,
        retention_minutes=cfg.structure_event_history_max_minutes,
        max_entries=cfg.structure_event_history_max_entries,
    )
    expiry_ts = bos_event.time + pd.Timedelta(
        minutes=cfg.structure_event_history_max_minutes + 5
    )
    EVENT_HISTORY.update_history(
        symbol="TEST",
        timeframe="5m",
        events=[],
        snapshot_end_ts=expiry_ts,
        retention_minutes=cfg.structure_event_history_max_minutes,
        max_entries=cfg.structure_event_history_max_entries,
    )
    history = EVENT_HISTORY.get_history("TEST", "5m")
    assert not history, "Подія повинна вийти за TTL та зникнути"

    structure = _structure([leg], [], bias="LONG", event_history=history)
    zones = detect_order_blocks(snapshot, structure, cfg)

    assert not zones, "OB не повинен відновлюватись після TTL"
