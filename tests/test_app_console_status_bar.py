"""Тести для консольного status bar (Rich Live).

Важливо тестувати не сам Rich, а наше перетворення стану у snapshot.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

from app.console_status_bar import _stderr_is_tty, build_status_snapshot
from data.fxcm_status_listener import FxcmFeedState


def test_build_snapshot_prefers_smc_meta_pipeline_state() -> None:
    smc_payload = {
        "meta": {
            "pipeline_state": "IDLE",
            "cycle_reason": "smc_idle_fxcm_status",
            "fxcm_idle_reason": "fxcm_market_closed",
        },
        "fxcm": {
            "market_state": "closed",
            "price_state": "ok",
            "ohlcv_state": "ok",
            "lag_seconds": 12.3,
            "next_open_utc": "2025-12-13 12:00:00Z",
        },
    }
    snap = build_status_snapshot(
        smc_payload=smc_payload,
        fxcm_state=None,
        redis_connected=True,
        sleep_for=0.5,
    )
    assert snap["mode"] == "IDLE"
    assert snap["market_open"] is False
    assert snap["ticks_alive"] is True
    assert snap["redis_connected"] is True
    assert snap["idle_reason"] == "fxcm_market_closed"
    assert snap["lag_seconds"] == 12.3
    assert snap["smc_state"] == "IDLE"
    assert snap["smc_reason"] == "fxcm_market_closed"


def test_build_snapshot_falls_back_to_fxcm_feed_state() -> None:
    fxcm_state = FxcmFeedState(
        market_state="open",
        price_state="stale",
        ohlcv_state="ok",
        lag_seconds=5.0,
        next_open_utc="2025-12-13 13:00:00Z",
        session_name="NY",
        session_state="OPEN",
        session_seconds_to_close=120.0,
        session_seconds_to_next_open=3600.0,
    )
    snap = build_status_snapshot(
        smc_payload=None,
        fxcm_state=fxcm_state,
        redis_connected=False,
        sleep_for=1.0,
    )
    assert snap["mode"] == "?"
    assert snap["market_open"] is True
    assert snap["ticks_alive"] is False
    assert snap["redis_connected"] is False
    assert snap["lag_seconds"] == 5.0
    assert snap["session_name"] == "NY"
    assert snap["session_state"] == "OPEN"
    assert snap["session_seconds_to_close"] == 120.0
    assert snap["session_seconds_to_next_open"] is None


def test_build_snapshot_forces_session_closed_when_market_closed() -> None:
    fxcm_state = FxcmFeedState(
        market_state="closed",
        price_state="ok",
        ohlcv_state="ok",
        lag_seconds=0.0,
        next_open_utc="2025-12-14T23:00:00+00:00",
        session_name="Tokyo Metals",
        session_state="OPEN",
        session_seconds_to_close=15_000.0,
        session_seconds_to_next_open=159_822.0,
    )
    snap = build_status_snapshot(
        smc_payload=None,
        fxcm_state=fxcm_state,
        redis_connected=True,
        sleep_for=0.5,
    )
    assert snap["market_open"] is False
    assert snap["session_name"] == "Tokyo Metals"
    assert snap["session_state"] == "CLOSED"
    assert snap["session_seconds_to_close"] is None
    assert snap["session_seconds_to_next_open"] == 159_822.0


def test_stderr_tty_check_uses_stderr(monkeypatch) -> None:
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False)
    assert _stderr_is_tty() is False

    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    assert _stderr_is_tty() is True


def test_build_snapshot_includes_cycle_and_pipeline_meta_and_age() -> None:
    smc_payload = {
        "meta": {
            "pipeline_state": "LIVE",
            "pipeline_ready_assets": 3,
            "pipeline_assets_total": 10,
            "pipeline_ready_pct": 0.3,
            "pipeline_processed_assets": 5,
            "pipeline_skipped_assets": 2,
            "cycle_seq": 7,
            "cycle_duration_ms": 123.4,
            "ts": "2025-12-13T00:00:00Z",
        },
        "fxcm": {"market_state": "open", "price_state": "ok", "ohlcv_state": "ok"},
    }
    now = datetime(2025, 12, 13, 0, 0, 2, tzinfo=UTC)
    snap = build_status_snapshot(
        smc_payload=smc_payload,
        fxcm_state=None,
        redis_connected=True,
        sleep_for=0.5,
        uptime_seconds=90061.0,
        now_utc=now,
    )
    assert snap["pipeline_ready_assets"] == 3
    assert snap["pipeline_assets_total"] == 10
    assert snap["pipeline_ready_pct"] == 0.3
    assert snap["pipeline_processed_assets"] == 5
    assert snap["pipeline_skipped_assets"] == 2
    assert snap["cycle_seq"] == 7
    assert snap["cycle_duration_ms"] == 123.4
    assert snap["snapshot_age_seconds"] == 2.0
    assert snap["smc_state"] == "WAIT"
    assert snap["uptime_seconds"] == 90061.0


def test_build_snapshot_includes_connector_health_from_fxcm_status_ts() -> None:
    fxcm_state = FxcmFeedState(
        market_state="open",
        price_state="ok",
        ohlcv_state="ok",
        process_state="up",
        status_ts=1_000.0,
    )
    now = datetime.fromtimestamp(1_005.0, tz=UTC)
    snap = build_status_snapshot(
        smc_payload=None,
        fxcm_state=fxcm_state,
        redis_connected=True,
        sleep_for=0.5,
        now_utc=now,
    )
    assert snap["connector_state"] == "ok"
    assert snap["connector_age_seconds"] == 5.0
