"""Юніт-тести для fxcm_status_listener."""

from __future__ import annotations

import time

import pytest

from data import fxcm_status_listener as fsl
from data.fxcm_models import (
    FxcmAggregatedStatus,
    FxcmHeartbeat,
    FxcmHeartbeatContext,
    FxcmMarketStatus,
    FxcmSessionContext,
)


def setup_function() -> None:
    fsl._reset_fxcm_feed_state_for_tests()


def test_default_state_unknown() -> None:
    state = fsl.get_fxcm_feed_state()
    assert state.market_state == "unknown"
    assert state.process_state == "unknown"
    assert state.lag_seconds is None


def test_apply_heartbeat_updates_context_fields():
    ctx = FxcmHeartbeatContext(
        lag_seconds=3.2,
        market_pause=True,
        market_pause_reason="calendar",
        seconds_to_open=5400.0,
        next_open_ms=1764022500000,
        next_open_utc="2025-11-30T22:15:00Z",
        bars_published=8,
        stream_targets=[{"symbol": "xauusd", "tf": "m1"}],
        session=FxcmSessionContext(tag="NY_METALS", timezone="America/New_York"),
    )
    hb = FxcmHeartbeat(
        type="heartbeat",
        state="warmup_cache",
        last_bar_close_ms=1764002159000,
        ts="2025-11-30T22:28:52+00:00",
        context=ctx,
    )
    snapshot = fsl._apply_heartbeat(hb)
    assert snapshot.process_state == "warmup_cache"
    assert snapshot.lag_seconds == pytest.approx(3.2)
    assert snapshot.market_pause is True
    assert snapshot.market_pause_reason == "calendar"
    assert snapshot.next_open_ms == 1764022500000
    assert snapshot.last_heartbeat_iso == "2025-11-30T22:28:52+00:00"
    assert snapshot.published_bars == 8
    assert snapshot.stream_targets == ctx.stream_targets
    state = fsl.get_fxcm_feed_state()
    assert state.seconds_to_open == pytest.approx(5400.0)
    assert state.session is not None
    assert state.session.get("tag") == "NY_METALS"


def test_apply_heartbeat_computes_lag_without_context(monkeypatch: pytest.MonkeyPatch):
    now = time.time()

    monkeypatch.setattr(fsl.time, "time", lambda: now)
    monkeypatch.setattr(fsl.time, "monotonic", lambda: now)
    hb = FxcmHeartbeat(
        type="heartbeat",
        state="stream",
        last_bar_close_ms=int((now - 5.0) * 1000),
        context=None,
    )
    snapshot = fsl._apply_heartbeat(hb)
    assert snapshot.lag_seconds == pytest.approx(5.0, rel=0.05)


def test_apply_market_status_updates_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_time = 1_700_000_100.0
    monkeypatch.setattr(fsl.time, "time", lambda: fake_time)
    monkeypatch.setattr(fsl.time, "monotonic", lambda: fake_time)
    status = FxcmMarketStatus(
        type="market_status",
        state="closed",
        next_open_ms=1764022500000,
        next_open_in_seconds=3600,  # type: ignore
        next_open_utc="2025-11-30T22:15:00Z",
        ts="2025-11-30T22:29:00+00:00",
        session=FxcmSessionContext(tag="NY_METALS", timezone="America/New_York"),
    )
    snapshot = fsl._apply_market_status(status)
    assert snapshot.market_state == "closed"
    assert snapshot.next_open_ms == 1764022500000
    assert snapshot.seconds_to_open == 3600
    assert snapshot.next_open_utc == "2025-11-30T22:15:00Z"
    assert snapshot.last_market_status_ts == fake_time
    assert snapshot.last_market_status_iso == "2025-11-30T22:29:00+00:00"
    assert snapshot.session is not None
    assert snapshot.session.get("tag") == "NY_METALS"


def test_market_status_open_clears_next_open(monkeypatch: pytest.MonkeyPatch) -> None:
    base_time = 1_764_353_600.0
    monkeypatch.setattr(fsl.time, "time", lambda: base_time)
    monkeypatch.setattr(fsl.time, "monotonic", lambda: base_time)
    hb = FxcmHeartbeat(
        type="heartbeat",
        state="idle",
        last_bar_close_ms=int((base_time - 5.0) * 1000),
        context=FxcmHeartbeatContext(next_open_utc="2025-11-30T22:15:00+00:00"),
    )
    fsl._apply_heartbeat(hb)

    status = FxcmMarketStatus(
        type="market_status",
        state="open",
        next_open_ms=None,
        next_open_in_seconds=0,  # type: ignore
        next_open_utc="2025-12-01T00:00:00Z",
    )
    snapshot = fsl._apply_market_status(status)

    assert snapshot.market_state == "open"
    assert snapshot.next_open_utc is None


