"""Тести для логіки оновлення fast_symbols у SMC producer.

Ціль: не затирати `smc_hint`, якщо символ тимчасово зник зі списку fast_symbols.
"""

from app.smc_producer import _apply_fast_symbols_update
from app.smc_state_manager import SmcStateManager


def test_fast_symbols_update_keeps_state_for_removed_symbol() -> None:
    manager = SmcStateManager(["xauusd", "eurusd"])

    # Попередній валідний стан (має зберігатися).
    manager.update_asset(
        "xauusd",
        {
            "signal": "SMC_HINT",
            "smc_hint": {"structure": {"trend": "UP"}},
            "hints": ["SMC: дані оновлено"],
        },
    )

    assets_current = ["xauusd", "eurusd"]

    # Нова fast-таблиця тимчасово не містить xauusd.
    new_assets = _apply_fast_symbols_update(
        state_manager=manager,
        assets_current=assets_current,
        fresh_symbols=["eurusd"],
    )

    assert set(new_assets) == {"eurusd"}
    assert "xauusd" in manager.state

    x = manager.state["xauusd"]
    assert x.get("smc_hint") == {"structure": {"trend": "UP"}}
    assert x.get("signal") == "SMC_PAUSED"

    stats = x.get("stats") or {}
    assert stats.get("smc_fast_list_member") is False
