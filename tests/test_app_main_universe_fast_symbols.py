"""Перевірка логування розривів FXCM_FAST_SYMBOLS vs smc_universe."""

from __future__ import annotations

import logging

from app.main import _validate_fast_symbols_against_universe


def test_validate_fast_symbols_warns_on_missing_symbols(caplog) -> None:
    fast_symbols = ["xauusd", "eurusd"]
    allowed_pairs = {("xauusd", "1m")}

    with caplog.at_level(logging.WARNING, logger="app.main"):
        _validate_fast_symbols_against_universe(fast_symbols, allowed_pairs)

    records = [rec for rec in caplog.records if "SMC_UNIVERSE" in rec.msg]
    assert any("eurusd" in rec.getMessage() for rec in records)


def test_validate_fast_symbols_legacy_mode_info(caplog) -> None:
    fast_symbols = ["xauusd"]
    allowed_pairs = None

    with caplog.at_level(logging.INFO, logger="app.main"):
        _validate_fast_symbols_against_universe(fast_symbols, allowed_pairs)

    records = [rec for rec in caplog.records if "SMC_UNIVERSE" in rec.msg]
    assert any("legacy mode" in rec.getMessage() for rec in records)
    assert not any("відсутня у fxcm_contract" in rec.getMessage() for rec in records)
