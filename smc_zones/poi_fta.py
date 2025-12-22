"""Stage4 POI/FTA: скоринг і відбір активних зон.

Ціль:
- перетворити множину зон (FVG/OB/Breaker) у малий список POI, які реально мають сенс
    для трейдера "де чекати реакцію";
- тримати на екрані максимум 1–3 активні POI на сторону, решту вважати архівом.

Вихід:
- `poi_zones` (SmcZone з meta: score/filled_pct/why/poi_type);
- `active_poi` (JSON-friendly список dict у meta SmcZonesState).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any, Literal

import pandas as pd

from core.serialization import safe_float
from smc_core.config import SmcCoreConfig
from smc_core.smc_types import (
    SmcInput,
    SmcLiquidityState,
    SmcStructureState,
    SmcZone,
    SmcZoneType,
)

PoiType = Literal["FVG", "OB", "BREAKER"]


def build_active_poi_zones(
    *,
    snapshot: SmcInput,
    structure: SmcStructureState,
    liquidity: SmcLiquidityState | None,
    zones: Sequence[SmcZone],
    cfg: SmcCoreConfig,
) -> tuple[list[SmcZone], list[dict[str, Any]], dict[str, Any]]:
    """Будує список активних POI (1–3 на сторону) + JSON-friendly payload.

    Важливо:
    - працюємо без сторонніх залежностей і без I/O;
    - не ламаємо базові контракти: додаємо лише meta/poi_zones.
    """

    frame = snapshot.ohlc_by_tf.get(snapshot.tf_primary)
    if frame is None or frame.empty:
        return [], [], {"poi_candidates": 0, "poi_active": 0, "poi_archived": 0}

    ref_price = _last_close(frame)
    if ref_price is None:
        return [], [], {"poi_candidates": 0, "poi_active": 0, "poi_archived": 0}

    atr = safe_float(
        (structure.meta or {}).get("atr_last")
        or (structure.meta or {}).get("atr_median")
    )
    bias = str(
        (structure.meta or {}).get("bias") or structure.bias or "NEUTRAL"
    ).upper()
    eq_level = _extract_eq_level(structure)

    candidates: list[tuple[float, SmcZone, dict[str, Any]]] = []
    archived = 0
    archived_invalidated = 0
    archived_filled = 0
    archived_score_le_0 = 0
    archived_wide_span_atr = 0
    skipped_non_poi_type = 0
    skipped_bad_direction = 0
    for z in zones or []:
        poi_type = _classify_poi_type(z)
        if poi_type is None:
            skipped_non_poi_type += 1
            continue
        if z.direction not in {"LONG", "SHORT"}:
            skipped_bad_direction += 1
            continue

        filled_pct = _compute_filled_pct(
            frame=frame,
            zone=z,
        )
        invalidated = _is_invalidated(frame=frame, zone=z)
        if invalidated:
            archived += 1
            archived_invalidated += 1
            continue
        if filled_pct >= 1.0:
            archived += 1
            archived_filled += 1
            continue

        # distance_atr та state потрібні UI, щоб трейдер читав POI як SMC, а не як «просто S/R».
        center = (float(z.price_min) + float(z.price_max)) / 2.0
        dist_atr = None
        if atr is not None and atr > 0:
            dist_atr = abs(center - float(ref_price)) / float(atr)

        if filled_pct > 0:
            state = "TOUCHED"
        else:
            state = "FRESH"

        span_atr: float | None = None
        if atr is not None and atr > 0:
            try:
                pmin = float(z.price_min)
                pmax = float(z.price_max)
                span_atr = abs(pmax - pmin) / float(atr)
            except Exception:
                span_atr = None

        # Випадок D: надто широка зона — скоріше range/area.
        # Для POI це шум, тому архівуємо.
        if (
            span_atr is not None
            and cfg.max_zone_span_atr is not None
            and cfg.max_zone_span_atr > 0
            and float(span_atr) > float(cfg.max_zone_span_atr)
        ):
            archived += 1
            archived_wide_span_atr += 1
            continue

        score, why = _score_zone(
            zone=z,
            poi_type=poi_type,
            ref_price=ref_price,
            atr=atr,
            bias=bias,
            eq_level=eq_level,
            liquidity=liquidity,
            cfg=cfg,
            filled_pct=filled_pct,
        )
        if score <= 0:
            archived += 1
            archived_score_le_0 += 1
            continue

        meta_patch = {
            "poi_type": poi_type,
            "filled_pct": round(float(filled_pct), 4),
            "score": round(float(score), 3),
            "why": why,
            "distance_atr": (
                round(float(dist_atr), 4) if dist_atr is not None else None
            ),
            "state": state,
            "span_atr": (round(float(span_atr), 4) if span_atr is not None else None),
        }
        z2 = replace(z)
        z2.meta = dict(z.meta or {})
        z2.meta.update(meta_patch)
        candidates.append((score, z2, meta_patch))

    # Відбір: 1–3 на сторону, найвищий score.
    long = sorted(
        (c for c in candidates if c[1].direction == "LONG"),
        key=lambda x: x[0],
        reverse=True,
    )
    short = sorted(
        (c for c in candidates if c[1].direction == "SHORT"),
        key=lambda x: x[0],
        reverse=True,
    )

    long_picked = min(3, len(long))
    short_picked = min(3, len(short))
    dropped_due_cap = max(0, len(long) - long_picked) + max(
        0, len(short) - short_picked
    )

    picked: list[SmcZone] = []
    picked_payload: list[dict[str, Any]] = []

    for group in (long[:3], short[:3]):
        for score, zone, patch in group:
            _ = score
            picked.append(zone)
            picked_payload.append(
                {
                    "type": patch.get("poi_type"),
                    "direction": zone.direction,
                    "role": zone.role,
                    "price_min": float(zone.price_min),
                    "price_max": float(zone.price_max),
                    "filled_pct": patch.get("filled_pct"),
                    "score": patch.get("score"),
                    "why": patch.get("why") or [],
                }
            )

    poi_meta = {
        "poi_candidates": len(candidates),
        "poi_active": len(picked),
        "poi_archived": int(archived),
        "poi_max_per_side": 3,
        "poi_candidates_long": int(len(long)),
        "poi_candidates_short": int(len(short)),
        "poi_active_long": int(long_picked),
        "poi_active_short": int(short_picked),
        "poi_dropped_due_cap": int(dropped_due_cap),
        "poi_archived_invalidated": int(archived_invalidated),
        "poi_archived_filled": int(archived_filled),
        "poi_archived_score_le_0": int(archived_score_le_0),
        "poi_archived_wide_span_atr": int(archived_wide_span_atr),
        "poi_skipped_non_poi_type": int(skipped_non_poi_type),
        "poi_skipped_bad_direction": int(skipped_bad_direction),
    }
    return picked, picked_payload, poi_meta


def _classify_poi_type(zone: SmcZone) -> PoiType | None:
    if zone.zone_type is SmcZoneType.ORDER_BLOCK:
        return "OB"
    if zone.zone_type is SmcZoneType.BREAKER:
        return "BREAKER"
    # FVG/Imbalance: у нас детектори зараз кладуть у IMBALANCE.
    if zone.zone_type is SmcZoneType.IMBALANCE:
        return (
            "FVG"
            if any(
                str(c).lower().startswith("fvg") or str(c).lower() == "fvg"
                for c in (zone.components or [])
            )
            else "FVG"
        )
    return None


def _extract_eq_level(structure: SmcStructureState) -> float | None:
    ar = getattr(structure, "active_range", None)
    if ar is None:
        return None
    return safe_float(getattr(ar, "eq_level", None))


def _last_close(frame: pd.DataFrame) -> float | None:
    try:
        return safe_float(frame["close"].iloc[-1])
    except Exception:
        return None


def _iter_rows_since_origin(frame: pd.DataFrame, origin: pd.Timestamp) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    if isinstance(frame.index, pd.DatetimeIndex):
        idx = frame.index
        try:
            if idx.tz is None and origin.tzinfo is not None:
                # Індекс naive, origin aware → порівнюємо в naive UTC.
                o = origin.tz_convert("UTC").tz_localize(None)
            elif idx.tz is not None and origin.tzinfo is None:
                # Індекс aware, origin naive → локалізуємо origin у tz індексу.
                o = origin.tz_localize(idx.tz)
            elif idx.tz is not None and origin.tzinfo is not None:
                o = origin.tz_convert(idx.tz)
            else:
                o = origin
        except Exception:
            o = origin
        return frame.loc[idx >= o]
    if "timestamp" in frame.columns:
        ts = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
        mask = ts >= (
            origin.tz_convert("UTC") if origin.tzinfo else origin.tz_localize("UTC")
        )
        return frame.loc[mask]
    return frame


def _compute_filled_pct(*, frame: pd.DataFrame, zone: SmcZone) -> float:
    width = max(float(zone.price_max) - float(zone.price_min), 1e-9)
    tail = _iter_rows_since_origin(frame, zone.origin_time)
    if tail is None or tail.empty:
        return 0.0

    if zone.direction == "LONG":
        try:
            low_min = safe_float(tail["low"].min())
        except Exception:
            low_min = None
        if low_min is None:
            return 0.0
        # Скільки зони зверху вниз "пройшли" (ретест вглиб зони).
        penetration = float(zone.price_max) - max(float(zone.price_min), float(low_min))
        return float(max(0.0, min(1.0, penetration / width)))

    if zone.direction == "SHORT":
        try:
            high_max = safe_float(tail["high"].max())
        except Exception:
            high_max = None
        if high_max is None:
            return 0.0
        penetration = min(float(zone.price_max), float(high_max)) - float(
            zone.price_min
        )
        return float(max(0.0, min(1.0, penetration / width)))

    return 0.0


def _is_invalidated(*, frame: pd.DataFrame, zone: SmcZone) -> bool:
    # Мінімальний інваріант: close пробив зону по "неправильному" боку.
    try:
        close_last = safe_float(frame["close"].iloc[-1])
    except Exception:
        close_last = None
    if close_last is None:
        return False

    if zone.direction == "LONG":
        return float(close_last) < float(zone.price_min)
    if zone.direction == "SHORT":
        return float(close_last) > float(zone.price_max)
    return False


def _score_zone(
    *,
    zone: SmcZone,
    poi_type: PoiType,
    ref_price: float,
    atr: float | None,
    bias: str,
    eq_level: float | None,
    liquidity: SmcLiquidityState | None,
    cfg: SmcCoreConfig,
    filled_pct: float,
) -> tuple[float, list[str]]:
    """Повертає score (0..100+) та список пояснень why[]."""

    width = max(float(zone.price_max) - float(zone.price_min), 1e-9)
    center = (float(zone.price_min) + float(zone.price_max)) / 2.0
    dist_abs = abs(center - float(ref_price))
    dist_atr = (dist_abs / atr) if atr and atr > 0 else None

    why: list[str] = []

    base = 35.0
    if poi_type == "OB":
        base = 45.0
    elif poi_type == "BREAKER":
        base = 50.0
    elif poi_type == "FVG":
        base = 40.0

    # Якість/довіра, які вже порахували детектори.
    strength = float(zone.strength or 0.0)
    confidence = float(zone.confidence or 0.0)
    score = (
        base
        + 10.0 * min(3.0, max(0.0, strength))
        + 25.0 * min(1.0, max(0.0, confidence))
    )
    why.append(
        f"base={round(base, 1)} strength={round(strength, 2)} conf={round(confidence, 2)}"
    )

    # Роль / bias.
    if str(zone.role).upper() == "PRIMARY":
        score += 10.0
        why.append("role=PRIMARY")
    if bias in {"LONG", "SHORT"} and zone.direction == bias:
        score += 10.0
        why.append("bias=match")
    elif bias in {"LONG", "SHORT"}:
        score -= 6.0
        why.append("bias=counter")

    # Premium/Discount кон-флюенс (якщо є EQ).
    if eq_level is not None and safe_float(eq_level) is not None:
        eq = float(eq_level)
        if zone.direction == "LONG" and center <= eq:
            score += 6.0
            why.append("discount")
        if zone.direction == "SHORT" and center >= eq:
            score += 6.0
            why.append("premium")

    # Displacement.
    displacement_atr = safe_float((zone.meta or {}).get("displacement_atr"))
    if displacement_atr is not None and displacement_atr > 0:
        bonus = min(20.0, displacement_atr * 8.0)
        score += bonus
        why.append(f"displacement_atr={round(displacement_atr, 2)}")
    else:
        # fallback для OB/FVG: gap/амплітуда
        gap_atr = safe_float((zone.meta or {}).get("gap_atr"))
        if gap_atr is not None and gap_atr > 0:
            bonus = min(14.0, gap_atr * 6.0)
            score += bonus
            why.append(f"gap_atr={round(gap_atr, 2)}")
        amplitude = safe_float((zone.meta or {}).get("amplitude"))
        if amplitude is not None and atr and atr > 0:
            amp_atr = amplitude / atr
            bonus = min(12.0, amp_atr * 4.0)
            score += bonus
            why.append(f"amp_atr={round(amp_atr, 2)}")

    # Ліквідність: близькість до liquidity_targets (internal/external).
    if liquidity is not None:
        targets = (liquidity.meta or {}).get("liquidity_targets")
        nearest = _nearest_liquidity_target_distance(
            targets,
            price=center,
        )
        if nearest is not None:
            dist = nearest
            # Якщо є ATR — нормалізуємо. Інакше відносно ширини зони.
            if atr and atr > 0:
                dist_norm = dist / atr
            else:
                dist_norm = dist / max(width, 1e-9)
            bonus = max(0.0, 12.0 * (1.0 - min(1.0, dist_norm / 2.0)))
            if bonus > 0:
                score += bonus
                why.append("liq_target_near")

    # Penalty за заповнення (чим більше fill — тим гірше POI).
    if filled_pct >= 0.85:
        score -= 22.0
        why.append("filled>=85%")
    elif filled_pct >= 0.6:
        score -= 12.0
        why.append("filled>=60%")

    # Penalty за "дуже далеко".
    if dist_atr is not None and dist_atr > 4.0:
        score -= 15.0
        why.append("far>4atr")

    # Простий поріг, щоб не тягнути слабкі POI.
    threshold = 55.0
    if score < threshold:
        why.append(f"below_threshold={threshold}")
        return 0.0, why

    # Додаємо компактні поля для пояснення.
    if dist_atr is not None:
        why.append(f"dist_atr={round(dist_atr, 2)}")
    else:
        why.append(f"dist_abs={round(dist_abs, 4)}")

    why.append(f"filled={round(filled_pct * 100.0, 1)}%")
    return float(score), why


def _nearest_liquidity_target_distance(targets: Any, *, price: float) -> float | None:
    if not isinstance(targets, Sequence):
        return None
    best: float | None = None
    for t in targets:
        if not isinstance(t, dict):
            continue
        tp = safe_float(t.get("price"))
        if tp is None:
            continue
        d = abs(float(tp) - float(price))
        best = d if best is None else min(best, d)
    return best
