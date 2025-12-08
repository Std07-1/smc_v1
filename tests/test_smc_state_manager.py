"""Тести для SmcStateManager."""

from app.smc_state_manager import SmcStateManager
from config.constants import ASSET_STATE


def test_smc_state_manager_updates_stats() -> None:
    manager = SmcStateManager(["xauusd"])

    manager.update_asset(
        "xauusd",
        {
            "signal": "SMC_HINT",
            "state": ASSET_STATE["NORMAL"],
            "hints": ["ok"],
            "smc_hint": {"structure": {"swings": []}},
            "stats": {"current_price": 2000.0},
        },
    )

    assets = manager.get_all_assets()
    assert len(assets) == 1
    asset = assets[0]
    assert asset["symbol"] == "xauusd"
    assert asset["signal"] == "SMC_HINT"
    assert asset["state"] == ASSET_STATE["NORMAL"]
    assert asset["stats"]["current_price"] == 2000.0
    assert asset["hints"] == ["ok"]
