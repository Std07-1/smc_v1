"""Тести для профілю запуску AI_ONE_MODE (prod/local) у config.config.

Важливо: `config.config` обчислює NAMESPACE на імпорті, тому в тесті
використовуємо importlib.reload.
"""

from __future__ import annotations

import importlib
import os


def _reload_config():
    import config.config as cfg

    return importlib.reload(cfg)


def test_ai_one_mode_local_sets_default_namespace_when_env_namespace_missing() -> None:
    prev_mode = os.environ.get("AI_ONE_MODE")
    prev_ns = os.environ.get("AI_ONE_NAMESPACE")
    try:
        os.environ["AI_ONE_MODE"] = "local"
        os.environ.pop("AI_ONE_NAMESPACE", None)
        cfg = _reload_config()
        assert cfg.AI_ONE_MODE == "local"
        assert cfg.NAMESPACE == "ai_one_local"
    finally:
        if prev_mode is None:
            os.environ.pop("AI_ONE_MODE", None)
        else:
            os.environ["AI_ONE_MODE"] = prev_mode
        if prev_ns is None:
            os.environ.pop("AI_ONE_NAMESPACE", None)
        else:
            os.environ["AI_ONE_NAMESPACE"] = prev_ns
        _reload_config()


def test_ai_one_mode_prod_sets_default_namespace_when_env_namespace_missing() -> None:
    prev_mode = os.environ.get("AI_ONE_MODE")
    prev_ns = os.environ.get("AI_ONE_NAMESPACE")
    try:
        os.environ["AI_ONE_MODE"] = "prod"
        os.environ.pop("AI_ONE_NAMESPACE", None)
        cfg = _reload_config()
        assert cfg.AI_ONE_MODE == "prod"
        assert cfg.NAMESPACE == "ai_one_prod"
    finally:
        if prev_mode is None:
            os.environ.pop("AI_ONE_MODE", None)
        else:
            os.environ["AI_ONE_MODE"] = prev_mode
        if prev_ns is None:
            os.environ.pop("AI_ONE_NAMESPACE", None)
        else:
            os.environ["AI_ONE_NAMESPACE"] = prev_ns
        _reload_config()


def test_ai_one_namespace_env_override_wins_over_mode() -> None:
    prev_mode = os.environ.get("AI_ONE_MODE")
    prev_ns = os.environ.get("AI_ONE_NAMESPACE")
    try:
        os.environ["AI_ONE_MODE"] = "local"
        os.environ["AI_ONE_NAMESPACE"] = "ai_one_custom"
        cfg = _reload_config()
        assert cfg.AI_ONE_MODE == "local"
        assert cfg.NAMESPACE == "ai_one_custom"
    finally:
        if prev_mode is None:
            os.environ.pop("AI_ONE_MODE", None)
        else:
            os.environ["AI_ONE_MODE"] = prev_mode
        if prev_ns is None:
            os.environ.pop("AI_ONE_NAMESPACE", None)
        else:
            os.environ["AI_ONE_NAMESPACE"] = prev_ns
        _reload_config()
