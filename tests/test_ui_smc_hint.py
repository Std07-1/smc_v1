"""Перевірки серіалізації smc_hint у UI payload."""

from __future__ import annotations

import pytest

from smc_core.smc_types import (
    SmcAmdPhase,
    SmcHint,
    SmcLiquidityState,
    SmcStructureState,
)
from UI.publish_smc_state import _format_tick_age, _prepare_smc_hint


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


def test_prepare_smc_hint_normalizes_price_scale() -> None:
    asset: dict[str, object] = {
        "symbol": "xauusd",
        "stats": {"current_price": 4175.0},
        "smc_hint": {
            "structure": {
                "swings": [{"price": 41.73}],
                "ranges": [{"high": 41.9, "low": 41.2, "eq_level": 41.5}],
                "events": [{"price_level": 41.561}],
                "ote_zones": [{"ote_min": 41.6, "ote_max": 41.8}],
                "legs": [
                    {
                        "from_swing": {"price": 41.6},
                        "to_swing": {"price": 41.8},
                    }
                ],
            },
            "liquidity": {
                "pools": [{"level": 41.7}],
                "magnets": [{"price_min": 41.4, "price_max": 41.9, "center": 41.65}],
            },
            "zones": {
                "zones": [
                    {
                        "price_min": 41.2,
                        "price_max": 41.3,
                        "entry_hint": 41.25,
                        "stop_hint": 41.1,
                    }
                ]
            },
        },
    }

    _prepare_smc_hint(asset)

    structure = asset.get("smc_structure")
    assert isinstance(structure, dict)
    first_swing = structure["swings"][0]["price"]
    assert first_swing == pytest.approx(4173.0, rel=1e-3)
    event_price = structure["events"][0]["price_level"]
    assert event_price == pytest.approx(4156.1, rel=1e-4)
    ote_zone = structure["ote_zones"][0]
    assert ote_zone["ote_min"] == pytest.approx(4160.0, rel=1e-3)
    leg_price = structure["legs"][0]["from_swing"]["price"]
    assert leg_price == pytest.approx(4160.0, rel=1e-3)

    liquidity = asset.get("smc_liquidity")
    assert isinstance(liquidity, dict)
    assert liquidity["pools"][0]["level"] == pytest.approx(4170.0, rel=1e-3)
    magnet = liquidity["magnets"][0]
    assert magnet["price_min"] == pytest.approx(4140.0, rel=1e-3)

    zones = asset.get("smc_zones")
    assert isinstance(zones, dict)
    zone = zones["zones"][0]
    assert zone["price_min"] == pytest.approx(4120.0, rel=1e-3)
    assert zone["entry_hint"] == pytest.approx(4125.0, rel=1e-3)


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.2, "200 мс"),
        (2, "2.0 с"),
        (120, "2.0 хв"),
        (-5, "-"),
        (None, "-"),
    ],
)
def test_format_tick_age(value: object, expected: str) -> None:
    assert _format_tick_age(value) == expected
