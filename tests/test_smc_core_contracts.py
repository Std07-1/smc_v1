"""Контрактні тести для SmcCoreEngine."""

from __future__ import annotations

import pandas as pd

from smc_core.engine import SmcCoreEngine
from smc_core.smc_types import SmcHint, SmcInput


def _sample_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open_time": [1, 2, 3],
            "open": [10.0, 11.0, 12.0],
            "high": [11.0, 12.5, 13.0],
            "low": [9.5, 10.5, 11.5],
            "close": [10.5, 12.0, 12.7],
            "volume": [100, 120, 150],
            "close_time": [2, 3, 4],
        }
    )


def test_engine_returns_hint() -> None:
    engine = SmcCoreEngine()
    snapshot = SmcInput(
        symbol="xauusd",
        tf_primary="5m",
        ohlc_by_tf={"5m": _sample_frame()},
        context={},
    )

    hint = engine.process_snapshot(snapshot)

    assert isinstance(hint, SmcHint)
    assert hint.signals == []
    assert hint.structure is not None
    assert hint.structure.meta["bar_count"] == 3
    assert hint.liquidity is not None
    assert hint.zones is not None
    assert "orderblocks_total" in hint.zones.meta
    assert hint.meta.get("last_price") == 12.7
