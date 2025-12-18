"""Гейт TF_TRUTH: tf_primary існує, але даних по ньому може не бути.

Ціль (F1): зафіксувати, що пайплайн не падає і повертає стабільний shape.
Без змін runtime-поведінки.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pandas as pd
import pytest

from app.smc_producer import process_smc_batch
from app.smc_state_manager import SmcStateManager


class _DummyRedis:
    async def jget(self, *args: Any, **kwargs: Any) -> dict:
        return {}


class _DummyStore:
    def __init__(self) -> None:
        self.redis = _DummyRedis()

    async def get_df(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        # Для Stage1-таймфрейму (параметр `timeframe` у process_smc_batch) повертаємо валідні 1m бари.
        if timeframe == "1m":
            rows = max(10, int(limit) or 10)
            base = 1_700_000_000_000  # ms
            open_time = [base + i * 60_000 for i in range(rows)]
            close_time = [t + 60_000 for t in open_time]
            return pd.DataFrame(
                {
                    "open_time": open_time,
                    "open": [1.0] * rows,
                    "high": [1.1] * rows,
                    "low": [0.9] * rows,
                    "close": [1.0] * rows,
                    "volume": [100.0] * rows,
                    "close_time": close_time,
                }
            )

        # Для tf_primary=5m — імітуємо "нема даних"
        return pd.DataFrame()

    def get_price_tick(self, symbol: str) -> None:
        return None


def test_tf_primary_missing_frame_does_not_crash_and_returns_stable_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.smc_producer as sp

    monkeypatch.setitem(sp.SMC_RUNTIME_PARAMS, "enabled", True)
    monkeypatch.setitem(sp.SMC_RUNTIME_PARAMS, "tf_primary", "5m")
    monkeypatch.setitem(sp.SMC_RUNTIME_PARAMS, "tfs_extra", ("1m",))
    monkeypatch.setitem(sp.SMC_RUNTIME_PARAMS, "limit", 50)

    state_manager = SmcStateManager(["xauusd"])
    store: Any = _DummyStore()

    asyncio.run(
        process_smc_batch(
            ["xauusd"],
            store=store,
            state_manager=state_manager,
            timeframe="1m",
            lookback=50,
        )
    )

    asset = state_manager.state.get("xauusd")
    assert isinstance(asset, dict)

    # Поточна поведінка: SMC engine повертає SmcHint навіть коли tf_primary-кадру немає.
    assert asset.get("signal") == "SMC_HINT"
    hint = asset.get("smc_hint")
    assert isinstance(hint, dict)

    meta = hint.get("meta")
    assert isinstance(meta, dict)
    assert meta.get("snapshot_tf") == "5m"
    # last_price відсутній, бо frame по tf_primary порожній
    assert "last_price" not in meta
