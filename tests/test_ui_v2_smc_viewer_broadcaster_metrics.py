"""Метрики для UI_v2.smc_viewer_broadcaster."""

from __future__ import annotations

from typing import Any

import pytest
from prometheus_client import REGISTRY

from UI_v2.schemas import UiSmcAssetPayload, UiSmcStatePayload
from UI_v2.smc_viewer_broadcaster import _process_smc_payload_with_metrics
from UI_v2 import smc_viewer_broadcaster as broadcaster_module
from UI_v2.viewer_state_builder import ViewerStateCache


def _metric_value(name: str) -> float:
    value = REGISTRY.get_sample_value(name)
    return float(value or 0.0)


def _snapshot_metrics() -> dict[str, float]:
    return {
        "messages": _metric_value("ai_one_smc_viewer_smc_messages_total"),
        "viewer_states": _metric_value("ai_one_smc_viewer_viewer_states_total"),
        "errors": _metric_value("ai_one_smc_viewer_errors_total"),
        "latency_count": _metric_value("ai_one_smc_viewer_build_latency_ms_count"),
    }


def _make_asset(symbol: str) -> UiSmcAssetPayload:
    return {
        "symbol": symbol,
        "stats": {
            "session_tag": "London",
            "current_price": 2412.5,
        },
        "smc_hint": {
            "structure": {},
            "liquidity": {},
            "zones": {},
            "signals": [],
            "meta": {},
        },
        "smc_structure": {
            "trend": "up",
            "bias": "long",
            "range_state": "dev_up",
            "legs": [],
            "swings": [],
            "ranges": [],
            "events": [],
            "ote_zones": [],
        },
        "smc_liquidity": {
            "amd_phase": "MANIP",
            "pools": [],
            "magnets": [],
        },
        "smc_zones": {"zones": []},
        "price": 2412.5,
    }


@pytest.mark.asyncio
async def test_process_payload_updates_metrics_and_snapshot() -> None:
    cache: dict[str, ViewerStateCache] = {}
    snapshot: dict[str, Any] = {}
    saved_snapshots: list[dict[str, Any]] = []
    published_states: list[dict[str, Any]] = []

    async def fake_save() -> None:
        saved_snapshots.append(dict(snapshot))

    async def fake_publish(states: dict[str, Any]) -> None:
        published_states.append(dict(states))

    payload: UiSmcStatePayload = {
        "type": "smc_state",
        "meta": {
            "ts": "2025-12-08T08:05:00+00:00",
            "seq": 1,
            "schema_version": "smc_state_v1",
        },
        "counters": {"assets": 1},
        "assets": [_make_asset("XAUUSD")],
        "fxcm": None,  # type: ignore
        "analytics": {},
    }

    before = _snapshot_metrics()

    await _process_smc_payload_with_metrics(
        payload=payload,
        cache_by_symbol=cache,
        snapshot_by_symbol=snapshot,
        save_snapshot_cb=fake_save,
        publish_cb=fake_publish,  # type: ignore
    )

    after = _snapshot_metrics()

    assert "XAUUSD" in snapshot
    assert len(saved_snapshots) == 1
    assert len(published_states) == 1
    assert after["messages"] == pytest.approx(before["messages"] + 1)
    assert after["viewer_states"] == pytest.approx(before["viewer_states"] + 1)
    assert after["errors"] == pytest.approx(before["errors"])
    assert after["latency_count"] == pytest.approx(before["latency_count"] + 1)


@pytest.mark.asyncio
async def test_process_payload_increments_error_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache: dict[str, ViewerStateCache] = {}
    snapshot: dict[str, Any] = {}
    saved_snapshots: list[dict[str, Any]] = []
    published_states: list[dict[str, Any]] = []

    async def fake_save() -> None:
        saved_snapshots.append(dict(snapshot))

    async def fake_publish(states: dict[str, Any]) -> None:
        published_states.append(dict(states))

    payload: UiSmcStatePayload = {
        "type": "smc_state",
        "meta": {},
        "counters": {"assets": 0},
        "assets": [],
        "fxcm": None,  # type: ignore
        "analytics": {},
    }

    def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("boom")

    monkeypatch.setattr(broadcaster_module, "build_viewer_states_from_payload", _boom)

    before = _snapshot_metrics()

    await _process_smc_payload_with_metrics(
        payload=payload,
        cache_by_symbol=cache,
        snapshot_by_symbol=snapshot,
        save_snapshot_cb=fake_save,
        publish_cb=fake_publish,  # type: ignore
    )

    after = _snapshot_metrics()

    assert saved_snapshots == []
    assert published_states == []
    assert after["messages"] == pytest.approx(before["messages"] + 1)
    assert after["errors"] == pytest.approx(before["errors"] + 1)
    assert after["viewer_states"] == pytest.approx(before["viewer_states"])
    assert after["latency_count"] == pytest.approx(before["latency_count"] + 1)

    # Повертаємо оригінальну функцію (monkeypatch зробить це автоматично).
