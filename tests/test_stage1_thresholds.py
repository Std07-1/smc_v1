import asyncio

import pandas as pd
import pytest

from stage1.asset_monitoring import AssetMonitorStage1, Stage1ThresholdProfile


def _sample_df(rows: int = 40) -> pd.DataFrame:
    """Генерує штучні бари для тестів Stage1."""
    data = []
    price = 100.0
    for idx in range(rows):
        step = idx * 0.2
        data.append(
            {
                "open": price + step,
                "high": price + step + 0.6,
                "low": price + step - 0.4,
                "close": price + step + 0.3,
                "volume": 1000 + idx * 25,
            }
        )
    return pd.DataFrame(data)


def test_stage1_monitor_returns_thresholds_block() -> None:
    monitor = AssetMonitorStage1(cache_handler=None)
    df = _sample_df()
    payload = asyncio.run(monitor.check_anomalies("xauusd", df))
    assert "thresholds" in payload
    thr = payload["thresholds"]
    assert thr["low_gate"] > 0
    assert thr["high_gate"] > thr["low_gate"]


def test_stage1_threshold_profile_to_dict() -> None:
    profile = Stage1ThresholdProfile(
        symbol="xauusd",
        low_gate=0.002,
        high_gate=0.01,
        vol_z_threshold=2.0,
        vwap_deviation=0.02,
        min_atr_percent=0.0005,
    )
    data = profile.to_dict()
    assert data["symbol"] == "xauusd"
    assert pytest.approx(data["high_gate"]) == 0.01
    assert "signal_thresholds" in data
