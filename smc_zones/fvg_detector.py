"""FVG/Imbalance_v1 скелет: пошук міні-gap'ів (Fair Value Gap)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Literal

import pandas as pd
from rich.console import Console
from rich.logging import RichHandler

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import SmcStructureState, SmcZone, SmcZoneType
from utils.utils import safe_float

logger = logging.getLogger("smc_zones.fvg_detector")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    logger.addHandler(RichHandler(console=Console(stderr=True), show_path=False))
    logger.propagate = False


def detect_fvg_zones(
    structure: SmcStructureState | None,
    cfg: SmcCoreConfig,
) -> list[SmcZone]:
    """Повертає список імбаланс-зон за 3-свічковим шаблоном."""

    if structure is None:
        return []

    frame = _primary_frame(structure)
    if frame is None or len(frame) < 3:
        return []

    meta = structure.meta or {}
    atr = safe_float(meta.get("atr_last") or meta.get("atr_median"))
    bias_context = _resolve_bias(meta.get("bias"), getattr(structure, "bias", None))
    last_timestamp = _row_timestamp(frame.iloc[-1])

    zones: list[SmcZone] = []
    for idx in range(len(frame) - 2):
        first = frame.iloc[idx]
        third = frame.iloc[idx + 2]
        zone = _build_fvg_zone(
            first_row=first,
            third_row=third,
            idx=idx,
            structure=structure,
            atr=atr,
            bias_context=bias_context,
            last_timestamp=last_timestamp,
            cfg=cfg,
        )
        if zone is not None:
            zones.append(zone)
    return zones


def _build_fvg_zone(
    *,
    first_row: pd.Series,
    third_row: pd.Series,
    idx: int,
    structure: SmcStructureState,
    atr: float | None,
    bias_context: Literal["LONG", "SHORT", "NEUTRAL", "UNKNOWN"],
    last_timestamp: pd.Timestamp | None,
    cfg: SmcCoreConfig,
) -> SmcZone | None:
    high_first = safe_float(first_row.get("high"))
    low_first = safe_float(first_row.get("low"))
    high_third = safe_float(third_row.get("high"))
    low_third = safe_float(third_row.get("low"))
    if (
        high_first is None
        or low_first is None
        or high_third is None
        or low_third is None
    ):
        return None

    direction: Literal["LONG", "SHORT"] | None = None
    price_min: float | None = None
    price_max: float | None = None

    if low_third > high_first:
        direction = "LONG"
        price_min = high_first
        price_max = low_third
    elif high_third < low_first:
        direction = "SHORT"
        price_min = high_third
        price_max = low_first

    if direction is None or price_min is None or price_max is None:
        return None

    gap = abs(price_max - price_min)
    if gap <= 0:
        return None

    price_ref = (price_min + price_max) / 2.0
    atr_condition = bool(atr and atr > 0 and gap >= cfg.fvg_min_gap_atr * atr)
    pct_condition = bool(price_ref > 0 and gap / price_ref >= cfg.fvg_min_gap_pct)
    if not (atr_condition or pct_condition):
        return None

    origin_time = _row_timestamp(third_row)
    if origin_time is None:
        return None

    age_min = None
    if last_timestamp is not None:
        age_min = round((last_timestamp - origin_time).total_seconds() / 60.0, 2)
        if age_min > cfg.fvg_max_age_minutes:
            return None

    atr_value = atr if atr and atr > 0 else gap
    strength = min(max(gap / atr_value, 0.1), 3.0)
    confidence = 0.35 if direction == bias_context else 0.2

    zone_id = f"fvg_{structure.primary_tf or 'primary'}_{origin_time.value}_{idx}"
    zone = SmcZone(
        zone_type=SmcZoneType.IMBALANCE,
        price_min=min(price_min, price_max),
        price_max=max(price_min, price_max),
        timeframe=structure.primary_tf or "",
        origin_time=origin_time,
        direction=direction,
        role=_role_from_bias(bias_context, direction),
        strength=strength,
        confidence=confidence,
        components=["fvg", f"gap_idx_{idx}"],
        zone_id=zone_id,
        entry_mode="WICK_05",
        quality="MEDIUM" if confidence >= 0.3 else "WEAK",
        bias_at_creation=bias_context,
        notes="",
        meta={
            "gap": gap,
            "gap_atr": gap / atr if atr and atr > 0 else None,
            "gap_pct": gap / price_ref if price_ref else None,
            "age_min": age_min,
            "source_idx": idx,
        },
    )
    return zone


def _primary_frame(structure: SmcStructureState) -> pd.DataFrame | None:
    meta = structure.meta or {}
    candidates: Sequence[Any] | pd.DataFrame | None = (
        meta.get("primary_bars")
        or meta.get("recent_bars")
        or meta.get("bars")
        or meta.get("frame")
    )
    if candidates is None:
        return None
    if isinstance(candidates, pd.DataFrame):
        frame = candidates.copy()
    else:
        try:
            frame = pd.DataFrame(list(candidates))
        except Exception:
            return None
    required = {"high", "low"}
    if frame.empty or not required.issubset(frame.columns):
        return None
    return frame.reset_index(drop=True)


def _row_timestamp(row: pd.Series) -> pd.Timestamp | None:
    for key in ("timestamp", "open_time", "close_time", "time"):
        value = row.get(key)
        if value is None:
            continue
        try:
            ts = pd.Timestamp(value)
        except Exception:
            continue
        if ts.tzinfo is None:
            try:
                ts = ts.tz_localize("UTC")
            except (TypeError, ValueError):
                continue
        else:
            ts = ts.tz_convert("UTC")
        return ts
    return None


def _resolve_bias(*candidates: Any) -> Literal["LONG", "SHORT", "NEUTRAL", "UNKNOWN"]:
    for candidate in candidates:
        if isinstance(candidate, str):
            upper = candidate.upper()
            if upper in {"LONG", "SHORT", "NEUTRAL"}:
                return upper  # type: ignore[return-value]
    return "UNKNOWN"


def _role_from_bias(
    bias: Literal["LONG", "SHORT", "NEUTRAL", "UNKNOWN"],
    direction: Literal["LONG", "SHORT"],
) -> Literal["PRIMARY", "COUNTERTREND", "NEUTRAL"]:
    if bias in {"UNKNOWN", "NEUTRAL"}:
        return "NEUTRAL"
    return "PRIMARY" if bias == direction else "COUNTERTREND"
