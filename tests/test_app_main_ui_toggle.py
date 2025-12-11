"""Перевірки вибору між legacy viewer та UI_v2 стеком."""

import app.main as main_module


def test_ui_v2_disabled_runs_legacy_viewer(monkeypatch):
    """Без прапорця UI_V2_ENABLED запускаємо старий viewer і не створюємо тасків."""

    monkeypatch.delenv("UI_V2_ENABLED", raising=False)

    calls = {"legacy": 0}

    def fake_launch():
        calls["legacy"] += 1

    monkeypatch.setattr(main_module, "launch_experimental_viewer", fake_launch)

    tasks = main_module._launch_ui_v2_tasks()

    assert tasks == []
    assert calls["legacy"] == 1
