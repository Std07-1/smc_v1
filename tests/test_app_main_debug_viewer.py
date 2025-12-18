"""Тести для запуску console debug viewer (best-effort)."""

from __future__ import annotations

import pytest

import app.main as main


def test_debug_viewer_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без DEBUG_VIEWER_ENABLED запуск не виконується."""

    monkeypatch.delenv("DEBUG_VIEWER_ENABLED", raising=False)

    called = False

    def _fake_launch() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(main, "launch_experimental_viewer", _fake_launch)

    main._maybe_launch_debug_viewer()
    assert called is False


def test_debug_viewer_enabled_calls_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """За DEBUG_VIEWER_ENABLED=1 має викликатись launcher."""

    monkeypatch.setenv("DEBUG_VIEWER_ENABLED", "1")

    called = False

    def _fake_launch() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(main, "launch_experimental_viewer", _fake_launch)

    main._maybe_launch_debug_viewer()
    assert called is True
