"""Перевірки серіалізації smc_hint у UI payload."""

from __future__ import annotations

from smc_core.smc_types import (
    SmcAmdPhase,
    SmcHint,
    SmcLiquidityState,
    SmcStructureState,
)
from UI.publish_full_state import _prepare_smc_hint


def test_prepare_smc_hint_serializes_dataclasses() -> None:
    asset: dict[str, object] = {"symbol": "xauusd"}
    asset["smc_hint"] = SmcHint(
        structure=SmcStructureState(bias="LONG"),
        liquidity=SmcLiquidityState(
            amd_phase=SmcAmdPhase.ACCUMULATION,
            meta={"pool_count": 2},
        ),
        meta={"last_price": 2375.5},
    )

    _prepare_smc_hint(asset)

    smc_hint_plain = asset.get("smc_hint")
    assert isinstance(smc_hint_plain, dict)
    assert asset.get("smc") is smc_hint_plain
    assert smc_hint_plain.get("structure", {}).get("bias") == "LONG"
    liq = asset.get("smc_liquidity")
    assert isinstance(liq, dict)
    assert liq.get("amd_phase") == "ACCUMULATION"
    assert smc_hint_plain.get("meta", {}).get("last_price") == 2375.5


def test_prepare_smc_hint_removes_empty_blocks() -> None:
    asset: dict[str, object] = {
        "symbol": "xauusd",
        "smc_hint": None,
        "smc_liquidity": {"legacy": True},
    }

    _prepare_smc_hint(asset)

    assert "smc_hint" not in asset
    assert "smc" not in asset
    assert "smc_liquidity" not in asset
    assert "smc_structure" not in asset
    assert "smc_zones" not in asset
