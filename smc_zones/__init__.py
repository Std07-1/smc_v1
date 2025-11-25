"""smc_zones
~~~~~~~~~

Фасад для модуля зон. На підетапі 4.2 додається перший бойовий детектор Order
Block, який будує ``SmcZonesState`` із базовими зонами. Інші детектори (Breaker,
Imbalance, POI/FTA) залишаються у стані заглушок до наступних підетапів.
"""

from __future__ import annotations

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import (
    SmcInput,
    SmcLiquidityState,
    SmcStructureState,
    SmcZonesState,
)
from smc_zones.orderblock_detector import detect_order_blocks


def compute_zones_state(
    snapshot: SmcInput,
    structure: SmcStructureState | None,
    liquidity: SmcLiquidityState | None,
    cfg: SmcCoreConfig,
) -> SmcZonesState:
    """Формує стан зон на основі order block детектора (етап 4.2)."""

    _ = liquidity  # поки що не використовується у 4.2
    frame = snapshot.ohlc_by_tf.get(snapshot.tf_primary)
    if structure is None or frame is None or frame.empty:
        return SmcZonesState(meta={"orderblocks_total": 0, "zone_count": 0})

    orderblocks = detect_order_blocks(snapshot, structure, cfg)
    all_zones = list(orderblocks)
    primary_zones = [zone for zone in all_zones if zone.role == "PRIMARY"]

    meta = {
        "zone_count": len(all_zones),
        "orderblocks_total": len(orderblocks),
        "orderblocks_primary": len(primary_zones),
        "orderblocks_countertrend": sum(
            1 for z in orderblocks if z.role == "COUNTERTREND"
        ),
        "orderblocks_long": sum(1 for z in orderblocks if z.direction == "LONG"),
        "orderblocks_short": sum(1 for z in orderblocks if z.direction == "SHORT"),
    }

    return SmcZonesState(
        zones=all_zones,
        active_zones=primary_zones,
        poi_zones=[],
        meta=meta,
    )
