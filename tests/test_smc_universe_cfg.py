"""Тести валідації SMC contract-of-needs (smc_universe)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.settings import DataStoreCfg


def test_valid_universe_single_symbol() -> None:
    data = {
        "smc_universe": {
            "fxcm_contract": {
                "version": 1,
                "symbols": [
                    {
                        "id": "XAUUSD",
                        "tfs": ["1m", "5m", "1h", "4h"],
                        "min_history_bars": 2000,
                        "enabled": True,
                    }
                ],
            }
        }
    }

    cfg = DataStoreCfg(**data)  # type: ignore

    fxcm = cfg.smc_universe.fxcm_contract
    assert fxcm is not None
    assert fxcm.version == 1
    assert len(fxcm.symbols) == 1

    symbol = fxcm.symbols[0]
    assert symbol.id == "xauusd"  # нормалізація до lower
    assert symbol.tfs == ["1m", "5m", "1h", "4h"]
    assert symbol.min_history_bars == 2000
    assert symbol.enabled is True


def test_invalid_tf_raises_error() -> None:
    data = {
        "smc_universe": {
            "fxcm_contract": {
                "symbols": [
                    {
                        "id": "xauusd",
                        "tfs": ["1m", "15m"],
                        "min_history_bars": 500,
                    }
                ]
            }
        }
    }

    with pytest.raises(ValidationError):
        DataStoreCfg(**data)  # type: ignore


def test_duplicate_symbol_tf_rejected() -> None:
    data = {
        "smc_universe": {
            "fxcm_contract": {
                "symbols": [
                    {
                        "id": "xauusd",
                        "tfs": ["1m"],
                        "min_history_bars": 500,
                    },
                    {
                        "id": "XAUUSD",
                        "tfs": ["1m", "5m"],
                        "min_history_bars": 700,
                    },
                ]
            }
        }
    }

    with pytest.raises(ValidationError):
        DataStoreCfg(**data)  # type: ignore


def test_missing_universe_defaults_to_empty() -> None:
    cfg = DataStoreCfg()
    assert (
        cfg.smc_universe.fxcm_contract is None
        or not cfg.smc_universe.fxcm_contract.symbols
    )
