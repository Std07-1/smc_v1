"""Перевірки форматування ціни (Stage1 UI)."""

from core.formatters import fmt_price_stage1


def test_format_price_two_decimals_with_comma() -> None:
    assert fmt_price_stage1(4131.31, "xauusd") == "4131,31"


def test_format_price_handles_small_numbers() -> None:
    assert fmt_price_stage1(0.012345, "btc") == "0,0123"
    assert fmt_price_stage1(0.000123, "btc") == "0,000123"
