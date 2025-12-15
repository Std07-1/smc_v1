"""Перевірки перемикачів UI_v2 web-стеку та debug viewer."""

import app.main as main_module


def test_ui_v2_disabled_does_not_launch_any_viewer(monkeypatch):
    """UI_V2_ENABLED=0 вимикає web стек і не запускає viewer автоматично."""

    monkeypatch.delenv("UI_V2_ENABLED", raising=False)
    monkeypatch.delenv("DEBUG_VIEWER_ENABLED", raising=False)

    calls = {"legacy": 0}

    def fake_launch():
        calls["legacy"] += 1

    monkeypatch.setattr(main_module, "launch_experimental_viewer", fake_launch)

    tasks = main_module._launch_ui_v2_tasks(None)

    assert tasks == []
    assert calls["legacy"] == 0


def test_debug_viewer_enabled_launches_legacy_viewer(monkeypatch):
    monkeypatch.setenv("DEBUG_VIEWER_ENABLED", "1")

    calls = {"legacy": 0}

    def fake_launch():
        calls["legacy"] += 1

    monkeypatch.setattr(main_module, "launch_experimental_viewer", fake_launch)

    main_module._maybe_launch_debug_viewer()
    assert calls["legacy"] == 1
