"""Тести панелі cold-start для Experimental Viewer."""

from rich.panel import Panel

from UI.experimental_viewer_extended import SmcExperimentalViewerExtended


def test_cold_panel_hidden_when_ready_success() -> None:
    viewer = SmcExperimentalViewerExtended("xauusd")

    panel = viewer._build_cold_start_panel({"state": "ready", "status": "success"})

    assert panel is None


def test_cold_panel_warns_during_initial_load_with_pending() -> None:
    viewer = SmcExperimentalViewerExtended("xauusd")
    meta = {
        "state": "initial_load",
        "status": "degraded",
        "symbols_ready": 5,
        "symbols_total": 10,
        "symbols_pending": [
            "xauusd",
            "eurusd",
            "btcusd",
            "ethusd",
            "gbpusd",
            "audusd",
        ],
        "required_bars": 500,
        "report_ts": 1_730_000_000,
    }

    panel = viewer._build_cold_start_panel(meta)

    assert isinstance(panel, Panel)
    assert panel.border_style == "yellow"
    body = getattr(panel.renderable, "plain", str(panel.renderable))
    assert "5/10" in body
    assert "+1" in body  # обрізання pending + лічильник


def test_cold_panel_error_style_on_timeout() -> None:
    viewer = SmcExperimentalViewerExtended("xauusd")

    panel = viewer._build_cold_start_panel({"state": "error", "status": "timeout"})

    assert isinstance(panel, Panel)
    assert panel.border_style == "red"