def test_note_fxcm_bar_close_updates_state(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 1_700_000_200.0
    monkeypatch.setattr(fsl.time, "time", lambda: now)
    close_ms = int((now - 3.0) * 1000)

    fsl.note_fxcm_bar_close(close_ms)

    state = fsl.get_fxcm_feed_state()
    assert state.last_bar_close_ms == close_ms
    assert state.lag_seconds is None


def test_fxcm_feed_state_to_metrics_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 1_700_000_400.0
    monkeypatch.setattr(time, "time", lambda: now)
    state = fsl.FxcmFeedState(
        market_state="open",
        process_state="stream",
        lag_seconds=2.5,
        last_bar_close_ms=int((now - 2.5) * 1000),
        next_open_ms=int((now + 600.0) * 1000),
        last_heartbeat_iso="2025-11-30T22:28:52+00:00",
        last_market_status_iso="2025-11-30T22:29:00+00:00",
        published_bars=5,
        stream_targets=[{"symbol": "xauusd", "tf": "m1"}],
        session={"tag": "NY_METALS"},
        price_state="ok",
        ohlcv_state="delayed",
        status_note="idle",
        session_seconds_to_close=900.0,
        session_seconds_to_next_open=3600.0,
        session_name="NY",
        session_state="open",
    )

    metrics = state.to_metrics_dict()

    assert metrics["market"] == "OPEN"
    assert metrics["process_state"] == "STREAM"
    assert metrics["lag_seconds"] == pytest.approx(2.5)
    assert metrics["last_bar_close_ms"] == state.last_bar_close_ms
    assert metrics["next_open_utc"].endswith("Z")
    assert metrics["heartbeat_ts"] == "2025-11-30T22:28:52+00:00"
    assert metrics["market_status_ts"] == "2025-11-30T22:29:00+00:00"
    assert metrics["published_bars"] == 5
    assert metrics["stream_targets"] == state.stream_targets
    assert metrics["session"] == state.session
    assert metrics["price_state"] == "ok"
    assert metrics["ohlcv_state"] == "delayed"
    assert metrics["status_note"] == "idle"
    assert metrics["session_seconds_to_close"] == pytest.approx(900.0)
    assert metrics["session_seconds_to_next_open"] == pytest.approx(3600.0)


def test_heartbeat_sets_market_open_when_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.time()
    monkeypatch.setattr(fsl.time, "time", lambda: now)
    monkeypatch.setattr(fsl.time, "monotonic", lambda: now)
    hb = FxcmHeartbeat(
        type="heartbeat",
        state="stream",
        last_bar_close_ms=int((now - 1.0) * 1000),
        context=None,
    )

    fsl._apply_heartbeat(hb)

    state = fsl.get_fxcm_feed_state()
    assert state.market_state == "open"


def test_apply_status_snapshot_updates_price_and_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_ts = 1_700_000_000.0
    monkeypatch.setattr(fsl.time, "monotonic", lambda: base_ts)
    status = FxcmAggregatedStatus(
        market="closed",
        process="sleep",
        price="down",
        ohlcv="delayed",
        note="maintenance",
        ts=base_ts,
        session=FxcmSessionContext(
            name="Tokyo",
            state="open",
            seconds_to_close=600,
            seconds_to_next_open=1800,  # type: ignore
        ),
    )

    snapshot = fsl._apply_status_snapshot(status)

    assert snapshot.market_state == "closed"
    assert snapshot.process_state == "sleep"
    assert snapshot.price_state == "down"
    assert snapshot.ohlcv_state == "delayed"
    assert snapshot.status_note == "maintenance"
    assert snapshot.status_ts_iso is not None
    assert snapshot.session_seconds_to_close == pytest.approx(600)
    assert snapshot.session_seconds_to_next_open == pytest.approx(1800)
    assert snapshot.session_name == "Tokyo"

    metrics = snapshot.to_metrics_dict()
    assert metrics["price_state"] == "down"
    assert metrics["ohlcv_state"] == "delayed"
    assert metrics["session_name"] == "Tokyo"
