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
