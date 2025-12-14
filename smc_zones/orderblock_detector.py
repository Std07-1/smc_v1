"""Детектор Order Block згідно з підетапом 4.2 (OB_v1)."""

from __future__ import annotations

import logging
from typing import Any, Literal, cast

import pandas as pd
from rich.logging import RichHandler

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import (
    SmcInput,
    SmcStructureEvent,
    SmcStructureLeg,
    SmcStructureState,
    SmcZone,
    SmcZoneType,
)
from utils.rich_console import get_rich_console

# ───────────────────────────── Логування ─────────────────────────────
logger = logging.getLogger("smc_zones.orderblock_detector")
if not logger.handlers:  # захист від повторної ініціалізації
    logger.setLevel(logging.INFO)
    # show_path=True для відображення файлу/рядка у WARN/ERROR
    logger.addHandler(RichHandler(console=get_rich_console(), show_path=True))
    logger.propagate = False


def _calc_duration_seconds(start: Any, end: Any) -> float | None:
    if start is None or end is None:
        return None
    try:
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
    except Exception:
        return None
    if pd.isna(start_ts) or pd.isna(end_ts):
        return None
    delta = end_ts - start_ts
    return max(delta.total_seconds(), 0.0)


def _leg_log_context(
    leg: SmcStructureLeg, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    context = dict(extra or {})
    context["leg_label"] = getattr(leg, "label", "UNKNOWN")
    from_swing = getattr(leg, "from_swing", None)
    to_swing = getattr(leg, "to_swing", None)
    if from_swing is not None:
        context["leg_from_index"] = getattr(from_swing, "index", None)
        if getattr(from_swing, "price", None) is not None:
            context["leg_from_price"] = float(from_swing.price)
        if getattr(from_swing, "time", None) is not None:
            context["leg_from_time"] = _format_ts(from_swing.time)
    if to_swing is not None:
        context["leg_to_index"] = getattr(to_swing, "index", None)
        if getattr(to_swing, "price", None) is not None:
            context["leg_to_price"] = float(to_swing.price)
        if getattr(to_swing, "time", None) is not None:
            context["leg_to_time"] = _format_ts(to_swing.time)
    leg_duration = _calc_duration_seconds(
        getattr(from_swing, "time", None), getattr(to_swing, "time", None)
    )
    if leg_duration is not None:
        context["leg_duration_sec"] = leg_duration
    direction = _leg_direction(leg)
    if direction is not None:
        context["leg_direction"] = direction
    return context


def detect_order_blocks(
    snapshot: SmcInput,
    structure: SmcStructureState | None,
    cfg: SmcCoreConfig,
) -> list[SmcZone]:
    """Шукає базові Order Block-и по структурі та імпульсним ногам."""

    if structure is None or not structure.legs:
        logger.debug(
            "OB_v1: пропуск — немає структури чи ніг",
            extra={"symbol": snapshot.symbol, "tf": snapshot.tf_primary},
        )
        return []
    frame = snapshot.ohlc_by_tf.get(snapshot.tf_primary)
    if frame is None or frame.empty:
        logger.debug(
            "OB_v1: пропуск — порожній фрейм",
            extra={"symbol": snapshot.symbol, "tf": snapshot.tf_primary},
        )
        return []
    required_cols = {"open", "close", "high", "low"}
    if not required_cols.issubset(frame.columns):
        logger.warning(
            "OB_v1: пропуск — відсутні стовпці %s",
            required_cols - set(frame.columns),
            extra={"symbol": snapshot.symbol, "tf": snapshot.tf_primary},
        )
        return []

    atr = float(
        structure.meta.get("atr_last") or structure.meta.get("atr_median") or 0.0
    )
    bias = str(structure.meta.get("bias") or structure.bias or "NEUTRAL").upper()
    structure_events = list(structure.event_history or structure.events or [])
    zones: list[SmcZone] = []

    logger.debug(
        "OB_v1: старт детекції",
        extra={
            "symbol": snapshot.symbol,
            "tf": snapshot.tf_primary,
            "legs": len(structure.legs),
            "bias": bias,
        },
    )

    for _, leg in enumerate(structure.legs):
        direction = _leg_direction(leg)
        if direction is None:
            continue
        if direction == "LONG" and leg.label not in {"HH", "HL"}:
            continue
        if direction == "SHORT" and leg.label not in {"LH", "LL"}:
            continue

        leg_span = _resolve_leg_span(frame, leg)
        if leg_span is None:
            continue
        start_pos, end_pos = leg_span
        if end_pos <= start_pos:
            continue
        bar_count = end_pos - start_pos + 1
        if bar_count > cfg.ob_leg_max_bars:
            logger.debug(
                "OB_v1: нога %s пропущена — тривалість %s > %s (цінa %.4f→%.4f, час %s→%s)",
                leg.label,
                bar_count,
                cfg.ob_leg_max_bars,
                float(leg.from_swing.price),
                float(leg.to_swing.price),
                _format_ts(leg.from_swing.time),
                _format_ts(leg.to_swing.time),
                extra=_leg_log_context(
                    leg,
                    {
                        "symbol": snapshot.symbol,
                        "tf": snapshot.tf_primary,
                        "bar_count": bar_count,
                        "leg_max_bars": cfg.ob_leg_max_bars,
                    },
                ),
            )
            continue

        amplitude = abs(float(leg.to_swing.price) - float(leg.from_swing.price))
        if atr > 0 and amplitude < cfg.ob_leg_min_atr_mul * atr:
            logger.debug(
                "OB_v1: нога %s пропущена — амплітуда %.4f < порога (цінa %.4f→%.4f, час %s→%s)",
                leg.label,
                amplitude,
                float(leg.from_swing.price),
                float(leg.to_swing.price),
                _format_ts(leg.from_swing.time),
                _format_ts(leg.to_swing.time),
                extra=_leg_log_context(
                    leg,
                    {
                        "symbol": snapshot.symbol,
                        "tf": snapshot.tf_primary,
                        "atr": atr,
                        "ob_leg_min_atr_mul": cfg.ob_leg_min_atr_mul,
                        "leg_amplitude": amplitude,
                    },
                ),
            )
            continue
        if amplitude <= 0:
            continue

        candidate_pos = _find_ob_candidate(frame, start_pos, direction, cfg)
        if candidate_pos is None:
            logger.debug(
                "OB_v1: не знайдено candlestick для ноги %s",
                leg.label,
                extra=_leg_log_context(
                    leg,
                    {
                        "symbol": snapshot.symbol,
                        "tf": snapshot.tf_primary,
                        "prelude_max_bars": cfg.ob_prelude_max_bars,
                    },
                ),
            )
            continue

        break_event = _leg_break_event(structure_events, leg, direction)
        if break_event is None:
            """
            logger.debug(
                "OB_v1: немає break події для ноги %s",
                leg.label,
                extra=_leg_log_context(
                    leg,
                    {
                        "symbol": snapshot.symbol,
                        "tf": snapshot.tf_primary,
                        "structure_events": len(structure_events),
                    },
                ),
            )
            """
            continue
        zone = _build_zone_from_row(
            snapshot=snapshot,
            frame=frame,
            row_pos=candidate_pos,
            direction=direction,
            leg=leg,
            bias=bias,
            atr=atr,
            amplitude=amplitude,
            bar_count=bar_count,
            break_event=break_event,
            cfg=cfg,
        )
        if zone is None:
            logger.debug(
                "OB_v1: побудова зони провалена для ноги %s",
                leg.label,
                extra=_leg_log_context(
                    leg,
                    {
                        "symbol": snapshot.symbol,
                        "tf": snapshot.tf_primary,
                        "candidate_pos": candidate_pos,
                        "direction": direction,
                    },
                ),
            )
            continue

        logger.info(
            "OB_v1: створено зону",
            extra={
                "symbol": snapshot.symbol,
                "tf": snapshot.tf_primary,
                "zone_id": zone.zone_id,
                "direction": zone.direction,
                "role": zone.role,
                **_leg_log_context(
                    leg,
                    {
                        "bar_count": bar_count,
                        "amplitude": amplitude,
                        "candidate_pos": candidate_pos,
                    },
                ),
            },
        )
        zones.append(zone)

    logger.debug(
        "OB_v1: завершено детекцію",
        extra={
            "symbol": snapshot.symbol,
            "tf": snapshot.tf_primary,
            "zones_total": len(zones),
        },
    )
    return zones


def _leg_direction(leg: SmcStructureLeg) -> Literal["LONG", "SHORT"] | None:
    if leg.label in {"HH", "HL"}:
        return "LONG"
    if leg.label in {"LH", "LL"}:
        return "SHORT"
    return None


def _resolve_leg_span(
    frame: pd.DataFrame, leg: SmcStructureLeg
) -> tuple[int, int] | None:
    start_pos = _resolve_position(frame, leg.from_swing.index)
    end_pos = _resolve_position(frame, leg.to_swing.index)
    if start_pos is None or end_pos is None:
        return None
    return min(start_pos, end_pos), max(start_pos, end_pos)


def _resolve_position(frame: pd.DataFrame, raw_index: int) -> int | None:
    try:
        loc = frame.index.get_loc(raw_index)
        if isinstance(loc, slice):
            return int(loc.start)
        return int(loc)
    except (KeyError, AttributeError, TypeError):
        pass
    if 0 <= raw_index < len(frame):
        return int(raw_index)
    return None


def _find_ob_candidate(
    frame: pd.DataFrame,
    start_pos: int,
    direction: Literal["LONG", "SHORT"],
    cfg: SmcCoreConfig,
) -> int | None:
    pre_start = max(0, start_pos - cfg.ob_prelude_max_bars)
    window = frame.iloc[pre_start : start_pos + 1]
    if window.empty:
        return None

    def _is_opposite(row: pd.Series) -> bool:
        open_v = float(row["open"])
        close_v = float(row["close"])
        return close_v < open_v if direction == "LONG" else close_v > open_v

    for rel in range(len(window) - 1, -1, -1):
        row = window.iloc[rel]
        if _is_opposite(row):
            return pre_start + rel

    # fallback: найбільш екстремальна свічка у вікні
    if direction == "LONG":
        rel_pos = int(window["low"].astype(float).to_numpy().argmin())
    else:
        rel_pos = int(window["high"].astype(float).to_numpy().argmax())
    return pre_start + rel_pos


def _leg_break_event(
    events: list[SmcStructureEvent],
    leg: SmcStructureLeg,
    direction: Literal["LONG", "SHORT"],
) -> SmcStructureEvent | None:
    target_sig = _leg_signature(leg)
    for event in events:
        if event.event_type not in {"BOS", "CHOCH"}:
            continue
        if event.direction != direction:
            continue
        if event.source_leg is None:
            continue
        if _leg_signature(event.source_leg) == target_sig:
            return event
    return None


def _leg_signature(leg: SmcStructureLeg) -> tuple[int, int, str]:
    return (
        int(getattr(leg.from_swing, "index", -1)),
        int(getattr(leg.to_swing, "index", -1)),
        str(getattr(leg, "label", "")),
    )


def _build_zone_from_row(
    snapshot: SmcInput,
    frame: pd.DataFrame,
    row_pos: int,
    direction: Literal["LONG", "SHORT"],
    leg: SmcStructureLeg,
    bias: str,
    atr: float,
    amplitude: float,
    bar_count: int,
    break_event: SmcStructureEvent | None,
    cfg: SmcCoreConfig,
) -> SmcZone | None:
    try:
        row = frame.iloc[row_pos]
    except IndexError:
        return None

    high = float(row["high"])
    low = float(row["low"])
    open_v = float(row["open"])
    close_v = float(row["close"])
    full_range = max(high - low, 1e-9)
    body_high = max(open_v, close_v)
    body_low = min(open_v, close_v)
    body_abs = body_high - body_low
    body_pct = body_abs / full_range
    wick_top_pct = (high - body_high) / full_range
    wick_bottom_pct = (body_low - low) / full_range

    if break_event is None:
        return None
    if body_abs < cfg.ob_body_min_pct * amplitude:
        return None
    if body_pct < cfg.ob_body_domination_pct:
        return None

    zone_low = low
    zone_high = high
    entry_mode = "WICK_05"
    if body_pct >= cfg.ob_body_domination_pct:
        zone_low = body_low
        zone_high = body_high
        entry_mode = "BODY_05"
    elif body_pct <= cfg.ob_body_min_pct:
        zone_low = body_low
        zone_high = body_high
        entry_mode = "BODY_TOUCH"
    elif direction == "SHORT":
        entry_mode = "WICK_TOUCH"

    strength = amplitude / max(atr, 1e-9) if atr > 0 else body_pct * 2.0
    strength = max(0.1, min(strength, 3.0))
    confidence = 0.45 + 0.25 * min(body_pct, 1.0) + 0.15
    confidence = max(0.2, min(confidence, 0.95))

    quality = "STRONG"
    role = _derive_role(direction, bias)

    origin_time = _extract_timestamp(row)
    leg_id = f"leg_{leg.from_swing.index}_{leg.to_swing.index}"
    zone_id = f"ob_{snapshot.symbol.lower()}_{snapshot.tf_primary}_{row_pos}_{leg.to_swing.index}"
    reference_event_id = f"structure_event_{int(break_event.time.value)}"
    reference_event_type = break_event.event_type

    bias_value = bias if bias in {"LONG", "SHORT", "NEUTRAL"} else "UNKNOWN"
    bias_at_creation = cast(Literal["LONG", "SHORT", "NEUTRAL", "UNKNOWN"], bias_value)

    zone = SmcZone(
        zone_type=SmcZoneType.ORDER_BLOCK,
        price_min=min(zone_low, zone_high),
        price_max=max(zone_low, zone_high),
        timeframe=snapshot.tf_primary,
        origin_time=origin_time,
        direction=direction,
        role=role,
        strength=strength,
        confidence=confidence,
        components=["orderblock", leg_id],
        zone_id=zone_id,
        entry_mode=entry_mode,
        quality=quality,
        reference_leg_id=leg_id,
        reference_event_id=reference_event_id,
        bias_at_creation=bias_at_creation,
        notes="",
        meta={},
    )

    zone.meta.update(
        {
            "body_pct": body_pct,
            "wick_top_pct": wick_top_pct,
            "wick_bottom_pct": wick_bottom_pct,
            "entry_mode": entry_mode,
            "role": role,
            "bias_at_creation": bias_at_creation,
            "reference_event_type": reference_event_type,
            "bar_count": bar_count,
            "amplitude": amplitude,
            "quality": quality,
        }
    )
    return zone


def _format_ts(value: Any) -> str:
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    try:
        return pd.Timestamp(value).isoformat()
    except Exception:
        return str(value)


def _derive_role(
    direction: Literal["LONG", "SHORT"], bias: str
) -> Literal["PRIMARY", "COUNTERTREND", "NEUTRAL"]:
    bias = bias.upper()
    if bias not in {"LONG", "SHORT"}:
        return "NEUTRAL"
    return "PRIMARY" if bias == direction else "COUNTERTREND"


def _extract_timestamp(row: pd.Series) -> pd.Timestamp:
    """
    Витягує відмітку часу з рядка DataFrame або повертає поточний час, якщо її немає.

        :param row: Рядок з OHLCV-даними.
        :type row: pd.Series
        :return: Відповідна мітка часу.
        :rtype: pd.Timestamp
    """

    for column in ("open_time", "close_time", "time", "timestamp"):
        if column in row and pd.notna(row[column]):
            try:
                return pd.Timestamp(row[column])
            except Exception:
                continue
    if isinstance(row.name, pd.Timestamp):
        return row.name
    return pd.Timestamp.utcnow()
