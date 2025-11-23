"""Формування магнітів ліквідності на базі кластерів пулів."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import (
    SmcInput,
    SmcLiquidityMagnet,
    SmcLiquidityPool,
    SmcLiquidityType,
    SmcStructureState,
)


def build_magnets_from_pools_and_range(
    pools: list[SmcLiquidityPool],
    structure: SmcStructureState,
    snapshot: SmcInput,
    cfg: SmcCoreConfig,
) -> list[SmcLiquidityMagnet]:
    """Групує пулі за рівнем ціни та повертає узгоджені магніти."""

    if not pools:
        return []

    tolerance = max(cfg.eq_tolerance_pct, 0.001)
    clusters = _cluster_pools(pools, tolerance)
    magnets: list[SmcLiquidityMagnet] = []
    for cluster in clusters:
        levels = [pool.level for pool in cluster]
        price_min = float(min(levels))
        price_max = float(max(levels))
        center = float(sum(levels) / len(levels))
        magnets.append(
            SmcLiquidityMagnet(
                price_min=price_min,
                price_max=price_max,
                center=center,
                liq_type=_infer_magnet_type(cluster),
                role=_derive_magnet_role(cluster),
                pools=list(cluster),
                meta={
                    "pool_count": len(cluster),
                    "source_types": [pool.liq_type.name for pool in cluster],
                    "symbol": snapshot.symbol,
                    "bias": structure.bias,
                },
            )
        )
    return magnets


# ──────────────────────────── Helpers ─────────────────────────────


def _cluster_pools(
    pools: Iterable[SmcLiquidityPool], tolerance_pct: float
) -> list[list[SmcLiquidityPool]]:
    clusters: list[list[SmcLiquidityPool]] = []
    for pool in sorted(pools, key=lambda p: p.level):
        matched = False
        for cluster in clusters:
            center = sum(entry.level for entry in cluster) / len(cluster)
            if _within_tolerance(pool.level, center, tolerance_pct):
                cluster.append(pool)
                matched = True
                break
        if not matched:
            clusters.append([pool])
    return clusters


def _within_tolerance(price: float, ref: float, tolerance_pct: float) -> bool:
    if ref == 0:
        return abs(price - ref) <= tolerance_pct
    diff_ratio = abs(price - ref) / max(abs(ref), 1e-6)
    return diff_ratio <= tolerance_pct


def _infer_magnet_type(cluster: list[SmcLiquidityPool]) -> SmcLiquidityType:
    priority = [
        SmcLiquidityType.RANGE_EXTREME,
        SmcLiquidityType.SESSION_HIGH,
        SmcLiquidityType.SESSION_LOW,
        SmcLiquidityType.TLQ,
        SmcLiquidityType.SLQ,
        SmcLiquidityType.EQH,
        SmcLiquidityType.EQL,
    ]
    for liq_type in priority:
        if any(pool.liq_type is liq_type for pool in cluster):
            return liq_type
    return cluster[0].liq_type if cluster else SmcLiquidityType.OTHER


def _derive_magnet_role(
    cluster: list[SmcLiquidityPool],
) -> Literal["PRIMARY", "COUNTERTREND", "NEUTRAL"]:
    if any(pool.role == "PRIMARY" for pool in cluster):
        return "PRIMARY"
    if all(pool.role == "COUNTERTREND" for pool in cluster):
        return "COUNTERTREND"
    return "NEUTRAL"
