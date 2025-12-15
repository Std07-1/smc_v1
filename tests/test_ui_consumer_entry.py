"""Тести для CLI viewer: гарантуємо extended режим за замовчуванням."""

from UI.experimental_viewer import SmcExperimentalViewer
from UI.experimental_viewer_extended import SmcExperimentalViewerExtended
from UI.ui_consumer_experimental_entry import ExperimentalViewerConsumer


def test_consumer_uses_extended_viewer_by_default(tmp_path) -> None:
    consumer = ExperimentalViewerConsumer(
        symbol="xauusd",
        snapshot_dir=str(tmp_path),
        channel="test_channel",
        snapshot_key="test_snapshot",
    )

    assert isinstance(consumer.viewer, SmcExperimentalViewerExtended)


def test_consumer_allows_injecting_custom_viewer(tmp_path) -> None:
    consumer = ExperimentalViewerConsumer(
        symbol="xauusd",
        snapshot_dir=str(tmp_path),
        channel="test_channel",
        snapshot_key="test_snapshot",
        viewer_cls=SmcExperimentalViewer,
    )

    assert isinstance(consumer.viewer, SmcExperimentalViewer)


def test_consumer_extract_tick_mid_symbol_match() -> None:
    payload = {
        "symbol": "XAUUSD",
        "bid": 1.0,
        "ask": 2.0,
        "mid": 1.5,
        "tick_ts": 1.0,
        "snap_ts": 2.0,
    }
    mid = ExperimentalViewerConsumer._extract_tick_mid(payload, symbol="xauusd")
    assert mid == 1.5


def test_consumer_extract_tick_mid_symbol_mismatch() -> None:
    payload = {"symbol": "EURUSD", "mid": 1.234}
    assert (
        ExperimentalViewerConsumer._extract_tick_mid(payload, symbol="xauusd") is None
    )
