"""smc_zones
~~~~~~~~~

Фасад для модуля зон. На підетапі 4.2 додається перший бойовий детектор Order
Block, який будує ``SmcZonesState`` із базовими зонами. Інші детектори (Breaker,
Imbalance, POI/FTA) залишаються у стані заглушок до наступних підетапів.
"""

from __future__ import annotations

from collections.abc import Sequence

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


def compute_zones_state(
    snapshot: SmcInput,
    structure: SmcStructureState | None,
    liquidity: SmcLiquidityState | None,
    cfg: SmcCoreConfig,
) -> SmcZonesState:
    """Формує стан зон на основі order block детектора (етап 4.2).

    Інваріанти (санітарна перевірка 4.1):
    - завжди повертається ``SmcZonesState`` навіть за відсутності даних;
    - ``zones`` містить усі знайдені зони, ``active_zones`` — лише ще валідні,
      ``poi_zones`` резервується виключно під POI/FTA та поки що порожній.
    """

    _ = liquidity  # поки що не використовується у 4.2
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
    fvg_zones = detect_fvg_zones(structure, cfg)
    all_zones = [*orderblocks, *breakers, *fvg_zones]
    active_zones, distance_meta = _select_active_zones(
        all_zones,
        frame,
        structure,
        cfg,
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

    return SmcZonesState(
        zones=all_zones,
        active_zones=active_zones,
        poi_zones=[],
        meta=meta,
    )


def _select_active_zones(
    zones: Sequence[SmcZone],
    frame: pd.DataFrame,
    structure: SmcStructureState,
    cfg: SmcCoreConfig,
) -> tuple[list[SmcZone], dict[str, object]]:
    """Фільтрує зони по часу та (опційно) по ATR-відстані."""

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
    if threshold is None or price_ref is None or atr_last is None or atr_last <= 0:
        return list(time_filtered), distance_meta

    filtered: list[SmcZone] = []
    filtered_count = 0
    max_distance: float | None = None
    for zone in time_filtered:
        distance = _zone_distance_atr(zone, price_ref, atr_last)
        if distance is not None:
            max_distance = (
                distance if max_distance is None else max(max_distance, distance)
            )
        if (
            distance is not None
            and zone.zone_type in {SmcZoneType.ORDER_BLOCK, SmcZoneType.BREAKER}
            and distance > threshold
        ):
            filtered_count += 1
            continue
        filtered.append(zone)

    distance_meta["active_within_distance"] = len(filtered)
    distance_meta["filtered_out_by_distance"] = filtered_count
    distance_meta["max_distance_atr"] = max_distance
    return filtered, distance_meta


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
        }
    )
    return meta


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
