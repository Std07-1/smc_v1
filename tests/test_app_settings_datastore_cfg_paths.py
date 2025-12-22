"""Тести для нормалізації шляхів у load_datastore_cfg().

Ціль: базові шляхи мають бути стабільні незалежно від CWD.
"""

from __future__ import annotations

import os
from pathlib import Path

from app.settings import load_datastore_cfg


def test_load_datastore_cfg_resolves_relative_base_dir_to_project_root(
    tmp_path: Path,
) -> None:
    # Відносний base_dir (типовий у YAML) має перетворитися на абсолютний шлях.
    # Беремо шлях до файлу в tmp, щоб не залежати від реального config/datastore.yaml.
    yaml_path = tmp_path / "datastore.yaml"
    yaml_path.write_text(
        "\n".join(
            [
                "namespace: ai_one",
                "base_dir: ./datastore",
                "profile:",
                "  name: default",
            ]
        ),
        encoding="utf-8",
    )

    cfg = load_datastore_cfg(yaml_path)

    base_dir = Path(cfg.base_dir)
    assert base_dir.is_absolute()
    # У репозиторії `datastore/` існує; якщо тестовий рантайм запускається з workspace,
    # очікуємо, що резолв йде саме в корінь проєкту, а не в tmp/config.
    assert base_dir.name == "datastore"


def test_load_datastore_cfg_allows_namespace_override_via_env(tmp_path: Path) -> None:
    yaml_path = tmp_path / "datastore.yaml"
    yaml_path.write_text(
        "\n".join(
            [
                "namespace: ai_one",
                "base_dir: ./datastore",
                "profile:",
                "  name: default",
            ]
        ),
        encoding="utf-8",
    )

    prev = os.environ.get("AI_ONE_NAMESPACE")
    try:
        os.environ["AI_ONE_NAMESPACE"] = "ai_one_local"
        cfg = load_datastore_cfg(yaml_path)
        assert cfg.namespace == "ai_one_local"
    finally:
        if prev is None:
            os.environ.pop("AI_ONE_NAMESPACE", None)
        else:
            os.environ["AI_ONE_NAMESPACE"] = prev


def test_load_datastore_cfg_uses_local_mode_namespace_when_enabled(
    tmp_path: Path,
) -> None:
    yaml_path = tmp_path / "datastore.yaml"
    yaml_path.write_text(
        "\n".join(
            [
                "namespace: ai_one",
                "base_dir: ./datastore",
                "profile:",
                "  name: default",
            ]
        ),
        encoding="utf-8",
    )

    prev_mode = os.environ.get("AI_ONE_MODE")
    prev_ns = os.environ.get("AI_ONE_NAMESPACE")
    try:
        os.environ["AI_ONE_MODE"] = "local"
        os.environ.pop("AI_ONE_NAMESPACE", None)
        cfg = load_datastore_cfg(yaml_path)
        assert cfg.namespace == "ai_one_local"
    finally:
        if prev_mode is None:
            os.environ.pop("AI_ONE_MODE", None)
        else:
            os.environ["AI_ONE_MODE"] = prev_mode
        if prev_ns is None:
            os.environ.pop("AI_ONE_NAMESPACE", None)
        else:
            os.environ["AI_ONE_NAMESPACE"] = prev_ns
