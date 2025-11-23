"""Тести легкого Stage2-моста для SMC liquidity."""

from __future__ import annotations

from smc_core.config import SMC_CORE_CONFIG
from smc_core.liquidity_bridge import build_liquidity_hint
from smc_core.smc_types import (
    SmcAmdPhase,
    SmcHint,
    SmcLiquidityMagnet,
    SmcLiquidityState,
    SmcLiquidityType,
)


def _magnet(center: float) -> SmcLiquidityMagnet:
    return SmcLiquidityMagnet(
        price_min=center - 0.5,
        price_max=center + 0.5,
        center=center,
        liq_type=SmcLiquidityType.EQH,
        role="PRIMARY",
        pools=[],
        meta={},
    )


def test_bridge_sets_flags_and_distance() -> None:
    liquidity = SmcLiquidityState(
        pools=[],
        magnets=[_magnet(95.0), _magnet(105.0)],
        amd_phase=SmcAmdPhase.MANIPULATION,
        meta={"amd_reason": "range_dev"},
    )
    hint = SmcHint(
        structure=None,
        liquidity=liquidity,
        zones=None,
        signals=[],
        meta={"last_price": 100.0},
    )

    liq_hint = build_liquidity_hint(hint, SMC_CORE_CONFIG)

    assert liq_hint["smc_liq_has_above"] is True
    assert liq_hint["smc_liq_has_below"] is True
    assert liq_hint["smc_liq_amd_phase"] == "MANIPULATION"
    assert liq_hint["smc_liq_dist_to_primary"] == 0.05
    assert liq_hint["smc_liq_meta"] == {"amd_reason": "range_dev"}
    assert liq_hint["smc_liq_ref_price"] == 100.0


def test_bridge_handles_missing_price() -> None:
    liquidity = SmcLiquidityState(
        pools=[],
        magnets=[_magnet(110.0)],
        amd_phase=SmcAmdPhase.ACCUMULATION,
        meta={},
    )
    hint = SmcHint(structure=None, liquidity=liquidity, zones=None, signals=[], meta={})

    liq_hint = build_liquidity_hint(hint, SMC_CORE_CONFIG)

    assert liq_hint["smc_liq_has_above"] is False
    assert liq_hint["smc_liq_has_below"] is False
    assert liq_hint["smc_liq_dist_to_primary"] is None
    assert "smc_liq_ref_price" not in liq_hint


def test_bridge_returns_empty_when_liquidity_missing() -> None:
    hint = SmcHint(structure=None, liquidity=None, zones=None, signals=[], meta={})

    liq_hint = build_liquidity_hint(hint, SMC_CORE_CONFIG)

    assert liq_hint == {}
