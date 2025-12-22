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
    low_val = ctx.get("smc_session_low")
    high_val = ctx.get("smc_session_high")
    session_tag = ctx.get("smc_session_tag") or ctx.get("session_tag")
    levels = [
        (low_val, SmcLiquidityType.SESSION_LOW, "LOW"),
        (high_val, SmcLiquidityType.SESSION_HIGH, "HIGH"),
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
                    "key": "smc_session_low" if side == "LOW" else "smc_session_high",
                    "session_tag": session_tag,
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


def throttle_pools(
    pools: list[SmcLiquidityPool], *, cfg: SmcCoreConfig
) -> list[SmcLiquidityPool]:
    """Приборкує шум у pools: кластеризація рівнів + top-K + загальний cap.

    Це QA/UI-орієнтований «фільтр подачі», не продакшн-сигнал.
    Алгоритм детермінований:
    - групуємо по (liq_type, role, side);
    - всередині групи кластеризуємо за ціною з допуском cfg.eq_tolerance_pct;
    - лишаємо top-K за strength/n_touches;
    - застосовуємо загальний cap cfg.liquidity_pools_max_total.
    """

    if not pools:
        return []

    tolerance = max(cfg.eq_tolerance_pct, 0.001)

    # 1) Кластеризація в межах групи.
    grouped: dict[tuple[str, str, str], list[SmcLiquidityPool]] = {}
    for p in pools:
        side = _pool_side(p)
        key = (str(getattr(p.liq_type, "name", str(p.liq_type))), str(p.role), side)
        grouped.setdefault(key, []).append(p)

    clustered: list[SmcLiquidityPool] = []
    for (_typ, _role, _side), items in grouped.items():
        clustered.extend(_cluster_pools_by_level(items, tolerance_pct=tolerance))

    # 2) Top-K пер типу/групи.
    capped: list[SmcLiquidityPool] = []
    grouped2: dict[tuple[str, str, str], list[SmcLiquidityPool]] = {}
    for p in clustered:
        side = _pool_side(p)
        key = (str(getattr(p.liq_type, "name", str(p.liq_type))), str(p.role), side)
        grouped2.setdefault(key, []).append(p)

    for (typ, _role, side), items in grouped2.items():
        k = _topk_for_group(typ=typ, side=side, cfg=cfg)
        items_sorted = sorted(
            items,
            key=lambda p: (float(getattr(p, "strength", 0.0) or 0.0), int(p.n_touches)),
            reverse=True,
        )
        if k > 0:
            items_sorted = items_sorted[:k]
        capped.extend(items_sorted)

    # 3) Загальний cap (зберігаємо важливі типи першими).
    max_total = int(cfg.liquidity_pools_max_total or 0)
    if max_total > 0 and len(capped) > max_total:
        priority = {
            "RANGE_EXTREME": 100,
            "SESSION_HIGH": 90,
            "SESSION_LOW": 90,
            "TLQ": 80,
            "SLQ": 80,
            "EQH": 50,
            "EQL": 50,
            "SFP": 30,
            "WICK_CLUSTER": 25,
        }

        def _score(p: SmcLiquidityPool) -> tuple[int, float, int]:
            typ = str(getattr(p.liq_type, "name", str(p.liq_type)))
            pr = int(priority.get(typ, 10))
            strength = float(getattr(p, "strength", 0.0) or 0.0)
            return (pr, strength, int(p.n_touches))

        capped = sorted(capped, key=_score, reverse=True)[:max_total]

    return capped


def _topk_for_group(*, typ: str, side: str, cfg: SmcCoreConfig) -> int:
    typ_u = str(typ).upper()
    if typ_u in ("EQH", "EQL"):
        return int(cfg.liquidity_eq_topk_per_side)
    if typ_u == "WICK_CLUSTER":
        return int(cfg.liquidity_wick_cluster_topk_per_side)
    if typ_u == "SFP":
        return int(cfg.liquidity_sfp_topk_per_side)
    return int(cfg.liquidity_other_topk_per_group)


def _pool_side(pool: SmcLiquidityPool) -> str:
    """Визначає сторону рівня для групування (HIGH/LOW/UNKNOWN)."""

    try:
        meta_side = str((pool.meta or {}).get("side") or "").upper()
    except Exception:
        meta_side = ""
    if meta_side in {"HIGH", "LOW"}:
        return meta_side

    try:
        typ = str(getattr(pool.liq_type, "name", str(pool.liq_type))).upper()
    except Exception:
        typ = ""
    if typ in {"EQH", "SESSION_HIGH"}:
        return "HIGH"
    if typ in {"EQL", "SESSION_LOW"}:
        return "LOW"
    if typ in {"TLQ", "RANGE_EXTREME", "SFP", "WICK_CLUSTER"}:
        # Для RANGE_EXTREME/SFP/WICK_CLUSTER зазвичай є meta[side]. Якщо нема — UNKNOWN.
        return "UNKNOWN"
    if typ == "SLQ":
        return "HIGH"
    return "UNKNOWN"


def _cluster_pools_by_level(
    pools: list[SmcLiquidityPool], *, tolerance_pct: float
) -> list[SmcLiquidityPool]:
    if not pools:
        return []

    items = sorted(pools, key=lambda p: float(p.level))
    clusters: list[list[SmcLiquidityPool]] = []
    for p in items:
        matched = False
        for cluster in clusters:
            ref = float(sum(c.level for c in cluster) / max(len(cluster), 1))
            if _within_tolerance(float(p.level), ref, float(tolerance_pct)):
                cluster.append(p)
                matched = True
                break
        if not matched:
            clusters.append([p])

    out: list[SmcLiquidityPool] = []
    for cluster in clusters:
        if len(cluster) == 1:
            out.append(cluster[0])
            continue

        strength_sum = float(
            sum(float(getattr(p, "strength", 0.0) or 0.0) for p in cluster)
        )
        touches_sum = int(sum(int(getattr(p, "n_touches", 0) or 0) for p in cluster))
        # Вибираємо repr з найвищою strength; рівень — середній.
        repr_pool = max(
            cluster,
            key=lambda p: (float(getattr(p, "strength", 0.0) or 0.0), int(p.n_touches)),
        )
        level_avg = float(sum(float(p.level) for p in cluster) / len(cluster))
        first_time = min(
            (p.first_time for p in cluster if p.first_time is not None), default=None
        )
        last_time = max(
            (p.last_time for p in cluster if p.last_time is not None), default=None
        )

        meta = dict(repr_pool.meta or {})
        meta["throttled"] = True
        meta["throttled_cluster_size"] = int(len(cluster))
        out.append(
            SmcLiquidityPool(
                level=level_avg,
                liq_type=repr_pool.liq_type,
                strength=strength_sum,
                n_touches=touches_sum if touches_sum > 0 else int(repr_pool.n_touches),
                first_time=first_time,
                last_time=last_time,
                role=repr_pool.role,
                source_swings=list(repr_pool.source_swings or []),
                meta=meta,
            )
        )

    return out
