"""Юніт-тести для fxcm_status_listener."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from data import fxcm_status_listener as fsl


def setup_function() -> None:
    fsl._reset_fxcm_feed_state_for_tests()


def test_default_state_unknown() -> None:
    state = fsl.get_fxcm_feed_state()
    assert state.market_state == "unknown"
    assert state.process_state == "unknown"
    assert state.lag_seconds is None


def test_market_status_updates_state(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_time = 1_700_000_000.0
    monkeypatch.setattr(fsl, "time", SimpleNamespace(time=lambda: fake_time))
    payload = {"state": "open", "next_open_utc": "2025-01-01T00:00:00Z"}
    state = fsl._apply_market_status(payload)
    assert state.market_state == "open"
    assert state.next_open_utc is None
    assert state.last_status_ts == fake_time


def test_market_status_closed_sets_next_open(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_time = 1_700_000_100.0
    monkeypatch.setattr(fsl, "time", SimpleNamespace(time=lambda: fake_time))
    payload = {"state": "closed", "next_open_utc": "2025-01-02T00:00:00Z"}
    state = fsl._apply_market_status(payload)
    assert state.market_state == "closed"
    assert state.next_open_utc == "2025-01-02T00:00:00Z"


def test_heartbeat_updates_lag(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_time = 1_700_000_100.0  # секунди
    monkeypatch.setattr(fsl, "time", SimpleNamespace(time=lambda: fake_time))
    last_close_ms = int((fake_time - 30.0) * 1000)  # лаг 30 секунд
    payload = {"state": "stream", "last_bar_close_ms": last_close_ms}
    state = fsl._apply_heartbeat(payload)
    assert state.process_state == "stream"
    assert state.last_bar_close_ms == last_close_ms
    assert state.lag_seconds == pytest.approx(30.0, rel=1e-3)


def test_heartbeat_sets_next_open_and_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_time = 1_764_353_600.0
    monkeypatch.setattr(fsl, "time", SimpleNamespace(time=lambda: fake_time))
    payload = {
        "state": "idle",
        "last_bar_close_ms": int((fake_time - 10.0) * 1000),
        "next_open_utc": "2025-11-30T22:15:00+00:00",
    }
    state = fsl._apply_heartbeat(payload)
    assert state.process_state == "idle"
    assert state.next_open_utc == "2025-11-30T22:15:00+00:00"
    assert state.market_state == "closed"


def test_market_status_open_clears_next_open(monkeypatch: pytest.MonkeyPatch) -> None:
    base_time = 1_764_353_600.0
    monkeypatch.setattr(fsl, "time", SimpleNamespace(time=lambda: base_time))
    fsl._apply_heartbeat(
        {
            "state": "idle",
            "last_bar_close_ms": int((base_time - 5.0) * 1000),
            "next_open_utc": "2025-11-30T22:15:00+00:00",
        }
    )

    state = fsl._apply_market_status(
        {"state": "open", "next_open_utc": "2025-12-01T00:00:00Z"}
    )

    assert state.market_state == "open"
    assert state.next_open_utc is None
