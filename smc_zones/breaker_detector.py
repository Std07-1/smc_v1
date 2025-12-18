"""Breaker_v1: зони після інвалідованих PRIMARY Order Block."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, cast

import pandas as pd

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import (
    SmcInput,
    SmcLiquidityState,
    SmcStructureEvent,
    SmcStructureState,
    SmcZone,
    SmcZoneType,
)
from core.serialization import safe_float

logger = logging.getLogger("smc_zones.breaker_detector")
if not logger.handlers:  # pragma: no cover - ініціалізація логера
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler())
    logger.propagate = False


@dataclass(slots=True)
class _SweepEvent:
    time: pd.Timestamp
    level: float
    side: Literal["HIGH", "LOW"]
    source: str | None = None


def detect_breakers(
    snapshot: SmcInput,
    structure: SmcStructureState | None,
    liquidity: SmcLiquidityState | None,
    orderblocks: Sequence[SmcZone],
    cfg: SmcCoreConfig,
) -> list[SmcZone]:
    """Шукає breaker-зони за схемою OB → sweep → протилежний BOS."""

    if structure is None:
        _log_debug("Breaker_v1: пропуск — немає структури", snapshot)
        return []
    if liquidity is None:
        _log_debug("Breaker_v1: пропуск — немає ліквідності", snapshot)
        return []
    if not orderblocks:
        _log_debug("Breaker_v1: пропуск — база order block порожня", snapshot)
        return []
    if not (structure.events or structure.event_history):
        _log_debug("Breaker_v1: пропуск — історія BOS/CHOCH порожня", snapshot)
        return []

    frame = snapshot.ohlc_by_tf.get(snapshot.tf_primary)
    if frame is None or frame.empty:
        _log_debug("Breaker_v1: пропуск — немає первинного фрейму", snapshot)
        return []

    sweeps = _extract_sweep_events(liquidity)
    if not sweeps:
        _log_debug("Breaker_v1: пропуск — SFP/sweep події відсутні", snapshot)
        return []

    structure_meta = structure.meta or {}
    atr = safe_float(structure_meta.get("atr_last") or structure_meta.get("atr_median"))
    bias_context = _resolve_bias(
        structure_meta.get("bias"), getattr(structure, "bias", None)
    )
    bos_events = _collect_bos_events(structure)
    if not bos_events:
        _log_debug("Breaker_v1: пропуск — не знайдено BOS по історії", snapshot)
        return []

    breakers: list[SmcZone] = []
    for ob in orderblocks:
        if ob.zone_type is not SmcZoneType.ORDER_BLOCK:
            _log_debug(
                "Breaker_v1: зона пропущена — не ORDER_BLOCK",
                snapshot,
                zone_id=ob.zone_id,
                zone_type=str(ob.zone_type),
            )
            continue
        if ob.role != "PRIMARY":
            _log_debug(
                "Breaker_v1: зона пропущена — role не PRIMARY",
                snapshot,
                zone_id=ob.zone_id,
                role=ob.role,
            )
            continue
        if ob.origin_time is None:
            _log_debug(
                "Breaker_v1: зона пропущена — немає origin_time",
                snapshot,
                zone_id=ob.zone_id,
            )
            continue

        sweep_side = "HIGH" if ob.direction == "SHORT" else "LOW"
        target_direction: Literal["LONG", "SHORT"] = (
            "LONG" if ob.direction == "SHORT" else "SHORT"
        )
        sweep = _find_matching_sweep(ob, sweep_side, sweeps, cfg)
        if sweep is None:
            _log_debug(
                "Breaker_v1: зона пропущена — не знайдено sweep",
                snapshot,
                zone_id=ob.zone_id,
                sweep_side=sweep_side,
            )
            continue

        bos_event = _find_bos_after_sweep(
            bos_events,
            target_direction,
            sweep.time,
            ob.origin_time,
            cfg,
        )
        if bos_event is None:
            _log_debug(
                "Breaker_v1: зона пропущена — немає протилежного BOS",
                snapshot,
                zone_id=ob.zone_id,
                sweep_time=sweep.time.isoformat(),
                target_direction=target_direction,
            )
            continue

        displacement = _calc_displacement(
            sweep_level=sweep.level,
            bos_level=safe_float(bos_event.price_level),
            atr=atr,
        )
        if displacement is None:
            _log_debug(
                "Breaker_v1: зона пропущена — неможливо обчислити displacement",
                snapshot,
                zone_id=ob.zone_id,
                sweep_level=sweep.level,
                bos_level=safe_float(bos_event.price_level),
            )
            continue
        if displacement < cfg.breaker_min_displacement_atr:
            _log_debug(
                "Breaker_v1: зона пропущена — замалий displacement",
                snapshot,
                zone_id=ob.zone_id,
                displacement=displacement,
                threshold=cfg.breaker_min_displacement_atr,
            )
            continue

        row_idx = _find_row_by_timestamp(frame, bos_event.time)
        if row_idx is None:
            _log_debug(
                "Breaker_v1: зона пропущена — не знайдено рядок BOS у фреймі",
                snapshot,
                zone_id=ob.zone_id,
                bos_time=bos_event.time.isoformat(),
            )
            continue
        zone = _build_breaker_zone(
            snapshot=snapshot,
            frame=frame,
            row_index=row_idx,
            direction=target_direction,
            ob=ob,
            sweep=sweep,
            bos_event=bos_event,
            atr=atr,
            cfg=cfg,
            bias_context=bias_context,
            displacement=displacement,
        )
        if zone is None:
            _log_debug(
                "Breaker_v1: побудова зони провалена через дані свічки",
                snapshot,
                zone_id=ob.zone_id,
                bos_time=bos_event.time.isoformat(),
            )
            continue

        logger.info(
            "Breaker_v1: створено зону",
            extra={
                "symbol": snapshot.symbol,
                "tf": snapshot.tf_primary,
                "zone_id": zone.zone_id,
                "direction": zone.direction,
                "source_ob": ob.zone_id,
                "displacement_atr": displacement,
            },
        )
        breakers.append(zone)

    return breakers


def _extract_sweep_events(liquidity: SmcLiquidityState) -> list[_SweepEvent]:
    meta = liquidity.meta or {}
    raw_events = meta.get("sfp_events")
    sweeps: list[_SweepEvent] = []
    if not isinstance(raw_events, Sequence):
        return sweeps
    for entry in raw_events:
        if not isinstance(entry, dict):
            continue
        side = entry.get("side")
        level = safe_float(entry.get("level"))
        ts_raw = entry.get("time")
        if side not in {"HIGH", "LOW"} or level is None:
            continue
        ts = _safe_timestamp(ts_raw)
        if ts is None:
            continue
        source_value = entry.get("source")
        source = str(source_value) if source_value is not None else None
        sweeps.append(_SweepEvent(time=ts, level=level, side=side, source=source))
    sweeps.sort(key=lambda e: e.time)
    return sweeps


def _find_matching_sweep(
    ob: SmcZone,
    sweep_side: Literal["HIGH", "LOW"],
    sweeps: Sequence[_SweepEvent],
    cfg: SmcCoreConfig,
) -> _SweepEvent | None:
    tolerance = _breaker_tolerance(ob, cfg)
    target_level = ob.price_max if sweep_side == "HIGH" else ob.price_min
    origin_time = _ensure_utc(ob.origin_time)
    if origin_time is None:
        return None
    for sweep in sweeps:
        if sweep.side != sweep_side:
            continue
        sweep_time = _ensure_utc(sweep.time)
        if sweep_time is None:
            continue
        if sweep_time < origin_time:
            continue
        if (sweep_time - origin_time) > timedelta(
            minutes=cfg.breaker_max_sweep_delay_minutes
        ):
            break
        if abs(sweep.level - target_level) <= tolerance:
            return _SweepEvent(
                time=sweep_time,
                level=sweep.level,
                side=sweep.side,
                source=sweep.source,
            )
    return None


def _breaker_tolerance(ob: SmcZone, cfg: SmcCoreConfig) -> float:
    price_span = abs(float(ob.price_max) - float(ob.price_min))
    anchor = max(abs(float(ob.price_max)), abs(float(ob.price_min)), 1.0)
    rel_tol = anchor * max(cfg.breaker_level_tolerance_pct, 1e-5)
    return max(rel_tol, price_span * 0.15)


def _collect_bos_events(structure: SmcStructureState) -> list[SmcStructureEvent]:
    candidates = list(structure.events or []) + list(structure.event_history or [])
    filtered: list[SmcStructureEvent] = []
    for event in candidates:
        if event.event_type not in {"BOS", "CHOCH"}:
            continue
        if event.direction not in {"LONG", "SHORT"}:
            continue
        filtered.append(event)
    filtered.sort(key=lambda ev: ev.time)
    return filtered


def _find_bos_after_sweep(
    events: Sequence[SmcStructureEvent],
    direction: Literal["LONG", "SHORT"],
    sweep_time: pd.Timestamp,
    origin_time: pd.Timestamp,
    cfg: SmcCoreConfig,
) -> SmcStructureEvent | None:
    sweep_time_utc = _ensure_utc(sweep_time)
    origin_time_utc = _ensure_utc(origin_time)
    if sweep_time_utc is None or origin_time_utc is None:
        return None
    max_age = timedelta(minutes=cfg.breaker_max_ob_age_minutes)
    max_delay = timedelta(minutes=cfg.breaker_max_sweep_delay_minutes)
    for event in events:
        if event.direction != direction:
            continue
        event_time = _ensure_utc(event.time)
        if event_time is None:
            continue
        if event_time < sweep_time_utc:
            continue
        if event_time - sweep_time_utc > max_delay:
            return None
        if event_time - origin_time_utc > max_age:
            return None
        return event
    return None


def _find_row_by_timestamp(frame: pd.DataFrame, ts: pd.Timestamp) -> int | None:
    if not isinstance(ts, pd.Timestamp):
        return None
    target = _ensure_utc(ts)
    if target is None:
        return None
    if isinstance(frame.index, pd.DatetimeIndex):
        try:
            index = frame.index
            index_utc = index if index.tz is not None else index.tz_localize("UTC")
            index_utc = index_utc.tz_convert("UTC")
            delta_series = pd.Series(
                index_utc - target,
                index=pd.RangeIndex(len(index_utc)),
            )
            if not delta_series.isna().all():
                delta_seconds = delta_series.abs().dt.total_seconds().dropna()
                if not delta_seconds.empty:
                    idx = int(delta_seconds.idxmin())
                    if 0 <= idx < len(frame):
                        return idx
        except Exception:
            pass
    for column in ("timestamp", "open_time", "close_time"):
        if column in frame.columns:
            try:
                series = pd.to_datetime(frame[column], utc=True, errors="coerce")
            except Exception:
                continue
            diffs = (series - target).abs()
            if diffs.isna().all():
                continue
            idx = int(diffs.to_numpy().argmin())
            if 0 <= idx < len(frame):
                return idx
    return None


def _build_breaker_zone(
    snapshot: SmcInput,
    frame: pd.DataFrame,
    row_index: int,
    direction: Literal["LONG", "SHORT"],
    ob: SmcZone,
    sweep: _SweepEvent,
    bos_event: SmcStructureEvent,
    atr: float | None,
    cfg: SmcCoreConfig,
    bias_context: Literal["LONG", "SHORT", "NEUTRAL", "UNKNOWN"] = "UNKNOWN",
    displacement: float | None = None,
) -> SmcZone | None:
    try:
        row = frame.iloc[row_index]
    except IndexError:
        return None

    high = safe_float(row.get("high"))
    low = safe_float(row.get("low"))
    open_v = safe_float(row.get("open"))
    close_v = safe_float(row.get("close"))
    origin_time = _resolve_row_timestamp(row, frame.index[row_index])
    if None in {high, low, open_v, close_v} or origin_time is None:
        return None

    high = cast(float, high)
    low = cast(float, low)
    open_v = cast(float, open_v)
    close_v = cast(float, close_v)

    full_range = max(high - low, 1e-9)
    body_high = max(open_v, close_v)
    body_low = min(open_v, close_v)
    body_pct = (body_high - body_low) / full_range

    if body_pct >= cfg.breaker_min_body_pct:
        zone_low = body_low
        zone_high = body_high
        entry_mode: Literal["BODY_05", "WICK_05"] = "BODY_05"
    else:
        zone_low = low
        zone_high = high
        entry_mode = "WICK_05"

    span = max(zone_high - zone_low, 1e-9)
    atr_value = atr if atr and atr > 0 else span
    strength = max(0.1, min(span / atr_value, 3.0))
    confidence = max(0.25, min(0.5 + 0.2 * min(body_pct, 1.0), 0.95))

    prefix = f"brk_{snapshot.symbol.lower()}_{snapshot.tf_primary}"
    zone_id = f"{prefix}_{row_index}"
    reference_event_id = f"structure_event_{int(bos_event.time.value)}"

    role = _role_from_bias(bias_context, direction)

    zone = SmcZone(
        zone_type=SmcZoneType.BREAKER,
        price_min=min(zone_low, zone_high),
        price_max=max(zone_low, zone_high),
        timeframe=snapshot.tf_primary,
        origin_time=origin_time,
        direction=direction,
        role=role,
        strength=strength,
        confidence=confidence,
        components=[
            "breaker",
            ob.zone_id or "unknown_ob",
            reference_event_id,
        ],
        zone_id=zone_id,
        entry_mode=entry_mode,
        quality="STRONG" if body_pct >= cfg.breaker_min_body_pct else "MEDIUM",
        reference_leg_id=(
            str(bos_event.source_leg.label)
            if getattr(bos_event, "source_leg", None)
            else None
        ),
        reference_event_id=reference_event_id,
        bias_at_creation=bias_context,
        notes="",
        meta={},
    )

    zone_center = _zone_center(ob)

    zone.meta.update(
        {
            "derived_from_ob_id": ob.zone_id,
            "source_orderblock_id": ob.zone_id,
            "sweep_time": sweep.time.isoformat(),
            "sweep_level": sweep.level,
            "sweep_source": sweep.source,
            "bos_time": bos_event.time.isoformat(),
            "bos_event_type": bos_event.event_type,
            "break_event_id": reference_event_id,
            "breaker_age_min": _minutes_between(ob.origin_time, bos_event.time),
            "distance_to_sweep": (
                abs(zone_center - sweep.level) if zone_center is not None else None
            ),
            "displacement_atr": displacement,
            "breaker_params": {
                "max_ob_age_min": cfg.breaker_max_ob_age_minutes,
                "max_sweep_delay_min": cfg.breaker_max_sweep_delay_minutes,
                "level_tolerance_pct": cfg.breaker_level_tolerance_pct,
                "min_body_pct": cfg.breaker_min_body_pct,
                "min_displacement_atr": cfg.breaker_min_displacement_atr,
            },
        }
    )
    return zone


def _zone_center(zone: SmcZone) -> float | None:
    price_min = safe_float(zone.price_min)
    price_max = safe_float(zone.price_max)
    if price_min is None or price_max is None:
        return None
    return (price_min + price_max) / 2.0


def _resolve_row_timestamp(row: pd.Series, index_value: object) -> pd.Timestamp | None:
    for column in ("timestamp", "close_time", "open_time"):
        if column in row and pd.notna(row[column]):
            try:
                return pd.Timestamp(row[column])
            except Exception:
                continue
    if isinstance(index_value, pd.Timestamp):
        return index_value
    if isinstance(index_value, (datetime, date, str, int, float)):
        try:
            return pd.Timestamp(index_value)
        except Exception:
            return None
    return None


def _safe_timestamp(value: object) -> pd.Timestamp | None:
    if isinstance(value, pd.Timestamp):
        return _ensure_utc(value)
    if isinstance(value, (int, float)):
        try:
            return pd.Timestamp(float(value), unit="s", tz="UTC")
        except Exception:
            return None
    if isinstance(value, str):
        try:
            return pd.Timestamp(value, tz="UTC")
        except Exception:
            return None
    return None


def _minutes_between(start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    if not isinstance(start, pd.Timestamp) or not isinstance(end, pd.Timestamp):
        return None
    start_utc = _ensure_utc(start)
    end_utc = _ensure_utc(end)
    if start_utc is None or end_utc is None:
        return None
    return round((end_utc - start_utc).total_seconds() / 60.0, 2)


def _calc_displacement(
    sweep_level: float | None,
    bos_level: float | None,
    atr: float | None,
) -> float | None:
    if sweep_level is None or bos_level is None or atr is None or atr <= 0:
        return None
    return abs(float(bos_level) - float(sweep_level)) / atr


def _resolve_bias(
    *candidates: object,
) -> Literal["LONG", "SHORT", "NEUTRAL", "UNKNOWN"]:
    for candidate in candidates:
        if isinstance(candidate, str):
            value = candidate.upper()
            if value in {"LONG", "SHORT", "NEUTRAL"}:
                return value  # type: ignore[return-value]
    return "UNKNOWN"


def _role_from_bias(
    bias: Literal["LONG", "SHORT", "NEUTRAL", "UNKNOWN"],
    direction: Literal["LONG", "SHORT"],
) -> Literal["PRIMARY", "COUNTERTREND", "NEUTRAL"]:
    if bias == "UNKNOWN":
        return "NEUTRAL"
    if bias == "NEUTRAL":
        return "NEUTRAL"
    return "PRIMARY" if bias == direction else "COUNTERTREND"


def _ensure_utc(ts: pd.Timestamp | None) -> pd.Timestamp | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        try:
            return ts.tz_localize("UTC")
        except (TypeError, ValueError):
            return None
    try:
        return ts.tz_convert("UTC")
    except Exception:
        return None


def _log_debug(message: str, snapshot: SmcInput, **extra: object) -> None:
    ctx: dict[str, object] = {
        "symbol": snapshot.symbol,
        "tf": snapshot.tf_primary,
    }
    ctx.update(extra)
    logger.debug(message, extra=ctx)


def detect_breaker_zones(
    snapshot: SmcInput,
    structure: SmcStructureState | None,
    liquidity: SmcLiquidityState | None,
    orderblocks: Sequence[SmcZone],
    cfg: SmcCoreConfig,
) -> list[SmcZone]:
    """Залишено для сумісності: проксі до detect_breakers."""

    return detect_breakers(snapshot, structure, liquidity, orderblocks, cfg)
