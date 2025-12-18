"""Гейт PRIMARY_ONLY: Stage2/Stage3-bridge використовує лише PRIMARY ролі.

Це tests-only хвиля (F1): фіксуємо, що bridge не враховує COUNTERTREND.
"""

from __future__ import annotations

from smc_core.liquidity_bridge import build_liquidity_hint
from smc_core.config import SMC_CORE_CONFIG
from smc_core.smc_types import (
    SmcAmdPhase,
    SmcHint,
    SmcLiquidityMagnet,
    SmcLiquidityState,
    SmcLiquidityType,
)


def test_liquidity_bridge_ignores_countertrend_magnets() -> None:
    hint = SmcHint(
        liquidity=SmcLiquidityState(
            magnets=[
                SmcLiquidityMagnet(
                    price_min=99.0,
                    price_max=101.0,
                    center=100.0,
                    liq_type=SmcLiquidityType.EQH,
                    role="COUNTERTREND",
                ),
                SmcLiquidityMagnet(
                    price_min=109.0,
                    price_max=111.0,
                    center=110.0,
                    liq_type=SmcLiquidityType.EQH,
                    role="PRIMARY",
                ),
            ],
            amd_phase=SmcAmdPhase.NEUTRAL,
            meta={"amd_reason": "test"},
        ),
        meta={"last_price": 105.0},
    )

    out = build_liquidity_hint(hint, SMC_CORE_CONFIG)
    assert out["smc_liq_has_above"] is True
    assert out["smc_liq_has_below"] is False
    assert out.get("smc_liq_primary_magnets") == 1


def test_liquidity_bridge_returns_no_targets_when_only_countertrend_exists() -> None:
    hint = SmcHint(
        liquidity=SmcLiquidityState(
            magnets=[
                SmcLiquidityMagnet(
                    price_min=109.0,
                    price_max=111.0,
                    center=110.0,
                    liq_type=SmcLiquidityType.EQH,
                    role="COUNTERTREND",
                )
            ],
            amd_phase=SmcAmdPhase.NEUTRAL,
        ),
        meta={"last_price": 105.0},
    )

    out = build_liquidity_hint(hint, SMC_CORE_CONFIG)
    assert out["smc_liq_has_above"] is False
    assert out["smc_liq_has_below"] is False
    assert out.get("smc_liq_primary_magnets") is None
