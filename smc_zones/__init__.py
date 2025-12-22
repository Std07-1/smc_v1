"""smc_zones
~~~~~~~~~

Фасад для модуля зон (Stage4).

Зони у SMC — це POI/FTA-об’єкти (прямокутники), а не нескінченні лінії.
Stage4 реалізує базовий набір детекторів (OrderBlock/Breaker/FVG(Imbalance)) та
POI/FTA відбір з explain-семантикою для UI.

Ключові UX-інваріанти:
- ``active_zones`` капимо (≤3 на сторону), щоб графік не перетворювався на «павутину».
- ``poi_zones`` містить малий “найкращий” список із ``score``/``why``/``filled_pct``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import pandas as pd

from core.serialization import safe_float as _safe_float
from smc_core.config import SmcCoreConfig
from smc_core.smc_types import (
    SmcInput,
    SmcLiquidityState,
    SmcStructureState,
    SmcZone,
    SmcZonesState,
    SmcZoneType,
)
from smc_zones.breaker_detector import detect_breakers
from smc_zones.fvg_detector import detect_fvg_zones
from smc_zones.orderblock_detector import detect_order_blocks
from smc_zones.poi_fta import build_active_poi_zones

logger = logging.getLogger(__name__)


_ACTIVE_ZONES_CAP_PER_SIDE = 3


def compute_zones_state(
    snapshot: SmcInput,
    structure: SmcStructureState | None,
    liquidity: SmcLiquidityState | None,
    cfg: SmcCoreConfig,
) -> SmcZonesState:
    """Формує стан зон на основі order block детектора (етап 4.2).

    Інваріанти (санітарна перевірка 4.1):
    - завжди повертається ``SmcZonesState`` навіть за відсутності даних;
    - ``zones`` містить усі знайдені зони, ``active_zones`` — лише ще валідні;
    - ``poi_zones`` містить малий список POI (Stage4) для UI.
    """

    frame = snapshot.ohlc_by_tf.get(snapshot.tf_primary)
    if structure is None or frame is None or frame.empty:
        empty_distance_meta = {
            "threshold_atr": cfg.ob_max_active_distance_atr,
            "active_within_distance": 0,
            "filtered_out_by_distance": 0,
            "max_distance_atr": None,
        }
        return SmcZonesState(
            zones=[],
            active_zones=[],
            poi_zones=[],
            meta=_build_meta(
                [],
                [],
                [],
                [],
                [],
                cfg,
                empty_distance_meta,
            ),
        )

    orderblocks = detect_order_blocks(snapshot, structure, cfg)
    breakers = detect_breakers(snapshot, structure, liquidity, orderblocks, cfg)
    fvg_zones = detect_fvg_zones(snapshot, structure, cfg)
    all_zones_raw = [*orderblocks, *breakers, *fvg_zones]
    all_zones, merge_meta = _merge_zones_by_overlap(
        zones=all_zones_raw,
        frame=frame,
        structure=structure,
        cfg=cfg,
    )

    # Після merge перераховуємо категорії, щоб meta відображала фактичні зони.
    orderblocks = [z for z in all_zones if z.zone_type is SmcZoneType.ORDER_BLOCK]
    breakers = [z for z in all_zones if z.zone_type is SmcZoneType.BREAKER]
    fvg_zones = [
        z
        for z in all_zones
        if z.zone_type in (SmcZoneType.FAIR_VALUE_GAP, SmcZoneType.IMBALANCE)
    ]
    active_zones, distance_meta = _select_active_zones(
        all_zones,
        frame,
        structure,
        cfg,
    )

    poi_zones: list[SmcZone] = []
    active_poi_payload: list[dict[str, Any]] = []
    poi_meta: dict[str, Any] = {}
    try:
        poi_zones, active_poi_payload, poi_meta = build_active_poi_zones(
            snapshot=snapshot,
            structure=structure,
            liquidity=liquidity,
            zones=all_zones,
            cfg=cfg,
        )
    except Exception as exc:
        # POI — soft-fail: не ламаємо весь пайплайн зон.
        logger.exception("POI builder впав, пропускаю POI", extra={"err": str(exc)})
        poi_zones, active_poi_payload, poi_meta = [], [], {"poi_error": str(exc)}

    _merge_poi_semantics_into_active_zones(
        active_zones=active_zones, poi_zones=poi_zones
    )

    meta = _build_meta(
        all_zones,
        orderblocks,
        breakers,
        fvg_zones,
        active_zones,
        cfg,
        distance_meta,
    )

    if merge_meta:
        meta["merge"] = merge_meta

    meta["active_poi"] = active_poi_payload
    if poi_meta:
        meta["poi"] = poi_meta

    return SmcZonesState(
        zones=all_zones,
        active_zones=active_zones,
        poi_zones=poi_zones,
        meta=meta,
    )


def _merge_poi_semantics_into_active_zones(
    *,
    active_zones: list[SmcZone],
    poi_zones: list[SmcZone],
) -> None:
    """Прокидає explain-поля POI в active_zones.

    Мотивація: UI дефолтно малює active_zones (мінімальний набір), але трейдеру
    потрібні семантика й пояснення (score/why/filled_pct/poi_type). POI builder
    вже рахує це і кладе у meta POI-зон, тому зливаємо за стабільним ключем zone_id.
    """

    if not active_zones or not poi_zones:
        return

    by_id: dict[str, SmcZone] = {}
    for z in poi_zones:
        if z.zone_id:
            by_id[str(z.zone_id)] = z

    if not by_id:
        # Нормально: продовжимо лише з fuzzy-match нижче.
        by_id = {}

    def _bounds(zone: SmcZone) -> tuple[float | None, float | None]:
        lo = _safe_float(getattr(zone, "price_min", None))
        hi = _safe_float(getattr(zone, "price_max", None))
        if lo is None or hi is None:
            return None, None
        return min(lo, hi), max(lo, hi)

    def _overlap_ratio(a: SmcZone, b: SmcZone) -> float | None:
        a_lo, a_hi = _bounds(a)
        b_lo, b_hi = _bounds(b)
        if a_lo is None or a_hi is None or b_lo is None or b_hi is None:
            return None
        inter = max(0.0, min(a_hi, b_hi) - max(a_lo, b_lo))
        union = max(a_hi, b_hi) - min(a_lo, b_lo)
        if union <= 0:
            return None
        return inter / union

    def _fuzzy_match(active: SmcZone) -> SmcZone | None:
        """Fallback: коли zone_id не збігається (наприклад, детектори генерують різні id).

        Підбираємо POI з тим самим напрямком/TF і дуже схожим діапазоном цін.
        """

        best: SmcZone | None = None
        best_score: float = -1.0
        for candidate in poi_zones:
            if (candidate.direction or "").upper() != (active.direction or "").upper():
                continue
            if str(candidate.timeframe) != str(active.timeframe):
                continue
            ratio = _overlap_ratio(active, candidate)
            if ratio is None or ratio < 0.98:
                continue

            s = _safe_float((candidate.meta or {}).get("score"))
            if s is None:
                s = 0.0
            if s > best_score:
                best = candidate
                best_score = float(s)
        return best

    for z in active_zones:
        if not z.zone_id:
            match = None
        else:
            match = by_id.get(str(z.zone_id))
        if not match:
            match = _fuzzy_match(z)
        if not match:
            continue
        # Не перезаписуємо базові поля зони; додаємо лише пояснювальні ключі.
        patch = match.meta or {}
        if not isinstance(z.meta, dict):
            z.meta = {}
        for key in (
            "poi_type",
            "score",
            "filled_pct",
            "why",
            "distance_atr",
            "state",
            "invalidated_time",
            "invalidated_ts",
        ):
            if key in patch and patch.get(key) is not None:
                z.meta[key] = patch.get(key)


def _select_active_zones(
    zones: Sequence[SmcZone],
    frame: pd.DataFrame,
    structure: SmcStructureState,
    cfg: SmcCoreConfig,
) -> tuple[list[SmcZone], dict[str, object]]:
    """Фільтрує зони по часу та (опційно) по ATR-відстані.

    Додатково застосовує жорсткий кеп для UI: максимум 1–3 зони на сторону.
    Це запобігає ситуації, коли ``active_zones`` розростається і перетворюється
    на «павутину» прямокутників у графіку.
    """

    time_filtered = _filter_by_time(zones, frame, cfg.max_lookback_bars)
    distance_meta: dict[str, object] = {
        "threshold_atr": cfg.ob_max_active_distance_atr,
        "active_within_distance": len(time_filtered),
        "filtered_out_by_distance": 0,
        "max_distance_atr": None,
    }

    threshold = cfg.ob_max_active_distance_atr
    price_ref = _last_close(frame)
    atr_last = _safe_float((structure.meta or {}).get("atr_last"))

    span_thr = cfg.max_zone_span_atr

    # 1) Якщо немає ATR/ціни — повертаємо тільки time-filter, але все одно
    # застосовуємо кеп, щоб UI не захлинався.
    if price_ref is None or atr_last is None or atr_last <= 0:
        capped, cap_meta = _cap_active_zones_per_side(
            zones=list(time_filtered),
            price_ref=None,
            atr_last=None,
            cap_per_side=_ACTIVE_ZONES_CAP_PER_SIDE,
        )
        distance_meta.update(cap_meta)
        return capped, distance_meta

    filtered: list[SmcZone] = []
    filtered_count = 0
    filtered_wide_span = 0
    max_distance: float | None = None
    max_span_atr: float | None = None
    for zone in time_filtered:
        # 1.5) Випадок D: зона надто широка => не пхаємо в active/top-K.
        # Лише маркуємо її як range/area (в meta) для подальшого UI-шару.
        if span_thr is not None and span_thr > 0 and atr_last > 0:
            pmin = _safe_float(getattr(zone, "price_min", None))
            pmax = _safe_float(getattr(zone, "price_max", None))
            if pmin is not None and pmax is not None:
                span_atr = abs(float(pmax) - float(pmin)) / float(atr_last)
                max_span_atr = (
                    float(span_atr)
                    if max_span_atr is None
                    else max(float(max_span_atr), float(span_atr))
                )
                if not isinstance(zone.meta, dict):
                    zone.meta = {}
                zone.meta.setdefault("span_atr", round(float(span_atr), 4))
                if float(span_atr) > float(span_thr):
                    zone.meta["is_range_area"] = True
                    filtered_wide_span += 1
                    continue

        distance = _zone_distance_atr(zone, price_ref, atr_last)
        if distance is not None:
            max_distance = (
                distance if max_distance is None else max(max_distance, distance)
            )

        # 2) Distance-фільтр: застосовуємо до ВСІХ зон, а не лише OB/Breaker.
        # Інакше FVG можуть накопичуватись і «забивати» active.
        if threshold is not None and distance is not None and distance > threshold:
            filtered_count += 1
            continue
        filtered.append(zone)

    distance_meta["active_within_distance"] = len(filtered)
    distance_meta["filtered_out_by_distance"] = filtered_count
    distance_meta["max_distance_atr"] = max_distance
    distance_meta["span_atr_threshold"] = span_thr
    distance_meta["filtered_out_by_span_atr"] = int(filtered_wide_span)
    distance_meta["max_span_atr"] = max_span_atr

    capped, cap_meta = _cap_active_zones_per_side(
        zones=filtered,
        price_ref=price_ref,
        atr_last=atr_last,
        cap_per_side=_ACTIVE_ZONES_CAP_PER_SIDE,
    )
    distance_meta.update(cap_meta)
    return capped, distance_meta


def _cap_active_zones_per_side(
    *,
    zones: list[SmcZone],
    price_ref: float | None,
    atr_last: float | None,
    cap_per_side: int,
) -> tuple[list[SmcZone], dict[str, object]]:
    """Кепає ``active_zones`` до малого набору для UI.

    Правило: беремо максимум ``cap_per_side`` зон на сторону (LONG/SHORT).
    Вибір пріоритезує ближчі до ціни/ATR, сильніші та більш «primary» ролі.
    """

    meta: dict[str, object] = {
        "cap_per_side": cap_per_side,
        "cap_total": None,
        "cap_dropped": 0,
        "cap_kept_long": 0,
        "cap_kept_short": 0,
        "cap_overlap_dropped": 0,
    }

    if cap_per_side <= 0 or not zones:
        meta["cap_total"] = len(zones)
        return zones, meta

    cap_total = cap_per_side * 2
    meta["cap_total"] = cap_total

    def zone_score(zone: SmcZone) -> float:
        role = (zone.role or "").upper()
        role_mul = 1.15 if role == "PRIMARY" else 0.85

        zt = zone.zone_type
        type_mul = (
            1.10
            if zt is SmcZoneType.ORDER_BLOCK
            else (1.05 if zt is SmcZoneType.BREAKER else 1.0)
        )

        strength = _safe_float(getattr(zone, "strength", None))
        strength_bonus = 0.0 if strength is None else max(0.0, strength)

        dist = None
        if price_ref is not None and atr_last is not None and atr_last > 0:
            dist = _zone_distance_atr(zone, price_ref, atr_last)
        dist_penalty = 4.0 if dist is None else max(0.0, dist)

        # Чим ближче та сильніше — тим краще.
        return (role_mul * type_mul * (1.0 + 0.25 * strength_bonus)) / (
            1.0 + dist_penalty
        )

    def zone_bounds(zone: SmcZone) -> tuple[float | None, float | None]:
        lo = _safe_float(getattr(zone, "price_min", None))
        hi = _safe_float(getattr(zone, "price_max", None))
        if lo is None or hi is None:
            return None, None
        return min(lo, hi), max(lo, hi)

    def overlap_ratio(a: SmcZone, b: SmcZone) -> float | None:
        a_lo, a_hi = zone_bounds(a)
        b_lo, b_hi = zone_bounds(b)
        if a_lo is None or a_hi is None or b_lo is None or b_hi is None:
            return None
        inter = max(0.0, min(a_hi, b_hi) - max(a_lo, b_lo))
        union = max(a_hi, b_hi) - min(a_lo, b_lo)
        if union <= 0:
            return None
        return inter / union

    longs = [z for z in zones if (z.direction or "").upper() == "LONG"]
    shorts = [z for z in zones if (z.direction or "").upper() == "SHORT"]
    others = [z for z in zones if (z.direction or "").upper() not in {"LONG", "SHORT"}]

    def pick_side(side_zones: list[SmcZone]) -> list[SmcZone]:
        chosen: list[SmcZone] = []
        for z in sorted(side_zones, key=zone_score, reverse=True):
            if len(chosen) >= cap_per_side:
                break
            is_dup = False
            for kept_zone in chosen:
                ratio = overlap_ratio(z, kept_zone)
                if ratio is not None and ratio >= 0.65:
                    is_dup = True
                    break
            if is_dup:
                meta["cap_overlap_dropped"] = (
                    int(meta.get("cap_overlap_dropped") or 0) + 1
                )
                continue
            chosen.append(z)
        return chosen

    longs_sorted = pick_side(longs)
    shorts_sorted = pick_side(shorts)

    kept = [*longs_sorted, *shorts_sorted]

    # Якщо direction не заповнений — не додаємо у дефолтний UI, щоб не шуміло.
    # Але зберігаємо інваріант "не більше cap_total".
    if len(kept) < cap_total and others:
        missing = cap_total - len(kept)
        kept.extend(sorted(others, key=zone_score, reverse=True)[:missing])

    meta["cap_kept_long"] = sum(
        1 for z in kept if (z.direction or "").upper() == "LONG"
    )
    meta["cap_kept_short"] = sum(
        1 for z in kept if (z.direction or "").upper() == "SHORT"
    )
    meta["cap_dropped"] = max(0, len(zones) - len(kept))
    return kept, meta


def _filter_by_time(
    zones: Sequence[SmcZone], frame: pd.DataFrame, max_lookback_bars: int
) -> list[SmcZone]:
    if not zones or frame is None or frame.empty:
        return []

    index = frame.index
    if not isinstance(index, pd.DatetimeIndex):
        return list(zones)

    lookback = min(max_lookback_bars, len(index))
    threshold_time = index[-lookback]
    return [zone for zone in zones if zone.origin_time >= threshold_time]


def _zone_distance_atr(
    zone: SmcZone, price_ref: float, atr_last: float
) -> float | None:
    if atr_last <= 0:
        return None
    center = _zone_center(zone)
    if center is None:
        return None
    return abs(center - price_ref) / atr_last


def _zone_center(zone: SmcZone) -> float | None:
    price_min = _safe_float(zone.price_min)
    price_max = _safe_float(zone.price_max)
    if price_min is None and price_max is None:
        return None
    if price_min is None:
        return price_max
    if price_max is None:
        return price_min
    return (price_min + price_max) / 2.0


def _last_close(frame: pd.DataFrame | None) -> float | None:
    if frame is None or frame.empty:
        return None
    try:
        return _safe_float(frame["close"].iloc[-1])
    except Exception:
        return None


def _build_meta(
    all_zones: Sequence[SmcZone],
    orderblocks: Sequence[SmcZone],
    breakers: Sequence[SmcZone],
    fvgs: Sequence[SmcZone],
    active_zones: Sequence[SmcZone],
    cfg: SmcCoreConfig,
    distance_meta: dict[str, object],
) -> dict[str, object]:
    """Формує агреговану телеметрію для SmcZonesState.meta."""

    primary_count = sum(1 for z in orderblocks if z.role == "PRIMARY")
    countertrend_count = sum(1 for z in orderblocks if z.role == "COUNTERTREND")
    long_count = sum(1 for z in orderblocks if z.direction == "LONG")
    short_count = sum(1 for z in orderblocks if z.direction == "SHORT")
    breaker_total = len(breakers)
    breaker_primary = sum(1 for z in breakers if z.role == "PRIMARY")
    breaker_long = sum(1 for z in breakers if z.direction == "LONG")
    breaker_short = sum(1 for z in breakers if z.direction == "SHORT")
    fvg_total = len(fvgs)
    fvg_long = sum(1 for z in fvgs if z.direction == "LONG")
    fvg_short = sum(1 for z in fvgs if z.direction == "SHORT")

    meta = {
        "zone_count": len(all_zones),
        "active_zone_count": len(active_zones),
        "orderblocks_total": len(orderblocks),
        "orderblocks_primary": primary_count,
        "orderblocks_countertrend": countertrend_count,
        "orderblocks_long": long_count,
        "orderblocks_short": short_count,
        "ob_params": _extract_ob_params(cfg),
        "breaker_total": breaker_total,
        "breaker_primary": breaker_primary,
        "breaker_long": breaker_long,
        "breaker_short": breaker_short,
        "breaker_params": _extract_breaker_params(cfg),
        "fvg_total": fvg_total,
        "fvg_long": fvg_long,
        "fvg_short": fvg_short,
        "fvg_params": _extract_fvg_params(cfg),
    }

    meta.update(
        {
            "active_zone_distance_threshold_atr": distance_meta.get("threshold_atr"),
            "active_zones_within_threshold": distance_meta.get(
                "active_within_distance"
            ),
            "zones_filtered_by_distance": distance_meta.get("filtered_out_by_distance"),
            "max_zone_distance_atr": distance_meta.get("max_distance_atr"),
            "max_zone_span_atr": cfg.max_zone_span_atr,
            "zones_filtered_by_span_atr": distance_meta.get("filtered_out_by_span_atr"),
            "max_span_atr": distance_meta.get("max_span_atr"),
            "touch_epsilon": float(cfg.touch_epsilon or 0.0),
        }
    )
    return meta


def _merge_zones_by_overlap(
    *,
    zones: Sequence[SmcZone],
    frame: pd.DataFrame,
    structure: SmcStructureState,
    cfg: SmcCoreConfig,
) -> tuple[list[SmcZone], dict[str, object]]:
    """Нормалізує дублікати зон через overlap (Випадок E).

    Логіка: якщо IoU >= threshold і (zone_type, role, direction, timeframe) однакові,
    лишаємо одну «кращу» (strength/свіжість/ближче до ціни). Для аудиту кладемо
    meta.merged_from у переможця (список zone_id, які поглинуті).

    Важливо: це не геометричний union/expand. Ми не розширюємо межі зони — лише
    прибираємо конкуренцію «дві зони як одна».
    """

    thr = cfg.zone_merge_iou_threshold
    if thr is None:
        return list(zones), {"enabled": False}
    try:
        thr_f = float(thr)
    except Exception:
        return list(zones), {"enabled": False}
    if thr_f <= 0:
        return list(zones), {"enabled": False}

    price_ref = _last_close(frame)
    atr_last = _safe_float((structure.meta or {}).get("atr_last"))
    now_ts = None
    try:
        if isinstance(frame.index, pd.DatetimeIndex) and len(frame.index):
            now_ts = frame.index[-1]
    except Exception:
        now_ts = None

    def _bounds(z: SmcZone) -> tuple[float | None, float | None]:
        lo = _safe_float(getattr(z, "price_min", None))
        hi = _safe_float(getattr(z, "price_max", None))
        if lo is None or hi is None:
            return None, None
        return min(lo, hi), max(lo, hi)

    def _iou(a: SmcZone, b: SmcZone) -> float | None:
        a_lo, a_hi = _bounds(a)
        b_lo, b_hi = _bounds(b)
        if a_lo is None or a_hi is None or b_lo is None or b_hi is None:
            return None
        inter = max(0.0, min(a_hi, b_hi) - max(a_lo, b_lo))
        union = max(a_hi, b_hi) - min(a_lo, b_lo)
        if union <= 0:
            return None
        return inter / union

    def _rank(z: SmcZone) -> float:
        # 1) База: strength
        strength = _safe_float(getattr(z, "strength", None))
        s = 0.0 if strength is None else max(0.0, float(strength))

        # 2) Свіжість: ближче origin_time до now_ts
        freshness = 0.0
        try:
            if now_ts is not None and getattr(z, "origin_time", None) is not None:
                dt_min = abs((now_ts - z.origin_time).total_seconds()) / 60.0
                # 0..1: до 6 годин — майже максимум
                freshness = 1.0 / (1.0 + (dt_min / 360.0))
        except Exception:
            freshness = 0.0

        # 3) Близькість до ціни (якщо маємо ATR)
        proximity = 0.0
        try:
            if price_ref is not None and atr_last is not None and atr_last > 0:
                d = _zone_distance_atr(z, price_ref, float(atr_last))
                if d is not None:
                    proximity = 1.0 / (1.0 + max(0.0, float(d)))
        except Exception:
            proximity = 0.0

        # 4) Роль: PRIMARY трохи вище
        role_mul = 1.05 if (z.role or "").upper() == "PRIMARY" else 1.0

        return float(role_mul) * (1.0 + 0.35 * s + 0.35 * freshness + 0.30 * proximity)

    # Групуємо зони за (type,role,dir,tf), щоб не змішувати різні сутності.
    groups: dict[tuple[str, str, str, str], list[SmcZone]] = {}
    for z in zones:
        if z is None:
            continue
        k = (str(z.zone_type), str(z.role), str(z.direction), str(z.timeframe))
        groups.setdefault(k, []).append(z)

    merged_out: list[SmcZone] = []
    merged_losers = 0
    merged_groups = 0

    for k, items in groups.items():
        if len(items) <= 1:
            merged_out.extend(items)
            continue
        kept: list[SmcZone] = []
        for cand in sorted(items, key=_rank, reverse=True):
            placed = False
            for i, cur in enumerate(list(kept)):
                ratio = _iou(cand, cur)
                if ratio is None or float(ratio) < float(thr_f):
                    continue

                # Дублі: лишаємо одного «кращого».
                cand_r = _rank(cand)
                cur_r = _rank(cur)
                winner = cand if cand_r >= cur_r else cur
                loser = cur if winner is cand else cand

                if not isinstance(winner.meta, dict):
                    winner.meta = {}
                mf = winner.meta.get("merged_from")
                if not isinstance(mf, list):
                    mf = []
                if loser.zone_id and str(loser.zone_id) not in mf:
                    mf.append(str(loser.zone_id))
                winner.meta["merged_from"] = mf

                # Якщо winner змінився — замінюємо в kept.
                kept[i] = winner
                merged_losers += 1
                merged_groups += 1
                placed = True
                break
            if not placed:
                kept.append(cand)
        merged_out.extend(kept)

    meta: dict[str, object] = {
        "enabled": True,
        "iou_threshold": float(thr_f),
        "input_zones": len(list(zones)),
        "output_zones": len(merged_out),
        "merged_losers": int(merged_losers),
        "merged_groups": int(merged_groups),
    }
    return merged_out, meta


def _extract_ob_params(cfg: SmcCoreConfig) -> dict[str, float | int | None]:
    return {
        "ob_leg_min_atr_mul": cfg.ob_leg_min_atr_mul,
        "ob_leg_max_bars": cfg.ob_leg_max_bars,
        "ob_prelude_max_bars": cfg.ob_prelude_max_bars,
        "ob_body_domination_pct": cfg.ob_body_domination_pct,
        "ob_body_min_pct": cfg.ob_body_min_pct,
        "max_lookback_bars": cfg.max_lookback_bars,
        "ob_max_active_distance_atr": cfg.ob_max_active_distance_atr,
    }


def _extract_breaker_params(cfg: SmcCoreConfig) -> dict[str, float | int]:
    return {
        "breaker_max_ob_age_minutes": cfg.breaker_max_ob_age_minutes,
        "breaker_max_sweep_delay_minutes": cfg.breaker_max_sweep_delay_minutes,
        "breaker_level_tolerance_pct": cfg.breaker_level_tolerance_pct,
        "breaker_min_body_pct": cfg.breaker_min_body_pct,
        "breaker_min_displacement_atr": cfg.breaker_min_displacement_atr,
    }


def _extract_fvg_params(cfg: SmcCoreConfig) -> dict[str, float | int]:
    return {
        "fvg_min_gap_atr": cfg.fvg_min_gap_atr,
        "fvg_min_gap_pct": cfg.fvg_min_gap_pct,
        "fvg_max_age_minutes": cfg.fvg_max_age_minutes,
    }
