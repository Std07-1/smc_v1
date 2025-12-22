"""Unit-тести для Stage5 execution: in_play гейтінг."""

from __future__ import annotations

import pandas as pd

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import (
    SmcInput,
    SmcStructureState,
    SmcZone,
    SmcZonesState,
    SmcZoneType,
)
from smc_execution import compute_execution_state

BASE_TS = pd.Timestamp("2024-01-01T00:00:00Z")


def _ts(i: int) -> pd.Timestamp:
    return BASE_TS + pd.Timedelta(minutes=i)


def _frame_1m(values: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([_ts(i) for i in range(len(values))], tz="UTC")
    df = pd.DataFrame(values, columns=["open", "high", "low", "close"], index=idx)
    return df


def _poi_zone(*, lo: float, hi: float, direction: str = "LONG") -> SmcZone:
    return SmcZone(
        zone_type=SmcZoneType.ORDER_BLOCK,
        price_min=float(lo),
        price_max=float(hi),
        timeframe="5m",
        origin_time=_ts(0),
        direction=direction,  # type: ignore[arg-type]
        role="PRIMARY",
        strength=1.0,
        confidence=1.0,
        components=["test"],
        zone_id="z1",
        meta={"poi_type": "OB", "score": 1.0, "why": ["test"]},
    )


def test_execution_is_empty_when_not_in_play() -> None:
    cfg = SmcCoreConfig(exec_enabled=True, exec_in_play_radius_atr=0.9)

    # Ціна ~100, POI далеко.
    df1m = _frame_1m(
        [
            (100.0, 100.2, 99.8, 100.1),
            (100.1, 100.3, 99.9, 100.0),
            (100.0, 100.2, 99.7, 100.05),
            (100.05, 100.1, 99.9, 100.0),
        ]
    )

    snapshot = SmcInput(symbol="xauusd", tf_primary="5m", ohlc_by_tf={"1m": df1m})
    structure = SmcStructureState(primary_tf="5m", bias="LONG", meta={"atr_last": 1.0})
    zones = SmcZonesState(poi_zones=[_poi_zone(lo=120.0, hi=121.0)])

    exec_state = compute_execution_state(
        snapshot=snapshot,
        structure=structure,
        liquidity=None,
        zones=zones,
        cfg=cfg,
    )

    assert exec_state.execution_events == []
    assert exec_state.meta.get("in_play") is False


def test_execution_in_play_true_inside_poi() -> None:
    cfg = SmcCoreConfig(exec_enabled=True, exec_in_play_radius_atr=0.0)

    df1m = _frame_1m(
        [
            (99.0, 99.5, 98.8, 99.2),
            (99.2, 99.6, 99.0, 99.4),
            (99.4, 99.7, 99.1, 99.55),
            # close близько до межі POI (Stage5 трактує POI як "зону входу").
            (99.55, 99.6, 99.0, 99.05),
        ]
    )

    snapshot = SmcInput(symbol="xauusd", tf_primary="5m", ohlc_by_tf={"1m": df1m})
    structure = SmcStructureState(primary_tf="5m", bias="LONG", meta={"atr_last": 1.0})
    zones = SmcZonesState(poi_zones=[_poi_zone(lo=99.0, hi=100.0)])

    exec_state = compute_execution_state(
        snapshot=snapshot,
        structure=structure,
        liquidity=None,
        zones=zones,
        cfg=cfg,
    )

    assert exec_state.meta.get("in_play") is True
