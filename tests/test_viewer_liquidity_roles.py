"""Тести підстановки ролей пулів у SmcExperimentalViewer."""

from __future__ import annotations

from typing import Any

from UI.experimental_viewer import SmcExperimentalViewer


def _asset_with_pools(
    bias: str, price: float, pools: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "symbol": "xauusd",
        "stats": {"current_price": price, "session_tag": "LONDON"},
        "smc": {
            "structure": {
                "trend": "RANGE",
                "bias": bias,
                "range_state": "NONE",
                "legs": [],
                "swings": [],
                "ranges": [],
                "events": [],
                "ote_zones": [],
            },
            "liquidity": {
                "amd_phase": "NEUTRAL",
                "pools": pools,
                "magnets": [],
            },
            "zones": {},
        },
    }


def test_pool_roles_fallback_for_neutral_bias(tmp_path: Any) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    pools = [
        {"level": 99.0, "liq_type": "EQL", "role": "NEUTRAL", "strength": 1.0},
        {"level": 101.0, "liq_type": "EQH", "role": "NEUTRAL", "strength": 1.0},
    ]
    asset = _asset_with_pools("NEUTRAL", 100.0, pools)

    state = viewer.build_state(asset, {"ts": "2025-01-01T00:00:00Z"})

    roles = [pool["role"] for pool in state["liquidity"]["pools"]]
    assert roles == ["PRIMARY", "COUNTERTREND"]


def test_range_extreme_uses_price_side(tmp_path: Any) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    pools = [
        {
            "level": 95.0,
            "liq_type": "RANGE_EXTREME",
            "role": "NEUTRAL",
            "strength": 1.0,
        },
        {
            "level": 105.0,
            "liq_type": "RANGE_EXTREME",
            "role": "NEUTRAL",
            "strength": 1.0,
        },
    ]
    asset = _asset_with_pools("NEUTRAL", 100.0, pools)

    state = viewer.build_state(asset, {"ts": "2025-01-01T00:00:00Z"})

    roles = [pool["role"] for pool in state["liquidity"]["pools"]]
    assert roles == ["PRIMARY", "COUNTERTREND"]


def test_fxcm_block_exposes_countdown_and_pause(tmp_path: Any) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    payload = {
        "market_state": "open",
        "process_state": "stream",
        "lag_seconds": 0.5,
        "lag_human": "0s (0.5s)",
        "last_bar_close_ms": 1_764_590_139_999,
        "next_open_utc": "2025-12-01T13:00:00Z",
        "seconds_to_open": 120,
        "seconds_to_close": 120,
        "market_pause": True,
        "market_pause_reason": "maintenance",
        "idle_reason": "calendar",
        "cache_source": "history_cache",
        "published_bars": 3,
        "heartbeat_ts": "2025-12-01T11:54:00+00:00",
        "market_status_ts": "2025-12-01T11:52:00+00:00",
        "price_state": "ok",
        "ohlcv_state": "delayed",
        "status_note": "idle",
    }

    fxcm_block = viewer._normalize_fxcm_block(payload)

    assert fxcm_block is not None
    assert fxcm_block["countdown"] == "2m 00s"
    assert fxcm_block["countdown_to_close"] == "2m 00s"
    assert fxcm_block["market_pause"] is True
    assert fxcm_block["market_pause_reason"] == "maintenance"
    assert fxcm_block["published_bars_delta"] is None
    assert fxcm_block["price_state"] == "ok"
    assert fxcm_block["ohlcv_state"] == "delayed"
    assert fxcm_block["status_note"] == "idle"

    payload["published_bars"] = 5
    payload["seconds_to_open"] = 30
    payload["seconds_to_close"] = 30
    fxcm_block_next = viewer._normalize_fxcm_block(payload)
    assert fxcm_block_next["published_bars_delta"] == 2  # type: ignore
    assert fxcm_block_next["countdown"] == "30s"  # type: ignore
    assert fxcm_block_next["countdown_to_close"] == "30s"  # type: ignore


def test_fxcm_block_includes_session_snapshot(tmp_path: Any) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    payload = {
        "session": {
            "tag": "NY_METALS",
            "timezone": "America/New_York",
            "weekly_open": "18:00@America/New_York",
            "weekly_close": "16:55@America/New_York",
            "daily_breaks": [
                {"start": "17:00", "end": "18:00", "tz": "America/New_York"}
            ],
            "next_open_seconds": 600,
        }
    }

    fxcm_block = viewer._normalize_fxcm_block(payload)

    assert fxcm_block is not None
    assert fxcm_block["session"]["tag"] == "NY_METALS"
    assert fxcm_block["session"]["timezone"] == "America/New_York"
    assert "17:00-18:00@America/New_York" in fxcm_block["session"]["daily_breaks"]
    assert fxcm_block["session"]["next_open_countdown"] == "10m 00s"
