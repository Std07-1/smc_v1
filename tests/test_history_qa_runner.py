import asyncio
import json
from types import SimpleNamespace

import pandas as pd

from app.history_qa_runner import HistoryQaConfig, run_history_qa_for_symbols


class FakeStore:
    def __init__(
        self, frames: dict[tuple[str, str], pd.DataFrame], base_dir: str
    ) -> None:
        self.frames = frames
        self.cfg = SimpleNamespace(base_dir=base_dir)

    async def get_df(self, symbol: str, interval: str, limit: int | None = None):
        frame = self.frames.get((symbol, interval))
        if frame is None:
            return None
        if limit is None or limit >= len(frame):
            return frame.copy().reset_index(drop=True)
        return frame.tail(limit).copy().reset_index(drop=True)


class FakeEngine:
    def process_snapshot(self, snapshot):  # noqa: D401
        frame = snapshot.ohlc_by_tf.get(snapshot.tf_primary)
        if frame is None or frame.empty:
            return {"bars": 0}
        close = float(frame["close"].iloc[-1])
        return {"bars": len(frame), "close": close}


def _make_frame(count: int, freq: str) -> pd.DataFrame:
    ts = pd.date_range("2025-01-01", periods=count, freq=freq, tz="UTC")
    data = {
        "timestamp": ts,
        "open": [float(i) for i in range(count)],
        "high": [float(i) + 0.5 for i in range(count)],
        "low": [float(i) - 0.5 for i in range(count)],
        "close": [float(i) for i in range(count)],
        "volume": [1000.0 + i for i in range(count)],
    }
    return pd.DataFrame(data)


def test_history_qa_runner_writes_jsonl(tmp_path):
    symbol = "xauusd"
    primary = _make_frame(12, "1min")
    higher = _make_frame(12, "5min")
    store = FakeStore(
        {
            (symbol, "1m"): primary,
            (symbol, "5m"): higher,
        },
        base_dir=str(tmp_path),
    )
    cfg = HistoryQaConfig(
        tf_primary="1m",
        tfs_extra=("5m",),
        limit=10,
        step=2,
        min_bars_per_snapshot=4,
    )
    engine = FakeEngine()
    report = asyncio.run(
        run_history_qa_for_symbols(
            store,  # type: ignore
            [symbol],
            cfg,
            engine=engine,  # type: ignore
        )
    )
    path = tmp_path / "xauusd_smc_1m_history.jsonl"
    assert path.exists()
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    expected_snapshots = len(range(cfg.min_bars_per_snapshot - 1, cfg.limit, cfg.step))
    assert len(lines) == expected_snapshots
    sample = json.loads(lines[-1])
    assert sample["symbol"] == symbol
    assert sample["tf"] == "1m"
    summary = report.to_summary()
    assert summary["status"] == "success"
    assert summary["symbols_success"] == 1
