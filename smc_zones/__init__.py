"""smc_zones
~~~~~~~~~

Фасад для модуля зон. На підетапі 4.2 додається перший бойовий детектор Order
Block, який будує ``SmcZonesState`` із базовими зонами. Інші детектори (Breaker,
Imbalance, POI/FTA) залишаються у стані заглушок до наступних підетапів.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import (
    SmcInput,
    SmcLiquidityState,
    SmcStructureState,
    SmcZone,
    SmcZonesState,
)
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
        return SmcZonesState(
            zones=[],
            active_zones=[],
            poi_zones=[],
            meta=_build_meta([], [], [], cfg),
        )

    orderblocks = detect_order_blocks(snapshot, structure, cfg)
    all_zones = list(orderblocks)
    active_zones = _filter_active_zones(all_zones, frame, cfg.max_lookback_bars)

    meta = _build_meta(all_zones, orderblocks, active_zones, cfg)

    return SmcZonesState(
        zones=all_zones,
        active_zones=active_zones,
        poi_zones=[],
        meta=meta,
    )


def _filter_active_zones(
    zones: Sequence[SmcZone], frame: pd.DataFrame, max_lookback_bars: int
) -> list[SmcZone]:
    """Повертає лише зони, що потрапляють у lookback-вікно по часу."""

    if not zones or frame is None or frame.empty:
        return []

    index = frame.index
    if not isinstance(index, pd.DatetimeIndex):
        return list(zones)

    lookback = min(max_lookback_bars, len(index))
    threshold_time = index[-lookback]
    return [zone for zone in zones if zone.origin_time >= threshold_time]


def _build_meta(
    all_zones: Sequence[SmcZone],
    orderblocks: Sequence[SmcZone],
    active_zones: Sequence[SmcZone],
    cfg: SmcCoreConfig,
) -> dict[str, object]:
    """Формує агреговану телеметрію для SmcZonesState.meta."""

    primary_count = sum(1 for z in orderblocks if z.role == "PRIMARY")
    countertrend_count = sum(1 for z in orderblocks if z.role == "COUNTERTREND")
    long_count = sum(1 for z in orderblocks if z.direction == "LONG")
    short_count = sum(1 for z in orderblocks if z.direction == "SHORT")

    return {
        "zone_count": len(all_zones),
        "active_zone_count": len(active_zones),
        "orderblocks_total": len(orderblocks),
        "orderblocks_primary": primary_count,
        "orderblocks_countertrend": countertrend_count,
        "orderblocks_long": long_count,
        "orderblocks_short": short_count,
        "ob_params": _extract_ob_params(cfg),
    }


def _extract_ob_params(cfg: SmcCoreConfig) -> dict[str, float | int]:
    return {
        "ob_leg_min_atr_mul": cfg.ob_leg_min_atr_mul,
        "ob_leg_max_bars": cfg.ob_leg_max_bars,
        "ob_prelude_max_bars": cfg.ob_prelude_max_bars,
        "ob_body_domination_pct": cfg.ob_body_domination_pct,
        "ob_body_min_pct": cfg.ob_body_min_pct,
        "max_lookback_bars": cfg.max_lookback_bars,
    }
