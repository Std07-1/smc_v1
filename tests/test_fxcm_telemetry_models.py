import json

import pytest

from data.fxcm_models import (
    FxcmHeartbeat,
    FxcmHeartbeatContext,
    FxcmMarketStatus,
    parse_fxcm_heartbeat,
    parse_fxcm_market_status,
)


def test_parse_full_heartbeat_with_context():
    payload = {
        "type": "heartbeat",
        "state": "warmup",
        "last_bar_close_ms": 1764002159999,
        "ts": "2025-11-30T22:28:52+00:00",
        "context": {
            "lag_seconds": 4.3,
            "market_pause": False,
            "market_pause_reason": None,
            "next_open_seconds": 0,
            "stream_targets": [
                {"symbol": "XAU/USD", "tf": "m1", "staleness_seconds": 0.1}
            ],
            "published_bars": 128,
            "next_open_utc": "2025-11-30T22:15:00Z",
            "next_open_ms": 1764022500000,
            "idle_reason": "maintenance",
            "session": {
                "tag": "NY_METALS",
                "timezone": "America/New_York",
                "weekly_open": "18:00@America/New_York",
                "weekly_close": "16:55@America/New_York",
                "daily_breaks": [
                    {"start": "17:00", "end": "18:00", "tz": "America/New_York"}
                ],
                "next_open_utc": "2025-11-30T22:15:00Z",
                "next_open_seconds": 900,
            },
        },
    }
    result = parse_fxcm_heartbeat(json.dumps(payload))
    assert isinstance(result, FxcmHeartbeat)
    assert result.state == "warmup"
    assert result.last_bar_close_ms == 1764002159999
    assert result.ts == "2025-11-30T22:28:52+00:00"
    assert isinstance(result.context, FxcmHeartbeatContext)
    assert result.context.lag_seconds == pytest.approx(4.3)
    assert isinstance(result.context.stream_targets, list)
    assert result.context.bars_published == 128
    assert result.context.seconds_to_open == 0
    assert result.context.idle_reason == "maintenance"
    assert result.context.session is not None
    assert result.context.session.tag == "NY_METALS"


def test_parse_heartbeat_without_context():
    payload = {
        "type": "heartbeat",
        "state": "stream",
        "last_bar_close_ms": None,
    }
    result = parse_fxcm_heartbeat(payload)
    assert result.context is None
    assert result.state == "stream"


def test_parse_market_status_open_closed():
    payload_open = {
        "type": "market_status",
        "state": "open",
        "next_open_ms": None,
        "next_open_in_seconds": 0,
        "ts": "2025-11-30T22:29:00+00:00",
    }
    open_status = parse_fxcm_market_status(payload_open)
    assert isinstance(open_status, FxcmMarketStatus)
    assert open_status.state == "open"
    assert open_status.seconds_to_open == 0
    assert open_status.ts == "2025-11-30T22:29:00+00:00"

    payload_closed = {
        "type": "market_status",
        "state": "closed",
        "next_open_ms": 1764022500000,
        "next_open_seconds": 5400,
        "session": {
            "tag": "NY_METALS",
            "timezone": "America/New_York",
            "next_open_seconds": 5400,
        },
    }
    closed_status = parse_fxcm_market_status(json.dumps(payload_closed))
    assert closed_status.state == "closed"
    assert closed_status.next_open_ms == 1764022500000
    assert closed_status.seconds_to_open == 5400
    assert closed_status.session is not None
    assert closed_status.session.tag == "NY_METALS"


def test_invalid_payload_raises_value_error():
    with pytest.raises(ValueError):
        parse_fxcm_heartbeat("   ")
    with pytest.raises(TypeError):
        parse_fxcm_market_status(123)  # type: ignore[arg-type]

    bad_type = {"type": "heartbeat", "state": "bad"}
    with pytest.raises(Exception):
        parse_fxcm_heartbeat(bad_type)
