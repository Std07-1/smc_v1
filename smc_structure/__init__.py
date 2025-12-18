"""SMC structure pipeline: swings → legs → trend → events → range → OTE.

Модуль формує стан у фіксованій послідовності: детектор свінгів → побудова
ніг HH/HL/LH/LL → оцінка тренду → BOS/ChoCH (із ATR-порогами) → active range/
deviation → OTE-зони, які додатково фільтруються bias та ``last_choch_ts``.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import (
    SmcInput,
    SmcStructureEvent,
    SmcStructureState,
    SmcTrend,
)

from . import metrics, ote_engine, range_engine, structure_engine, swing_detector
from .event_history import EVENT_HISTORY

ATR_PERIOD_M1 = 14


def compute_structure_state(
    snapshot: SmcInput, cfg: SmcCoreConfig
) -> SmcStructureState:
    df = _prepare_frame(
        snapshot.ohlc_by_tf.get(snapshot.tf_primary), cfg.max_lookback_bars
    )
    snapshot_start_ts, snapshot_end_ts = _snapshot_bounds(df)
    swings = swing_detector.detect_swings(df, cfg.min_swing_bars)
    legs = structure_engine.build_legs(swings)
    trend = structure_engine.infer_trend(legs)
    atr_series = metrics.compute_atr(df, ATR_PERIOD_M1)
    atr_last, atr_median = _extract_atr_stats(atr_series)
    events = structure_engine.detect_events(legs, df, atr_series, cfg)
    events_history = EVENT_HISTORY.update_history(
        symbol=snapshot.symbol,
        timeframe=snapshot.tf_primary,
        events=events,
        snapshot_end_ts=snapshot_end_ts,
        retention_minutes=cfg.structure_event_history_max_minutes,
        max_entries=cfg.structure_event_history_max_entries,
    )
    bias, last_choch_ts = _derive_bias(trend, events)
    active_range, range_state = range_engine.detect_active_range(
        df, cfg.min_range_bars, cfg.eq_tolerance_pct
    )
    ranges = [active_range] if active_range else []
    ote_zones = ote_engine.build_ote_zones(
        legs,
        trend,
        cfg,
        atr_series,
        bias=bias,
        last_choch_time=last_choch_ts,
    )

    return SmcStructureState(
        primary_tf=snapshot.tf_primary,
        trend=trend,
        swings=swings,
        legs=legs,
        ranges=ranges,
        active_range=active_range,
        range_state=range_state,
        events=events,
        event_history=events_history,
        ote_zones=ote_zones,
        bias=bias,
        meta={
            "bar_count": 0 if df is None else int(len(df)),
            "cfg_min_swing": cfg.min_swing_bars,
            "cfg_min_range_bars": cfg.min_range_bars,
            "bos_min_move_atr_m1": cfg.bos_min_move_atr_m1,
            "bos_min_move_pct_m1": cfg.bos_min_move_pct_m1,
            "leg_min_amplitude_atr_m1": cfg.leg_min_amplitude_atr_m1,
            "ote_trend_only_m1": cfg.ote_trend_only_m1,
            "ote_max_active_per_side_m1": cfg.ote_max_active_per_side_m1,
            "atr_period": ATR_PERIOD_M1 if atr_series is not None else None,
            "atr_available": atr_series is not None,
            "atr_last": atr_last,
            "atr_median": atr_median,
            "bias": bias,
            "last_choch_ts": last_choch_ts,
            "symbol": snapshot.symbol,
            "tf_input": snapshot.tf_primary,
            "snapshot_start_ts": snapshot_start_ts,
            "snapshot_end_ts": snapshot_end_ts,
            "swing_times": [swing.time for swing in swings],
            "events_retained_total": len(events_history),
            "events_recent_total": len(events),
        },
    )


def _prepare_frame(df: pd.DataFrame | None, max_bars: int) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    if max_bars > 0 and len(df) > max_bars:
        df = df.tail(max_bars).copy()
    else:
        df = df.copy()

    if "open_time" not in df.columns:
        return None
    open_time = pd.to_numeric(df["open_time"], errors="coerce")
    df["timestamp"] = pd.to_datetime(open_time, unit="ms", errors="coerce", utc=True)
    df = df.dropna(subset=["timestamp"]).copy()
    if df.empty:
        return None
    df = df.sort_values("open_time", kind="stable")
    return df.reset_index(drop=True)


def _snapshot_bounds(
    df: pd.DataFrame | None,
) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    if df is None or df.empty or "timestamp" not in df.columns:
        return None, None

    timestamps = df["timestamp"]
    if not pd.api.types.is_datetime64_any_dtype(timestamps):
        timestamps = pd.to_datetime(timestamps, errors="coerce")
    timestamps = timestamps.dropna()
    if timestamps.empty:
        return None, None
    return pd.Timestamp(timestamps.iloc[0]), pd.Timestamp(timestamps.iloc[-1])


def _extract_atr_stats(
    atr_series: pd.Series | None,
) -> tuple[float | None, float | None]:
    if atr_series is None:
        return None, None
    atr_clean = atr_series.dropna()
    if atr_clean.empty:
        return None, None
    return float(atr_clean.iloc[-1]), float(atr_clean.median())


def _derive_bias(
    trend: SmcTrend, events: list[SmcStructureEvent]
) -> tuple[Literal["LONG", "SHORT", "NEUTRAL"], pd.Timestamp | None]:
    last_choch: SmcStructureEvent | None = None
    for event in events or []:
        if event.event_type != "CHOCH":
            continue
        if last_choch is None or event.time >= last_choch.time:
            last_choch = event
    if last_choch is not None:
        return last_choch.direction, last_choch.time
    if trend == SmcTrend.UP:
        return "LONG", None
    if trend == SmcTrend.DOWN:
        return "SHORT", None
    return "NEUTRAL", None
