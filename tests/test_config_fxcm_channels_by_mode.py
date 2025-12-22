"""Тести профілювання FXCM каналів.

FXCM конектор живе в окремому репо, тому в цьому проєкті дефолтні канали
вважаються канонічними `fxcm:*` незалежно від `AI_ONE_MODE`.

Ізоляція локального/dev конектора робиться явним `FXCM_CHANNEL_PREFIX`.
"""

from __future__ import annotations

import importlib


def _reload_config():
    import config.config as cfg

    return importlib.reload(cfg)


def test_fxcm_channels_default_to_fxcm_prefix_in_local_mode(monkeypatch) -> None:
    monkeypatch.setenv("AI_ONE_MODE", "local")
    monkeypatch.delenv("FXCM_CHANNEL_PREFIX", raising=False)
    monkeypatch.delenv("FXCM_OHLCV_CHANNEL", raising=False)
    monkeypatch.delenv("FXCM_PRICE_TICK_CHANNEL", raising=False)
    monkeypatch.delenv("FXCM_STATUS_CHANNEL", raising=False)
    monkeypatch.delenv("FXCM_COMMANDS_CHANNEL", raising=False)

    cfg = _reload_config()

    assert cfg.FXCM_OHLCV_CHANNEL == "fxcm:ohlcv"
    assert cfg.FXCM_PRICE_TICK_CHANNEL == "fxcm:price_tik"
    assert cfg.FXCM_STATUS_CHANNEL == "fxcm:status"
    assert cfg.FXCM_COMMANDS_CHANNEL == "fxcm:commands"


def test_fxcm_channels_default_to_prod_prefix(monkeypatch) -> None:
    monkeypatch.setenv("AI_ONE_MODE", "prod")
    monkeypatch.delenv("FXCM_CHANNEL_PREFIX", raising=False)
    monkeypatch.delenv("FXCM_OHLCV_CHANNEL", raising=False)
    monkeypatch.delenv("FXCM_PRICE_TICK_CHANNEL", raising=False)
    monkeypatch.delenv("FXCM_STATUS_CHANNEL", raising=False)
    monkeypatch.delenv("FXCM_COMMANDS_CHANNEL", raising=False)

    cfg = _reload_config()

    assert cfg.FXCM_OHLCV_CHANNEL == "fxcm:ohlcv"
    assert cfg.FXCM_PRICE_TICK_CHANNEL == "fxcm:price_tik"
    assert cfg.FXCM_STATUS_CHANNEL == "fxcm:status"
    assert cfg.FXCM_COMMANDS_CHANNEL == "fxcm:commands"


def test_fxcm_channel_prefix_override(monkeypatch) -> None:
    monkeypatch.setenv("AI_ONE_MODE", "local")
    monkeypatch.setenv("FXCM_CHANNEL_PREFIX", "fxcm_dev")
    monkeypatch.delenv("FXCM_OHLCV_CHANNEL", raising=False)
    monkeypatch.delenv("FXCM_PRICE_TICK_CHANNEL", raising=False)
    monkeypatch.delenv("FXCM_STATUS_CHANNEL", raising=False)
    monkeypatch.delenv("FXCM_COMMANDS_CHANNEL", raising=False)

    cfg = _reload_config()
    assert cfg.FXCM_OHLCV_CHANNEL == "fxcm_dev:ohlcv"
    assert cfg.FXCM_PRICE_TICK_CHANNEL == "fxcm_dev:price_tik"
    assert cfg.FXCM_STATUS_CHANNEL == "fxcm_dev:status"
    assert cfg.FXCM_COMMANDS_CHANNEL == "fxcm_dev:commands"


def test_fxcm_channel_explicit_override_wins(monkeypatch) -> None:
    monkeypatch.setenv("AI_ONE_MODE", "local")
    monkeypatch.setenv("FXCM_OHLCV_CHANNEL", "fxcm_custom:ohlcv")

    cfg = _reload_config()
    assert cfg.FXCM_OHLCV_CHANNEL == "fxcm_custom:ohlcv"
