"""Тести для FXCM гейтів у screening_producer."""

from __future__ import annotations

from app import screening_producer as sp
from config.config import FXCM_STALE_LAG_SECONDS
from config.constants import K_SIGNAL, K_STATS
from data.fxcm_status_listener import FxcmFeedState


def test_fxcm_gate_closed_state() -> None:
    state = FxcmFeedState(market_state="closed", next_open_utc="2025-01-01T10:00:00Z")
    payload = sp._evaluate_fxcm_gates("xauusd", state)
    assert payload is not None
    assert payload[K_SIGNAL] == "FX_MARKET_CLOSED"
    stats = payload.get(K_STATS) or {}
    assert stats.get("fxcm_state") == "closed"
    assert stats.get("fxcm_next_open_utc") == "2025-01-01T10:00:00Z"


def test_fxcm_gate_stale_feed() -> None:
    lag = FXCM_STALE_LAG_SECONDS + 60
    state = FxcmFeedState(market_state="open", process_state="stream", lag_seconds=lag)
    payload = sp._evaluate_fxcm_gates("xauusd", state)
    assert payload is not None
    assert payload[K_SIGNAL] == "FX_FEED_STALE"
    stats = payload.get(K_STATS) or {}
    assert stats.get("fxcm_lag_seconds") == lag


def test_fxcm_gate_price_down() -> None:
    state = FxcmFeedState(
        market_state="open",
        process_state="stream",
        price_state="down",
        status_note="price stream paused",
    )
    payload = sp._evaluate_fxcm_gates("xauusd", state)
    assert payload is not None
    assert payload[K_SIGNAL] == "FX_PRICE_DOWN"
    assert "price stream paused" in " ".join(payload.get("hints", []))


def test_fxcm_gate_ohlcv_delayed() -> None:
    state = FxcmFeedState(
        market_state="open",
        process_state="stream",
        ohlcv_state="delayed",
    )
    payload = sp._evaluate_fxcm_gates("xauusd", state)
    assert payload is not None
    assert payload[K_SIGNAL] == "FX_OHLCV_DELAYED"


def test_fxcm_gate_process_error_uses_note() -> None:
    state = FxcmFeedState(
        market_state="open",
        process_state="error",
        status_note="backoff 30s",
    )
    payload = sp._evaluate_fxcm_gates("xauusd", state)
    assert payload is not None
    assert payload[K_SIGNAL] == "FX_PROCESS_ERROR"
    hint_text = " ".join(payload.get("hints", []))
    assert "backoff 30s" in hint_text
