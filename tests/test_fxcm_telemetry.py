"""Тести синхронізації FXCM-телеметрії між сховищем та UI."""

from __future__ import annotations

from typing import Any

import pytest

from data.fxcm_status_listener import FxcmFeedState
from data.unified_store import UnifiedDataStore
from UI.experimental_viewer import SmcExperimentalViewer
from UI.experimental_viewer_extended import SmcExperimentalViewerExtended
from UI.ui_consumer import UIConsumer


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
    store = UnifiedDataStore(redis=_DummyRedis())  # type: ignore
    close_ms = 1_700_000_000_000
    state = FxcmFeedState(
        market_state="open",
        process_state="stream",
        lag_seconds=12.3,
        last_bar_close_ms=close_ms,
        next_open_ms=close_ms + 3_600_000,
    )
    monkeypatch.setattr("data.unified_store.get_fxcm_feed_state", lambda: state)

    snapshot = store.metrics_snapshot()

    fxcm_block = snapshot.get("fxcm")
    assert isinstance(fxcm_block, dict)
    assert fxcm_block["market"] == "OPEN"
    assert fxcm_block["market_state"] == "open"
    assert fxcm_block["process_state"] == "STREAM"
    assert fxcm_block["lag_seconds"] == pytest.approx(12.3)
    assert fxcm_block["last_bar_close_ms"] == close_ms
    assert fxcm_block["last_close_utc"].endswith("Z")
    assert fxcm_block["next_open_utc"].endswith("Z")


def _minimal_asset() -> dict[str, Any]:
    return {
        "symbol": "xauusd",
        "stats": {"current_price": 2375.0, "session_tag": "LONDON"},
        "smc": {"structure": {}, "liquidity": {}, "zones": {}},
    }


def _fxcm_payload() -> dict[str, Any]:
    return {
        "market": "OPEN",
        "market_state": "open",
        "process": "STREAM",
        "process_state": "STREAM",
        "lag_seconds": 4.2,
        "last_bar_close_ms": 1_700_000_000_000,
        "last_close_utc": "2023-11-14 22:13:20Z",
        "next_open_utc": "2025-01-01T09:00:00Z",
        "price_state": "ok",
        "ohlcv_state": "delayed",
        "status_note": "ok",
        "session": {
            "tag": "NY_METALS",
            "next_open_utc": "2025-01-01T09:00:00Z",
            "seconds_to_close": 90,
            "seconds_to_next_open": 0.0,
        },
    }


