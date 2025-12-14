"""Тести для WS-проксі FXCM (OHLCV/ticks)."""

from __future__ import annotations

from UI_v2.fxcm_ohlcv_ws_server import FxcmOhlcvWsServer


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
