"""Юніт-тести для fxcm_status_listener."""

from __future__ import annotations

import time
from typing import Any

import pytest

from core.contracts.fxcm_telemetry import parse_fxcm_aggregated_status
from data import fxcm_status_listener as fsl


def setup_function() -> None:
    fsl._reset_fxcm_feed_state_for_tests()


def test_default_state_unknown() -> None:
    state = fsl.get_fxcm_feed_state()
    assert state.market_state == "unknown"
    assert state.process_state == "unknown"
    assert state.lag_seconds is None


_LEGACY_REASON = (
    "Legacy: тест написано під старий listener (fxcm:heartbeat/fxcm:market_status) "
    "та поля FxcmFeedState, яких у поточному runtime вже немає. "
    "Актуальний listener читає лише fxcm:status. Див. хвилю T1."
)


@pytest.mark.skip(reason=_LEGACY_REASON)
class TestLegacyFxcmStatusListener:
    def test_apply_heartbeat_updates_context_fields(self) -> None:
        from data.fxcm_models import (
            FxcmHeartbeat,
            FxcmHeartbeatContext,
            FxcmSessionContext,
        )

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
        apply_heartbeat = getattr(fsl, "_apply_heartbeat", None)
        if not callable(apply_heartbeat):
            pytest.skip("Legacy: _apply_heartbeat відсутній у поточному runtime.")
        snapshot: Any = apply_heartbeat(hb)
        assert snapshot.process_state == "warmup_cache"

    def test_apply_heartbeat_computes_lag_without_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from data.fxcm_models import FxcmHeartbeat

        now = time.time()
        monkeypatch.setattr(fsl.time, "time", lambda: now)
        monkeypatch.setattr(fsl.time, "monotonic", lambda: now)
        hb = FxcmHeartbeat(
            type="heartbeat",
            state="stream",
            last_bar_close_ms=int((now - 5.0) * 1000),
            context=None,
        )
        apply_heartbeat = getattr(fsl, "_apply_heartbeat", None)
        if not callable(apply_heartbeat):
            pytest.skip("Legacy: _apply_heartbeat відсутній у поточному runtime.")
        snapshot: Any = apply_heartbeat(hb)
        assert snapshot.lag_seconds == pytest.approx(5.0, rel=0.05)

    def test_apply_market_status_updates_fields(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from data.fxcm_models import FxcmMarketStatus, FxcmSessionContext

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
        apply_market_status = getattr(fsl, "_apply_market_status", None)
        if not callable(apply_market_status):
            pytest.skip("Legacy: _apply_market_status відсутній у поточному runtime.")
        snapshot: Any = apply_market_status(status)
        assert snapshot.market_state == "closed"

    def test_market_status_open_clears_next_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from data.fxcm_models import (
            FxcmHeartbeat,
            FxcmHeartbeatContext,
            FxcmMarketStatus,
        )

        base_time = 1_764_353_600.0
        monkeypatch.setattr(fsl.time, "time", lambda: base_time)
        monkeypatch.setattr(fsl.time, "monotonic", lambda: base_time)
        hb = FxcmHeartbeat(
            type="heartbeat",
            state="idle",
            last_bar_close_ms=int((base_time - 5.0) * 1000),
            context=FxcmHeartbeatContext(next_open_utc="2025-11-30T22:15:00+00:00"),
        )
        apply_heartbeat = getattr(fsl, "_apply_heartbeat", None)
        if not callable(apply_heartbeat):
            pytest.skip("Legacy: _apply_heartbeat відсутній у поточному runtime.")
        apply_heartbeat(hb)

        status = FxcmMarketStatus(
            type="market_status",
            state="open",
            next_open_ms=None,
            next_open_in_seconds=0,  # type: ignore
            next_open_utc="2025-12-01T00:00:00Z",
        )
        apply_market_status = getattr(fsl, "_apply_market_status", None)
        if not callable(apply_market_status):
            pytest.skip("Legacy: _apply_market_status відсутній у поточному runtime.")
        snapshot: Any = apply_market_status(status)
        assert snapshot.market_state == "open"


def test_note_fxcm_bar_close_updates_state(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 1_700_000_200.0
    monkeypatch.setattr(fsl.time, "time", lambda: now)
    close_ms = int((now - 3.0) * 1000)

    fsl.note_fxcm_bar_close(close_ms)

    state = fsl.get_fxcm_feed_state()
    assert state.last_bar_close_ms == close_ms
    assert state.lag_seconds == pytest.approx(3.0, rel=0.01)


def test_apply_status_snapshot_updates_price_and_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_ts = 1_700_000_000.0
    monkeypatch.setattr(fsl.time, "monotonic", lambda: base_ts)
    status = parse_fxcm_aggregated_status(
        {
            "market": "closed",
            "process": "sleep",
            "price": "down",
            "ohlcv": "delayed",
            "note": "maintenance",
            "ts": base_ts,
            "session": {
                "name": "Tokyo",
                "state": "open",
                "seconds_to_close": 600,
                "next_open_seconds": 1800,
            },
        }
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
