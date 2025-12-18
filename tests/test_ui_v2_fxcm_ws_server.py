"""Тести для WS-проксі FXCM (OHLCV/ticks/status)."""

from __future__ import annotations

from UI_v2.fxcm_ohlcv_ws_server import FxcmOhlcvWsServer, _should_gate_fxcm_payload


def test_extract_ohlcv_selection_ok() -> None:
    selection = FxcmOhlcvWsServer._extract_ohlcv_selection(
        "/fxcm/ohlcv?symbol=XAUUSD&tf=1m"
    )
    assert selection == ("XAUUSD", "1m")


def test_extract_ohlcv_selection_missing_params() -> None:
    assert FxcmOhlcvWsServer._extract_ohlcv_selection("/fxcm/ohlcv") is None
    assert (
        FxcmOhlcvWsServer._extract_ohlcv_selection("/fxcm/ohlcv?symbol=XAUUSD") is None
    )
    assert FxcmOhlcvWsServer._extract_ohlcv_selection("/fxcm/ohlcv?tf=1m") is None


def test_extract_ohlcv_selection_wrong_path() -> None:
    assert (
        FxcmOhlcvWsServer._extract_ohlcv_selection("/fxcm/ticks?symbol=XAUUSD") is None
    )


def test_extract_tick_selection_ok() -> None:
    symbol = FxcmOhlcvWsServer._extract_tick_selection("/fxcm/ticks?symbol=xauusd")
    assert symbol == "XAUUSD"


def test_extract_tick_selection_missing_symbol() -> None:
    assert FxcmOhlcvWsServer._extract_tick_selection("/fxcm/ticks") is None
    assert FxcmOhlcvWsServer._extract_tick_selection("/fxcm/ticks?symbol=") is None


def test_extract_tick_selection_wrong_path() -> None:
    assert (
        FxcmOhlcvWsServer._extract_tick_selection("/fxcm/ohlcv?symbol=XAUUSD&tf=1m")
        is None
    )


def test_is_status_path_ok() -> None:
    assert FxcmOhlcvWsServer._is_status_path("/fxcm/status") is True
    assert FxcmOhlcvWsServer._is_status_path("/fxcm/status?foo=bar") is True


def test_is_status_path_wrong_path() -> None:
    assert FxcmOhlcvWsServer._is_status_path("/fxcm/ohlcv?symbol=XAUUSD&tf=1m") is False
    assert FxcmOhlcvWsServer._is_status_path("/fxcm/ticks?symbol=XAUUSD") is False


def test_strict_gate_disabled_never_gates_invalid_payloads() -> None:
    assert (
        _should_gate_fxcm_payload(
            "ohlcv",
            {"symbol": "XAUUSD", "tf": "1m", "bars": "not-a-list"},
            strict_enabled=False,
        )
        is False
    )
    assert (
        _should_gate_fxcm_payload(
            "ticks",
            {"symbol": "XAUUSD"},
            strict_enabled=False,
        )
        is False
    )
    assert (
        _should_gate_fxcm_payload(
            "status",
            "not-a-dict",
            strict_enabled=False,
        )
        is False
    )


def test_strict_gate_enabled_gates_invalid_payloads() -> None:
    assert (
        _should_gate_fxcm_payload(
            "ohlcv",
            {"symbol": "XAUUSD", "tf": "1m", "bars": "not-a-list"},
            strict_enabled=True,
        )
        is True
    )
    assert (
        _should_gate_fxcm_payload(
            "ticks",
            {"symbol": "XAUUSD"},
            strict_enabled=True,
        )
        is True
    )
    assert (
        _should_gate_fxcm_payload(
            "status",
            "not-a-dict",
            strict_enabled=True,
        )
        is True
    )
