"""Юніт-тести для структури ринку (свінги, рендж, BOS)."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

import smc_structure
from smc_core.config import SmcCoreConfig
from smc_core.smc_types import (
    SmcInput,
    SmcRangeState,
    SmcStructureLeg,
    SmcSwing,
    SmcTrend,
)
from smc_structure import structure_engine
from smc_structure.range_engine import detect_active_range
from smc_structure.swing_detector import detect_swings


def _structure_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": list(range(11)),
            "open": [
                100,
                103,
                101,
                110,
                111,
                105,
                102,
                118,
                115,
                117,
                120,
            ],
            "high": [
                101,
                106,
                103,
                118,
                115,
                108,
                111,
                125,
                119,
                122,
                126,
            ],
            "low": [
                99,
                101,
                98,
                107,
                108,
                100,
                99,
                113,
                110,
                114,
                116,
            ],
            "close": [
                100,
                104,
                99.5,
                117,
                110,
                101,
                100.5,
                123,
                116,
                120,
                124,
            ],
        }
    )


def _noisy_frame() -> pd.DataFrame:
    base = 100.0
    closes = [base + ((-1) ** i) * 0.2 for i in range(20)]
    highs = [c + 0.3 for c in closes]
    lows = [c - 0.3 for c in closes]
    return pd.DataFrame(
        {
            "open_time": list(range(len(closes))),
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
        }
    )


def test_compute_structure_state_forms_hh_hl_sequence() -> None:
    cfg = SmcCoreConfig(
        min_swing_bars=2,
        min_range_bars=6,
        eq_tolerance_pct=0.1,
        ote_min=0.62,
        ote_max=0.79,
        max_lookback_bars=200,
        default_timeframes=("5m",),
    )
    frame = _structure_frame()
    snapshot = SmcInput(
        symbol="xauusd",
        tf_primary="5m",
        ohlc_by_tf={"5m": frame},
        context={},
    )

    state = smc_structure.compute_structure_state(snapshot, cfg)

    assert len(state.swings) >= 2
    assert any(leg.label == "HH" for leg in state.legs)
    assert any(evt.event_type == "BOS" for evt in state.events)
    assert state.trend in (SmcTrend.UP, SmcTrend.RANGE)
    assert state.active_range is not None
    assert state.range_state in {
        SmcRangeState.INSIDE,
        SmcRangeState.DEV_UP,
        SmcRangeState.DEV_DOWN,
    }
    assert state.meta["bar_count"] == len(frame)
    assert state.meta["symbol"] == "xauusd"
    assert state.meta["tf_input"] == "5m"
    assert isinstance(state.meta["snapshot_start_ts"], pd.Timestamp)
    assert isinstance(state.meta["snapshot_end_ts"], pd.Timestamp)
    assert isinstance(state.meta["swing_times"], list)
    assert len(state.meta["swing_times"]) == len(state.swings)
    assert state.bias in {"LONG", "SHORT", "NEUTRAL"}
    assert state.meta["bias"] == state.bias
    assert "last_choch_ts" in state.meta


def test_bos_threshold_filters_noise() -> None:
    cfg = SmcCoreConfig(
        min_swing_bars=2,
        min_range_bars=4,
        bos_min_move_pct_m1=0.05,
        bos_min_move_atr_m1=2.0,
        default_timeframes=("1m",),
    )
    frame = _noisy_frame()
    snapshot = SmcInput(
        symbol="xauusd",
        tf_primary="1m",
        ohlc_by_tf={"1m": frame},
        context={},
    )

    state = smc_structure.compute_structure_state(snapshot, cfg)

    assert state.events == []


def test_significant_moves_trigger_events() -> None:
    cfg = SmcCoreConfig(bos_min_move_pct_m1=0.0, bos_min_move_atr_m1=0.0)
    df = pd.DataFrame(
        {
            "open_time": [0, 1, 2],
            "open": [120.0, 100.0, 130.0],
            "high": [121.0, 101.0, 131.0],
            "low": [119.0, 99.0, 129.0],
            "close": [120.0, 100.0, 130.0],
        }
    )
    swing_high_1 = SmcSwing(
        index=0,
        time=pd.Timestamp("2024-01-01T00:00:00Z"),
        price=120.0,
        kind="HIGH",
        strength=1,
    )
    swing_low = SmcSwing(
        index=1,
        time=pd.Timestamp("2024-01-01T00:01:00Z"),
        price=100.0,
        kind="LOW",
        strength=1,
    )
    swing_high_2 = SmcSwing(
        index=2,
        time=pd.Timestamp("2024-01-01T00:02:00Z"),
        price=130.0,
        kind="HIGH",
        strength=1,
    )
    legs = [
        SmcStructureLeg(from_swing=swing_high_1, to_swing=swing_low, label="LL"),
        SmcStructureLeg(from_swing=swing_low, to_swing=swing_high_2, label="HH"),
    ]

    events = structure_engine.detect_events(legs, df, atr_series=None, cfg=cfg)

    assert any(evt.event_type == "BOS" and evt.direction == "SHORT" for evt in events)
    assert any(evt.event_type == "CHOCH" and evt.direction == "LONG" for evt in events)


def _epoch_ms_df(bar_count: int) -> pd.DataFrame:
    base = datetime(2025, 11, 25, 10, 0, tzinfo=UTC)
    base_ms = int(base.timestamp() * 1000)
    times = [base_ms + i * 60_000 for i in range(bar_count)]
    prices = [4100 + ((-1) ** i) * 2 + (i % 3) for i in range(bar_count)]
    return pd.DataFrame(
        {
            "timestamp": times,
            "open_time": times,
            "open": prices,
            "high": [p + 2 for p in prices],
            "low": [p - 2 for p in prices],
            "close": prices,
        }
    )


def test_detect_swings_preserves_epoch_ms() -> None:
    df = _epoch_ms_df(10)

    swings = detect_swings(df, min_separation=1)

    assert swings, "Очікуємо хоча б один свінг"
    assert all(s.time.year >= 2025 for s in swings)


def test_active_range_uses_epoch_ms_for_bounds() -> None:
    df = _epoch_ms_df(8)

    active_range, state = detect_active_range(df, min_range_bars=5, tolerance_pct=0.05)

    assert active_range is not None
    assert state in {
        SmcRangeState.INSIDE,
        SmcRangeState.DEV_UP,
        SmcRangeState.DEV_DOWN,
    }
    assert active_range.start_time is not None
    assert active_range.end_time is not None
    assert active_range.start_time.year >= 2025
    assert active_range.end_time.year >= 2025


def test_timestamp_meta_recovers_from_epoch_ms() -> None:
    base_ms = 1763337600000  # 2025-11-17T00:00:00Z
    frame = _structure_frame()
    frame = frame.copy()
    frame["open_time"] = [base_ms + i * 300_000 for i in range(len(frame))]
    # Емуляція поламаного timestamp у секундах, який дає 1970 рік
    frame["timestamp"] = pd.to_datetime(frame["open_time"] // 1_000, unit="s", utc=True)
    cfg = SmcCoreConfig(min_swing_bars=2, default_timeframes=("5m",))
    snapshot = SmcInput(
        symbol="xauusd",
        tf_primary="5m",
        ohlc_by_tf={"5m": frame},
        context={},
    )

    state = smc_structure.compute_structure_state(snapshot, cfg)

    assert state.meta["snapshot_start_ts"] is not None
    assert state.meta["snapshot_start_ts"].year >= 2025
    assert state.swings
    assert state.swings[0].time.year >= 2025
