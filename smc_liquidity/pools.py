"""Логіка формування пулів ліквідності (EQH/EQL/TLQ/SLQ/Range/Session).

Алгоритм працює поверх уже побудованої структури, не змінюючи її. Свінги
кластеризуються за ціною з допуском ``cfg.eq_tolerance_pct``; додаткові пули
формуються на базі активного ренджа та контексту сесії.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

import pandas as pd

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import (
    SmcInput,
    SmcLiquidityPool,
    SmcLiquidityType,
    SmcStructureState,
    SmcSwing,
)


def build_eq_pools_from_swings(
    structure: SmcStructureState, cfg: SmcCoreConfig
) -> list[SmcLiquidityPool]:
    """Кластеризує swing high/low у EQH/EQL з урахуванням допуску по ціні."""

    swings = structure.swings or []
    if not swings:
        return []

    tolerance = max(cfg.eq_tolerance_pct, 0.001)
    highs = [s for s in swings if s.kind == "HIGH"]
    lows = [s for s in swings if s.kind == "LOW"]
    pools: list[SmcLiquidityPool] = []
    pools.extend(
        _clusters_to_pools(highs, tolerance, SmcLiquidityType.EQH, structure.bias)
    )
    pools.extend(
        _clusters_to_pools(lows, tolerance, SmcLiquidityType.EQL, structure.bias)
    )
    return pools


def add_trend_pools(
    pools: list[SmcLiquidityPool], structure: SmcStructureState
) -> None:
    """Додає трендові пули TLQ/SLQ на базі останніх swing low/high."""

    last_low = _last_swing(structure.swings, "LOW")
    last_high = _last_swing(structure.swings, "HIGH")
    ref_ts = _structure_ref_ts(structure)

    if structure.bias == "LONG" and last_low:
        pools.append(
            SmcLiquidityPool(
                level=float(last_low.price),
                liq_type=SmcLiquidityType.TLQ,
                strength=float(last_low.strength or 1),
                n_touches=1,
                first_time=last_low.time,
                last_time=last_low.time,
                role=resolve_role_for_bias("LONG", SmcLiquidityType.TLQ),
                source_swings=[last_low],
                meta={"source": "last_low", "side": "LOW", "ref_ts": ref_ts},
            )
        )
    if structure.bias == "SHORT" and last_high:
        pools.append(
            SmcLiquidityPool(
                level=float(last_high.price),
                liq_type=SmcLiquidityType.SLQ,
                strength=float(last_high.strength or 1),
                n_touches=1,
                first_time=last_high.time,
                last_time=last_high.time,
                role=resolve_role_for_bias("SHORT", SmcLiquidityType.SLQ),
                source_swings=[last_high],
                meta={"source": "last_high", "side": "HIGH", "ref_ts": ref_ts},
            )
        )


def add_range_and_session_pools(
    pools: list[SmcLiquidityPool],
    structure: SmcStructureState,
    snapshot: SmcInput,
) -> None:
    """Розширює список пулів даними з активного ренджа та session context."""

    _add_range_pools(pools, structure)
    _add_session_pools(pools, structure, snapshot)


# ──────────────────────────── Helpers ─────────────────────────────


def _clusters_to_pools(
    swings: list[SmcSwing],
    tolerance_pct: float,
    liq_type: SmcLiquidityType,
    bias: str,
) -> list[SmcLiquidityPool]:
    clusters = _cluster_swings(swings, tolerance_pct)
    pools: list[SmcLiquidityPool] = []
    for cluster in clusters:
        level = float(sum(sw.price for sw in cluster) / len(cluster))
        strength = float(sum(sw.strength for sw in cluster))
        first_time = min((sw.time for sw in cluster), default=None)
        last_time = max((sw.time for sw in cluster), default=None)
        pools.append(
            SmcLiquidityPool(
                level=level,
                liq_type=liq_type,
                strength=strength,
                n_touches=len(cluster),
                first_time=first_time,
                last_time=last_time,
                role=resolve_role_for_bias(bias, liq_type),
                source_swings=list(cluster),
                meta={"source": "eq_cluster", "cluster_size": len(cluster)},
            )
        )
    return pools


def _cluster_swings(
    swings: list[SmcSwing], tolerance_pct: float
) -> list[list[SmcSwing]]:
    clusters: list[list[SmcSwing]] = []
    for swing in swings:
        matched = False
        for cluster in clusters:
            avg_price = sum(sw.price for sw in cluster) / len(cluster)
            if _within_tolerance(swing.price, avg_price, tolerance_pct):
                cluster.append(swing)
                matched = True
                break
        if not matched:
            clusters.append([swing])
    # Потрібні мінімум два торкання для EQH/EQL
    return [cluster for cluster in clusters if len(cluster) >= 2]


def _within_tolerance(price: float, ref: float, tolerance_pct: float) -> bool:
    if ref == 0:
        return abs(price - ref) <= tolerance_pct
    diff_ratio = abs(price - ref) / max(abs(ref), 1e-6)
    return diff_ratio <= tolerance_pct


def _last_swing(swings: Iterable[SmcSwing], kind: str) -> SmcSwing | None:
    for swing in reversed(list(swings or [])):
        if swing.kind == kind:
            return swing
    return None


def _structure_ref_ts(structure: SmcStructureState) -> pd.Timestamp | None:
    ts = structure.meta.get("snapshot_end_ts") if structure.meta else None
    if ts is None:
        return None
    try:
        return pd.Timestamp(ts)
    except Exception:
        return None


def _add_range_pools(
    pools: list[SmcLiquidityPool], structure: SmcStructureState
) -> None:
    active_range = structure.active_range
    if active_range is None:
        return
    low_role = resolve_role_for_bias(
        structure.bias, SmcLiquidityType.RANGE_EXTREME, side="LOW"
    )
    high_role = resolve_role_for_bias(
        structure.bias, SmcLiquidityType.RANGE_EXTREME, side="HIGH"
    )
    pools.append(
        SmcLiquidityPool(
            level=float(active_range.low),
            liq_type=SmcLiquidityType.RANGE_EXTREME,
            strength=float(active_range.high - active_range.low),
            n_touches=1,
            first_time=active_range.start_time,
            last_time=active_range.end_time or active_range.start_time,
            role=low_role,
            meta={"source": "range", "side": "LOW"},
        )
    )
    pools.append(
        SmcLiquidityPool(
            level=float(active_range.high),
            liq_type=SmcLiquidityType.RANGE_EXTREME,
            strength=float(active_range.high - active_range.low),
            n_touches=1,
            first_time=active_range.start_time,
            last_time=active_range.end_time or active_range.start_time,
            role=high_role,
            meta={"source": "range", "side": "HIGH"},
        )
    )


def _add_session_pools(
    pools: list[SmcLiquidityPool],
    structure: SmcStructureState,
    snapshot: SmcInput,
) -> None:
    ctx = snapshot.context or {}
    ref_ts = _structure_ref_ts(structure)
    levels = [
        (ctx.get("pdl"), SmcLiquidityType.SESSION_LOW, "LOW"),
        (ctx.get("pdh"), SmcLiquidityType.SESSION_HIGH, "HIGH"),
    ]
    for value, liq_type, side in levels:
        if value is None:
            continue
        try:
            level = float(value)
        except Exception:
            continue
        pools.append(
            SmcLiquidityPool(
                level=level,
                liq_type=liq_type,
                strength=1.0,
                n_touches=1,
                first_time=ref_ts,
                last_time=ref_ts,
                role=resolve_role_for_bias(structure.bias, liq_type),
                meta={
                    "source": "session",
                    "side": side,
                    "key": "pdl" if side == "LOW" else "pdh",
                },
            )
        )


def resolve_role_for_bias(
    bias: str,
    liq_type: SmcLiquidityType,
    side: str | None = None,
) -> Literal["PRIMARY", "COUNTERTREND", "NEUTRAL"]:
    bias = bias or "NEUTRAL"
    if liq_type in (SmcLiquidityType.SFP, SmcLiquidityType.WICK_CLUSTER):
        if bias == "LONG":
            if side == "LOW":
                return "PRIMARY"
            if side == "HIGH":
                return "COUNTERTREND"
        if bias == "SHORT":
            if side == "HIGH":
                return "PRIMARY"
            if side == "LOW":
                return "COUNTERTREND"
        return "NEUTRAL"

    if bias == "LONG":
        if liq_type in (
            SmcLiquidityType.EQL,
            SmcLiquidityType.TLQ,
            SmcLiquidityType.SESSION_LOW,
        ):
            return "PRIMARY"
        if liq_type in (
            SmcLiquidityType.EQH,
            SmcLiquidityType.SLQ,
            SmcLiquidityType.SESSION_HIGH,
        ):
            return "COUNTERTREND"
        if liq_type == SmcLiquidityType.RANGE_EXTREME:
            if side == "LOW":
                return "PRIMARY"
            if side == "HIGH":
                return "COUNTERTREND"
    if bias == "SHORT":
        if liq_type in (
            SmcLiquidityType.EQH,
            SmcLiquidityType.SLQ,
            SmcLiquidityType.SESSION_HIGH,
        ):
            return "PRIMARY"
        if liq_type in (
            SmcLiquidityType.EQL,
            SmcLiquidityType.TLQ,
            SmcLiquidityType.SESSION_LOW,
        ):
            return "COUNTERTREND"
        if liq_type == SmcLiquidityType.RANGE_EXTREME:
            if side == "HIGH":
                return "PRIMARY"
            if side == "LOW":
                return "COUNTERTREND"
    return "NEUTRAL"
