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
        meta={
            "amd_reason": "range_dev",
            "liquidity_targets": [
                {
                    "role": "internal",
                    "tf": "5m",
                    "side": "above",
                    "price": 105.0,
                    "type": "EQH",
                    "strength": 80.0,
                    "reason": ["test"],
                },
                {
                    "role": "external",
                    "tf": "4h",
                    "side": "below",
                    "price": 90.0,
                    "type": "HTF_SWING_LOW",
                    "strength": 60.0,
                    "reason": ["test"],
                },
            ],
        },
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
    assert liq_hint["smc_liq_nearest_internal"]["price"] == 105.0
    assert liq_hint["smc_liq_nearest_external"]["price"] == 90.0


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
    assert "smc_liq_nearest_internal" in liq_hint
    assert "smc_liq_nearest_external" in liq_hint
    assert liq_hint["smc_liq_nearest_internal"] is None
    assert liq_hint["smc_liq_nearest_external"] is None
    assert liq_hint["smc_liq_nearest_internal_why"] == ["no_ref_price"]
    assert liq_hint["smc_liq_nearest_external_why"] == ["no_ref_price"]


def test_bridge_returns_empty_when_liquidity_missing() -> None:
    hint = SmcHint(structure=None, liquidity=None, zones=None, signals=[], meta={})

    liq_hint = build_liquidity_hint(hint, SMC_CORE_CONFIG)

    assert liq_hint == {}


def test_bridge_includes_session_context_when_present() -> None:
    liquidity = SmcLiquidityState(
        pools=[],
        magnets=[_magnet(105.0)],
        amd_phase=SmcAmdPhase.NEUTRAL,
        meta={},
    )
    hint = SmcHint(
        structure=None,
        liquidity=liquidity,
        zones=None,
        signals=[],
        meta={
            "last_price": 100.0,
            "smc_session_tag": "LONDON",
            "smc_session_start_ms": 1,
            "smc_session_end_ms": 2,
            "smc_session_high": 101.0,
            "smc_session_low": 99.5,
            "smc_sessions": {
                "ASIA": {"high": 100.7, "low": 99.2, "start_ms": 10, "end_ms": 11},
                "LONDON": {"high": 101.0, "low": 99.5, "start_ms": 12, "end_ms": 13},
                "NY": {"high": 101.4, "low": 99.7, "start_ms": 14, "end_ms": 15},
            },
        },
    )

    out = build_liquidity_hint(hint, SMC_CORE_CONFIG)
    assert out.get("smc_session_tag") == "LONDON"
    assert out.get("smc_session_high") == 101.0
    assert out.get("smc_session_low") == 99.5
    assert isinstance(out.get("smc_sessions"), dict)
