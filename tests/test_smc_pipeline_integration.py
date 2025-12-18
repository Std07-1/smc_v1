"""Інтеграційні тести SMC-фіче-флагу у smc_producer."""

from __future__ import annotations

import asyncio
from typing import Any

import pandas as pd
import pytest

from app.smc_producer import process_smc_batch
from app.smc_state_manager import SmcStateManager


class DummyMonitor:
    async def check_anomalies(self, symbol: str, df: pd.DataFrame) -> dict[str, object]:
        return {
            "symbol": symbol,
            "signal": "NORMAL",
            "stats": {"ok": True},
        }


class DummyRedis:
    async def jget(self, *args, **kwargs) -> dict:
        return {}


class DummyStore:
    def __init__(self) -> None:
        self.redis = DummyRedis()

    async def get_df(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        rows = limit or 10
        index = pd.date_range("2024-01-01", periods=rows, freq="min", tz="UTC")
        return pd.DataFrame(
            {
                "timestamp": index,
                "open": [1.0] * rows,
                "high": [1.1] * rows,
                "low": [0.9] * rows,
                "close": [1.0] * rows,
                "volume": [100.0] * rows,
            }
        )

    def get_price_tick(self, symbol: str) -> None:
        return None


def test_smc_hint_added_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.smc_producer as sp

    monkeypatch.setitem(sp.SMC_RUNTIME_PARAMS, "enabled", True)

    async def fake_build_smc_hint(symbol: str, store: DummyStore) -> dict[str, Any]:
        return {"direction": "LONG", "meta": {"source": "test"}}

    monkeypatch.setattr(sp, "_build_smc_hint", fake_build_smc_hint, raising=False)

    state_manager = SmcStateManager(["xauusd"])
    store: Any = DummyStore()

    asyncio.run(
        process_smc_batch(
            ["xauusd"],
            store=store,
            state_manager=state_manager,
            timeframe="1m",
            lookback=10,
        )
    )

    xau_state = state_manager.state.get("xauusd")
    assert xau_state is not None
    assert xau_state.get("smc_hint") == {
        "direction": "LONG",
        "meta": {"source": "test"},
    }


def test_smc_hint_skipped_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.smc_producer as sp

    monkeypatch.setitem(sp.SMC_RUNTIME_PARAMS, "enabled", False)

    state_manager = SmcStateManager(["xauusd"])
    store: Any = DummyStore()

    asyncio.run(
        process_smc_batch(
            ["xauusd"],
            store=store,
            state_manager=state_manager,
            timeframe="1m",
            lookback=10,
        )
    )

    xau_state = state_manager.state.get("xauusd")
    assert xau_state is not None
    assert xau_state.get("smc_hint") is None
    assert xau_state.get("signal") == "SMC_PENDING"
