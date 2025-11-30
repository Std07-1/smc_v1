"""Тести синхронізації FXCM-телеметрії між сховищем та UI."""

from __future__ import annotations

from typing import Any

import pytest

from data.fxcm_status_listener import FxcmFeedState
from data.unified_store import UnifiedDataStore
from UI.experimental_viewer import SmcExperimentalViewer
from UI.experimental_viewer_extended import SmcExperimentalViewerExtended


class _DummyRedis:
    async def get(self, key: str) -> None:  # noqa: D401 - простий стаб
        return None

    async def set(
        self, key: str, value: Any, ex: int | None = None
    ) -> None:  # noqa: D401
        return None

    async def delete(self, key: str) -> None:  # noqa: D401
        return None


def test_metrics_snapshot_contains_fxcm_block(monkeypatch: pytest.MonkeyPatch) -> None:
    store = UnifiedDataStore(redis=_DummyRedis())
    close_ms = 1_700_000_000_000
    state = FxcmFeedState(
        market_state="open",
        process_state="stream",
        lag_seconds=12.3,
        last_bar_close_ms=close_ms,
        next_open_utc="2025-01-01T09:00:00Z",
    )
    monkeypatch.setattr("data.unified_store.get_fxcm_feed_state", lambda: state)

    snapshot = store.metrics_snapshot()

    fxcm_block = snapshot.get("fxcm")
    assert isinstance(fxcm_block, dict)
    assert fxcm_block["market_state"] == "open"
    assert fxcm_block["process_state"] == "stream"
    assert fxcm_block["lag_seconds"] == pytest.approx(12.3)
    assert fxcm_block["last_bar_close_ms"] == close_ms
    assert fxcm_block["next_open_utc"] == "2025-01-01T09:00:00Z"


def _minimal_asset() -> dict[str, Any]:
    return {
        "symbol": "xauusd",
        "stats": {"current_price": 2375.0, "session_tag": "LONDON"},
        "smc": {"structure": {}, "liquidity": {}, "zones": {}},
    }


def _fxcm_payload() -> dict[str, Any]:
    return {
        "market_state": "open",
        "process_state": "stream",
        "lag_seconds": 4.2,
        "last_bar_close_ms": 1_700_000_000_000,
        "next_open_utc": "2025-01-01T09:00:00Z",
    }


def test_viewer_state_contains_fxcm_block(tmp_path: Any) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    state = viewer.build_state(
        _minimal_asset(), {"ts": "2025-11-25T12:00:00Z"}, _fxcm_payload()
    )

    fxcm_state = state["fxcm"]
    assert isinstance(fxcm_state, dict)
    assert fxcm_state["market_state"] == "open"
    assert fxcm_state["process_state"] == "stream"
    assert fxcm_state["lag_seconds"] == pytest.approx(4.2)
    assert fxcm_state["last_bar_close_utc"] == "2023-11-14 22:13:20Z"


def test_extended_viewer_composes_fxcm_rows(tmp_path: Any) -> None:
    viewer = SmcExperimentalViewerExtended("xauusd", snapshot_dir=str(tmp_path))
    state = viewer.build_state(
        _minimal_asset(),
        {"ts": "2025-11-25T12:00:00Z"},
        _fxcm_payload(),
    )

    rows = viewer._compose_fxcm_rows(state["fxcm"])  # noqa: SLF001
    assert ("Market", "🟢 OPEN") in rows
    assert any("22:13:20 UTC" in value for _, value in rows)
    assert any("2025-01-01 09:00:00 UTC" in value for _, value in rows)