def _table_to_dict(table: Any) -> dict[str, str]:
    labels = list(table.columns[0]._cells)
    values = list(table.columns[1]._cells)
    return {
        str(label): str(value) for label, value in zip(labels, values, strict=False)
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
    assert state["meta"]["fxcm"]["market"] == "OPEN"
    assert state["session"] == "NY_METALS"


def test_extended_viewer_composes_fxcm_rows(tmp_path: Any) -> None:
    viewer = SmcExperimentalViewerExtended("xauusd", snapshot_dir=str(tmp_path))
    state = viewer.build_state(
        _minimal_asset(),
        {"ts": "2025-11-25T12:00:00Z"},
        _fxcm_payload(),
    )

    rows = viewer._compose_fxcm_rows(state)  # noqa: SLF001
    assert ("Market", "🟢 OPEN") in rows
    assert ("Price", "OK") in rows
    assert ("OHLCV", "DELAYED") in rows
    assert any("22:13:20 UTC" in value for _, value in rows)
    lag_value = next(value for label, value in rows if label == "Лаг")
    assert "4с" in lag_value
    assert "200мс" in lag_value
    next_open_value = dict(rows).get("Наступне відкриття")
    assert next_open_value == "-"
    close_value = dict(rows).get("До закриття")
    assert close_value == "1m 30s"


def test_extended_viewer_uses_session_next_open(tmp_path: Any) -> None:
    viewer = SmcExperimentalViewerExtended("xauusd", snapshot_dir=str(tmp_path))
    fxcm_payload = _fxcm_payload()
    fxcm_payload["next_open_utc"] = "-"
    fxcm_payload["market_state"] = "closed"
    fxcm_payload["market"] = "CLOSED"
    fxcm_payload["session"] = {
        "tag": "TOKYO",
        "next_open_utc": "2025-12-01T12:55:00+00:00",
    }
    state = viewer.build_state(
        _minimal_asset(),
        {"ts": "2025-11-25T12:00:00Z"},
        fxcm_payload,
    )

    rows = viewer._compose_fxcm_rows(state)  # noqa: SLF001
    next_open = next(value for label, value in rows if label == "Наступне відкриття")
    assert "2025-12-01 12:55:00 UTC" in next_open


def test_viewer_session_fallbacks_to_fxcm_session(tmp_path: Any) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    asset = {
        "symbol": "xauusd",
        "stats": {"current_price": 2375.0},
        "smc": {"structure": {}, "liquidity": {}, "zones": {}},
    }
    fxcm_payload = _fxcm_payload()
    fxcm_payload["session"] = {"tag": "TOKYO_METALS"}

    state = viewer.build_state(asset, {"ts": "2025-11-25T12:00:00Z"}, fxcm_payload)

    assert state["session"] == "TOKYO_METALS"


def test_viewer_session_falls_back_to_asset_stats(tmp_path: Any) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    asset = _minimal_asset()
    fxcm_payload = _fxcm_payload()
    fxcm_payload["session"] = None

    state = viewer.build_state(asset, {"ts": "2025-11-25T12:00:00Z"}, fxcm_payload)

    assert state["session"] == "LONDON"


def test_base_viewer_uses_session_next_open(tmp_path: Any) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    fxcm_payload = _fxcm_payload()
    fxcm_payload["next_open_utc"] = "-"
    fxcm_payload["market_state"] = "closed"
    fxcm_payload["market"] = "CLOSED"
    fxcm_payload["session"] = {
        "tag": "TOKYO",
        "next_open_utc": "2025-12-01T12:55:00+00:00",
    }
    state = viewer.build_state(
        _minimal_asset(),
        {"ts": "2025-11-25T12:00:00Z"},
        fxcm_payload,
    )

    table = viewer._build_fxcm_table(state["fxcm"])
    label_to_value = _table_to_dict(table)

    assert "2025-12-01 12:55:00 UTC" in label_to_value["Next open"]


def test_base_viewer_next_open_dash_when_market_open(tmp_path: Any) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    fxcm_payload = _fxcm_payload()
    fxcm_payload["session"] = {
        "tag": "TOKYO",
        "next_open_utc": "2025-12-01T12:55:00+00:00",
    }
    state = viewer.build_state(
        _minimal_asset(),
        {"ts": "2025-11-25T12:00:00Z"},
        fxcm_payload,
    )

    table = viewer._build_fxcm_table(state["fxcm"])
    label_to_value = _table_to_dict(table)

    assert label_to_value["Next open"] == "-"


def test_extended_viewer_next_open_dash_when_market_open(tmp_path: Any) -> None:
    viewer = SmcExperimentalViewerExtended("xauusd", snapshot_dir=str(tmp_path))
    state = viewer.build_state(
        _minimal_asset(),
        {"ts": "2025-11-25T12:00:00Z"},
        _fxcm_payload(),
    )

    rows = dict(viewer._compose_fxcm_rows(state))  # noqa: SLF001

    assert rows["Наступне відкриття"] == "-"


def test_extended_viewer_hides_close_countdown_when_market_closed(
    tmp_path: Any,
) -> None:
    viewer = SmcExperimentalViewerExtended("xauusd", snapshot_dir=str(tmp_path))
    payload = _fxcm_payload()
    payload["market_state"] = "closed"
    payload["market"] = "CLOSED"
    state = viewer.build_state(
        _minimal_asset(),
        {"ts": "2025-11-25T12:00:00Z"},
        payload,
    )

    rows = dict(viewer._compose_fxcm_rows(state))  # noqa: SLF001

    assert rows["До закриття"] == "-"


def test_extended_viewer_formats_long_lag(tmp_path: Any) -> None:
    viewer = SmcExperimentalViewerExtended("xauusd", snapshot_dir=str(tmp_path))
    payload = _fxcm_payload()
    payload["lag_seconds"] = 172800.5  # 2д + 0.5с
    state = viewer.build_state(
        _minimal_asset(),
        {"ts": "2025-11-25T12:00:00Z"},
        payload,
    )

    lag_value = dict(viewer._compose_fxcm_rows(state))["Лаг"]  # noqa: SLF001

    assert "2д" in lag_value
    assert "мс" in lag_value
    assert "172800.5с" in lag_value


def test_ui_consumer_prefers_meta_fxcm_block() -> None:
    consumer = UIConsumer()
    payload = {"meta": {"fxcm": {"market_state": "open"}}}

    fxcm_block = consumer._extract_meta_fxcm(payload)

    assert fxcm_block is payload["meta"]["fxcm"]
    assert consumer._fxcm_meta_warned is False


def test_ui_consumer_falls_back_to_top_level_fxcm_block() -> None:
    consumer = UIConsumer()
    payload = {"fxcm": {"market_state": "open", "lag_seconds": 3.0}}

    fxcm_block = consumer._extract_meta_fxcm(payload)

    assert fxcm_block is payload["fxcm"]
    assert consumer._fxcm_meta_warned is True


def test_base_viewer_hides_close_countdown_when_market_closed(tmp_path: Any) -> None:
    viewer = SmcExperimentalViewer("xauusd", snapshot_dir=str(tmp_path))
    payload = _fxcm_payload()
    payload["market_state"] = "closed"
    payload["market"] = "CLOSED"
    state = viewer.build_state(
        _minimal_asset(),
        {"ts": "2025-11-25T12:00:00Z"},
        payload,
    )

    table = viewer._build_fxcm_table(state["fxcm"])
    label_to_value = _table_to_dict(table)

    assert label_to_value["До закриття"] == "-"
