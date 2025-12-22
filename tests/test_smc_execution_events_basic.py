"""Unit-тести для Stage5 execution: sweep / micro-BOS / retest_ok."""

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
    return pd.DataFrame(values, columns=["open", "high", "low", "close"], index=idx)


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
        zone_id="z_poi",
        meta={"poi_type": "OB", "score": 1.0, "why": ["test"]},
    )


def test_micro_bos_emits_only_when_in_play() -> None:
    cfg = SmcCoreConfig(
        exec_enabled=True, exec_micro_pivot_bars=3, exec_in_play_radius_atr=0.0
    )

    # Останній close пробиває prev_high.
    df1m = _frame_1m(
        [
            (100.0, 100.2, 99.8, 100.0),
            (100.0, 100.3, 99.9, 100.1),
            (100.1, 100.25, 100.0, 100.15),
            (100.15, 101.2, 100.1, 101.0),
        ]
    )

    snapshot = SmcInput(symbol="xauusd", tf_primary="5m", ohlc_by_tf={"1m": df1m})
    structure = SmcStructureState(primary_tf="5m", bias="LONG", meta={"atr_last": 1.0})
    # POI: edge-band. Робимо верхню межу близько до останнього close.
    zones = SmcZonesState(poi_zones=[_poi_zone(lo=100.0, hi=101.0)])

    st = compute_execution_state(
        snapshot=snapshot,
        structure=structure,
        liquidity=None,
        zones=zones,
        cfg=cfg,
    )

    kinds = [e.event_type for e in st.execution_events]
    assert "MICRO_BOS" in kinds
    micro = [e for e in st.execution_events if e.event_type == "MICRO_BOS"]
    assert micro
    assert all(e.ref == "POI" for e in micro)
    assert all(e.poi_zone_id == "z_poi" for e in micro)


def test_sweep_short_near_target() -> None:
    cfg = SmcCoreConfig(exec_enabled=True, exec_in_play_radius_atr=0.9)

    # Target=100.0, останній бар робить high>100 і close<100 => SWEEP SHORT.
    df1m = _frame_1m(
        [
            (99.7, 99.9, 99.6, 99.8),
            (99.8, 100.1, 99.7, 99.95),
            (99.95, 100.6, 99.8, 99.6),
            (99.6, 100.4, 99.5, 99.7),
        ]
    )

    snapshot = SmcInput(
        symbol="xauusd",
        tf_primary="5m",
        ohlc_by_tf={"1m": df1m},
        # TARGET-гейт у Stage5 береться з context (PDH/PDL/PWH/PWL + завершені сесії).
        context={"pdh": 100.0},
    )
    structure = SmcStructureState(primary_tf="5m", bias="SHORT", meta={"atr_last": 1.0})

    st = compute_execution_state(
        snapshot=snapshot,
        structure=structure,
        liquidity=None,
        zones=None,
        cfg=cfg,
    )

    sweeps = [e for e in st.execution_events if e.event_type == "SWEEP"]
    assert sweeps, "Очікую хоча б один SWEEP"
    assert any(e.direction == "SHORT" for e in sweeps)
    assert all(e.ref == "TARGET" for e in sweeps)


def test_retest_ok_after_break_and_hold() -> None:
    cfg = SmcCoreConfig(
        exec_enabled=True, exec_micro_pivot_bars=3, exec_in_play_radius_atr=0.0
    )

    # bar[-2] пробиває pivot high, bar[-1] торкає level і закриває вище => RETEST_OK.
    df1m = _frame_1m(
        [
            (100.0, 100.2, 99.9, 100.0),
            (100.0, 100.3, 99.95, 100.1),
            (100.1, 100.25, 100.0, 100.15),
            (100.15, 101.4, 100.1, 101.2),  # break
            (101.2, 101.25, 100.3, 101.05),  # retest hold
        ]
    )

    snapshot = SmcInput(symbol="xauusd", tf_primary="5m", ohlc_by_tf={"1m": df1m})
    structure = SmcStructureState(primary_tf="5m", bias="LONG", meta={"atr_last": 1.0})
    # POI: edge-band. Робимо верхню межу близько до останнього close.
    zones = SmcZonesState(poi_zones=[_poi_zone(lo=99.5, hi=101.1)])

    st = compute_execution_state(
        snapshot=snapshot,
        structure=structure,
        liquidity=None,
        zones=zones,
        cfg=cfg,
    )

    retests = [e for e in st.execution_events if e.event_type == "RETEST_OK"]
    assert retests
    assert all(e.ref == "POI" for e in retests)
    assert all(e.poi_zone_id == "z_poi" for e in retests)


def test_retest_ok_after_sweep_and_reject_near_target() -> None:
    cfg = SmcCoreConfig(exec_enabled=True, exec_in_play_radius_atr=1.0)

    # Target=100.0.
    # bar[-2]: high>100 і close<100 => sweep high.
    # bar[-1]: торкає 100 і закриває нижче => reject/hold.
    df1m = _frame_1m(
        [
            (99.7, 99.9, 99.6, 99.8),
            (99.8, 100.0, 99.7, 99.9),
            (99.9, 100.1, 99.8, 99.95),
            (99.95, 100.6, 99.9, 99.7),
            (99.7, 100.05, 99.6, 99.65),
        ]
    )

    snapshot = SmcInput(
        symbol="xauusd",
        tf_primary="5m",
        ohlc_by_tf={"1m": df1m},
        context={"pdh": 100.0},
    )
    structure = SmcStructureState(primary_tf="5m", bias="SHORT", meta={"atr_last": 1.0})

    st = compute_execution_state(
        snapshot=snapshot,
        structure=structure,
        liquidity=None,
        zones=None,
        cfg=cfg,
    )

    retests = [e for e in st.execution_events if e.event_type == "RETEST_OK"]
    assert retests, "Очікую RETEST_OK після sweep&reject біля target"
    assert any(
        abs(float(e.level) - 100.0) < 1e-9 for e in retests
    ), "RETEST_OK має бути прив'язаний до target рівня"
