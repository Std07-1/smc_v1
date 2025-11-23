"""SMC liquidity pipeline: EQH/EQL + трендові та сесійні магніти."""

from __future__ import annotations

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import (
    SmcAmdPhase,
    SmcInput,
    SmcLiquidityState,
    SmcStructureState,
)

from .amd_state import derive_amd_phase
from .magnets import build_magnets_from_pools_and_range
from .pools import (
    add_range_and_session_pools,
    add_trend_pools,
    build_eq_pools_from_swings,
)
from .sfp_wick import detect_sfp_and_wicks


def compute_liquidity_state(
    snapshot: SmcInput,
    structure: SmcStructureState,
    cfg: SmcCoreConfig,
) -> SmcLiquidityState:
    """Будує стан ліквідності на основі свінгів, ренджу та контексту сесій."""

    frame = snapshot.ohlc_by_tf.get(snapshot.tf_primary)
    bar_count = 0 if frame is None else int(len(frame))
    if structure is None:
        return SmcLiquidityState(
            amd_phase=SmcAmdPhase.NEUTRAL,
            meta={
                "bar_count": bar_count,
                "reason": "no_structure",
                "amd_reason": "немає структури",
            },
        )

    pools = build_eq_pools_from_swings(structure, cfg)
    add_trend_pools(pools, structure)
    add_range_and_session_pools(pools, structure, snapshot)
    sfp_pools, sfp_events, wick_clusters = detect_sfp_and_wicks(
        snapshot, structure, cfg
    )
    if sfp_pools:
        pools.extend(sfp_pools)
    magnets = build_magnets_from_pools_and_range(pools, structure, snapshot, cfg)

    meta = {
        "bar_count": bar_count,
        "symbol": snapshot.symbol,
        "primary_tf": structure.primary_tf or snapshot.tf_primary,
        "pool_count": len(pools),
        "magnet_count": len(magnets),
        "bias": structure.bias,
        "sfp_events": sfp_events,
        "wick_clusters": wick_clusters,
    }

    liquidity_state = SmcLiquidityState(
        pools=pools,
        magnets=magnets,
        amd_phase=SmcAmdPhase.NEUTRAL,
        meta=meta,
    )
    phase, reason = derive_amd_phase(structure, liquidity_state, cfg)
    liquidity_state.amd_phase = phase
    liquidity_state.meta["amd_reason"] = reason
    return liquidity_state
