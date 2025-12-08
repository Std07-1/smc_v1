"""Побудова HH/LL-структури, тренду та подій BOS/ChoCH."""

from __future__ import annotations

import logging
from collections.abc import Sequence

import pandas as pd

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import (
    SmcStructureEvent,
    SmcStructureLeg,
    SmcSwing,
    SmcTrend,
)

LOGGER = logging.getLogger(__name__)


def build_legs(swings: Sequence[SmcSwing]) -> list[SmcStructureLeg]:
    """Перетворює свінги на послідовність ніг із класифікацією HH/HL/LH/LL."""

    if len(swings) < 2:
        return []

    legs: list[SmcStructureLeg] = []
    last_high = None
    last_low = None

    #  Ініціалізуємо останні значення типами перших свінгів
    first = swings[0]
    if first.kind == "HIGH":
        last_high = first.price
    else:
        last_low = first.price

    for idx in range(1, len(swings)):
        prev = swings[idx - 1]
        curr = swings[idx]

        if prev.kind == "HIGH":
            last_high = prev.price
        else:
            last_low = prev.price

        reference_price = last_high if curr.kind == "HIGH" else last_low

        label = "UNDEFINED"
        if curr.kind == "HIGH":
            if last_high is None:
                label = "UNDEFINED"
            elif curr.price > last_high:
                label = "HH"
            else:
                label = "LH"
            last_high = curr.price
        else:
            if last_low is None:
                label = "UNDEFINED"
            elif curr.price > last_low:
                label = "HL"
            else:
                label = "LL"
            last_low = curr.price

        legs.append(
            SmcStructureLeg(
                from_swing=prev,
                to_swing=curr,
                label=label,
                reference_price=(
                    float(reference_price) if reference_price is not None else None
                ),
            )
        )

    return legs


def infer_trend(legs: Sequence[SmcStructureLeg]) -> SmcTrend:
    """Оцінює тренд за останніми класифікаціями high/low."""

    last_high_label = _last_label_for_kind(legs, "HIGH")
    last_low_label = _last_label_for_kind(legs, "LOW")

    if last_high_label == "HH" and last_low_label == "HL":
        return SmcTrend.UP
    if last_high_label == "LH" and last_low_label == "LL":
        return SmcTrend.DOWN
    if last_high_label or last_low_label:
        return SmcTrend.RANGE
    return SmcTrend.UNKNOWN


def detect_events(
    legs: Sequence[SmcStructureLeg],
    df: pd.DataFrame | None,
    atr_series: pd.Series | None,
    cfg: SmcCoreConfig,
) -> list[SmcStructureEvent]:
    """Повертає BOS/ChoCH події на основі ніг та ATR-порогів."""

    events: list[SmcStructureEvent] = []
    structural_bias = SmcTrend.UNKNOWN
    closes = None
    if df is not None and "close" in df.columns:
        closes = df["close"].astype(float)

    LOGGER.debug(
        "Старт обробки BOS/CHOCH",
        extra={
            "legs": len(legs),
            "has_atr": atr_series is not None,
            "bos_min_move_atr_m1": cfg.bos_min_move_atr_m1,
            "bos_min_move_pct_m1": cfg.bos_min_move_pct_m1,
        },
    )

    for leg in legs:
        if leg.label == "UNDEFINED":
            continue
        close_value = _value_at_index(closes, leg.to_swing.index)
        baseline_price = leg.reference_price
        if close_value is None or baseline_price is None:
            continue
        if not _passes_break_threshold(
            close_value,
            baseline_price,
            _value_at_index(atr_series, leg.to_swing.index),
            cfg,
        ):
            continue

        event_type: str | None = None
        direction: str | None = None

        if leg.label == "HH":
            if structural_bias == SmcTrend.DOWN:
                event_type, direction = "CHOCH", "LONG"
            else:
                event_type, direction = "BOS", "LONG"
            structural_bias = SmcTrend.UP
        elif leg.label == "LL":
            if structural_bias == SmcTrend.UP:
                event_type, direction = "CHOCH", "SHORT"
            else:
                event_type, direction = "BOS", "SHORT"
            structural_bias = SmcTrend.DOWN
        elif leg.label == "LH" and structural_bias == SmcTrend.DOWN:
            event_type, direction = "BOS", "SHORT"
        elif leg.label == "HL" and structural_bias == SmcTrend.UP:
            event_type, direction = "BOS", "LONG"

        if event_type and direction:
            events.append(
                SmcStructureEvent(
                    event_type=event_type,
                    direction=direction,
                    price_level=leg.to_swing.price,
                    time=leg.to_swing.time,
                    source_leg=leg,
                )
            )
            LOGGER.debug(
                "Сформовано структуру подій",
                extra={
                    "event_type": event_type,
                    "direction": direction,
                    "price": float(leg.to_swing.price),
                    "time": str(leg.to_swing.time),
                    "leg_label": leg.label,
                },
            )
    LOGGER.debug(
        "Завершено обробку BOS/CHOCH",
        extra={"events_total": len(events)},
    )
    return events


def _last_label_for_kind(
    legs: Sequence[SmcStructureLeg], swing_kind: str
) -> str | None:
    for leg in reversed(legs):
        if leg.to_swing.kind == swing_kind:
            return leg.label
    return None


def _value_at_index(series: pd.Series | None, idx: int) -> float | None:
    if series is None or idx < 0 or idx >= len(series):
        return None
    value = series.iloc[idx]
    return float(value) if pd.notna(value) else None


def _passes_break_threshold(
    close_value: float,
    baseline_price: float,
    atr_value: float | None,
    cfg: SmcCoreConfig,
) -> bool:
    delta = abs(close_value - baseline_price)
    atr_component = 0.0 if atr_value is None else atr_value * cfg.bos_min_move_atr_m1
    pct_component = abs(close_value) * cfg.bos_min_move_pct_m1
    threshold = max(atr_component, pct_component)
    return delta >= threshold
