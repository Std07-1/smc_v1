"""Перевірки форматування ціни у utils.format_price."""

from utils.utils import format_price


def test_format_price_two_decimals_with_comma() -> None:
    assert format_price(4131.31, "xauusd") == "4131,31"


def test_format_price_handles_small_numbers() -> None:
    assert format_price(0.012345, "btc") == "0,0123"
    assert format_price(0.000123, "btc") == "0,000123"
