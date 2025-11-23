"""Розрахунок OTE-зон (Optimal Trade Entry) по останніх ногах."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

import pandas as pd

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import SmcOteZone, SmcStructureLeg, SmcTrend


def build_ote_zones(
    legs: Sequence[SmcStructureLeg],
    trend: SmcTrend,
    cfg: SmcCoreConfig,
    atr_series: pd.Series | None,
    *,
    bias: Literal["LONG", "SHORT", "NEUTRAL"] | None = None,
    last_choch_time: pd.Timestamp | None = None,
) -> list[SmcOteZone]:
    """Повертає OTE-зони з урахуванням тренду/ATR/biased-ролей."""

    if not legs or cfg.ote_min >= cfg.ote_max:
        return []

    scoped_legs = _legs_after_marker(legs, last_choch_time)
    if not scoped_legs:
        return []

    allowed_trends = _allowed_trends(trend, bias)
    per_side_limit = max(1, cfg.ote_max_active_per_side_m1)
    zones_long: list[SmcOteZone] = []
    zones_short: list[SmcOteZone] = []

    for leg in reversed(scoped_legs):
        leg_trend = _leg_trend_direction(leg)
        if leg_trend is None:
            continue
        if cfg.ote_trend_only_m1 and allowed_trends:
            if leg_trend not in allowed_trends:
                continue

        if not _leg_passes_amplitude_threshold(
            leg,
            atr_series,
            cfg,
        ):
            continue

        zone = _build_zone(leg, cfg.ote_min, cfg.ote_max)
        if zone is None:
            continue

        zone.role = _resolve_zone_role(bias, zone.direction)

        if zone.direction == "LONG":
            if len(zones_long) >= per_side_limit:
                continue
            zones_long.append(zone)
        else:
            if len(zones_short) >= per_side_limit:
                continue
            zones_short.append(zone)

    return list(reversed(zones_long)) + list(reversed(zones_short))


def _build_zone(
    leg: SmcStructureLeg, fib_min: float, fib_max: float
) -> SmcOteZone | None:
    price_delta = leg.to_swing.price - leg.from_swing.price
    if abs(price_delta) < 1e-9:
        return None

    if price_delta > 0:
        span = price_delta
        ote_min = leg.to_swing.price - span * fib_max
        ote_max = leg.to_swing.price - span * fib_min
        direction = "LONG"
    else:
        span = abs(price_delta)
        ote_min = leg.to_swing.price + span * fib_min
        ote_max = leg.to_swing.price + span * fib_max
        direction = "SHORT"

    if ote_min == ote_max:
        return None

    return SmcOteZone(
        leg=leg,
        ote_min=min(ote_min, ote_max),
        ote_max=max(ote_min, ote_max),
        direction=direction,
    )


def _leg_trend_direction(leg: SmcStructureLeg) -> SmcTrend | None:
    if leg.label == "HH":
        return SmcTrend.UP
    if leg.label == "LL":
        return SmcTrend.DOWN
    return None


def _legs_after_marker(
    legs: Sequence[SmcStructureLeg], marker_ts: pd.Timestamp | None
) -> list[SmcStructureLeg]:
    if marker_ts is None:
        return list(legs)
    start_idx = 0
    for idx, leg in enumerate(legs):
        if leg.to_swing.time >= marker_ts:
            start_idx = idx
            break
    else:
        return []
    return list(legs[start_idx:])


def _resolve_zone_role(
    bias: Literal["LONG", "SHORT", "NEUTRAL"] | None,
    direction: Literal["LONG", "SHORT"],
) -> Literal["PRIMARY", "COUNTERTREND", "NEUTRAL"]:
    if bias is None:
        return "PRIMARY"
    if bias == "NEUTRAL":
        return "NEUTRAL"
    if (bias == "LONG" and direction == "LONG") or (
        bias == "SHORT" and direction == "SHORT"
    ):
        return "PRIMARY"
    return "COUNTERTREND"


def _allowed_trends(
    trend: SmcTrend, bias: Literal["LONG", "SHORT", "NEUTRAL"] | None
) -> set[SmcTrend]:
    bias_trend: SmcTrend | None = None
    if bias == "LONG":
        bias_trend = SmcTrend.UP
    elif bias == "SHORT":
        bias_trend = SmcTrend.DOWN

    if trend in (SmcTrend.UP, SmcTrend.DOWN):
        allowed = {trend}
        if bias_trend is not None:
            allowed.add(bias_trend)
        return allowed

    if bias_trend is not None:
        # Коли тренд невизначений, але маємо bias — залишаємо обидва напрями
        # для діагностики (PRIMARY визначається роллю, а не фільтром).
        return {SmcTrend.UP, SmcTrend.DOWN}

    return set()


def _leg_passes_amplitude_threshold(
    leg: SmcStructureLeg, atr_series: pd.Series | None, cfg: SmcCoreConfig
) -> bool:
    amplitude = abs(leg.to_swing.price - leg.from_swing.price)
    atr_value = None
    if atr_series is not None and 0 <= leg.to_swing.index < len(atr_series):
        value = atr_series.iloc[leg.to_swing.index]
        atr_value = float(value) if pd.notna(value) else None

    atr_component = (
        0.0 if atr_value is None else atr_value * cfg.leg_min_amplitude_atr_m1
    )
    pct_component = abs(leg.to_swing.price) * cfg.bos_min_move_pct_m1
    threshold = max(atr_component, pct_component)
    if threshold == 0:
        return True
    return amplitude >= threshold
