"""Тести прозорого вибору env-файлу.

Вимога:
- Має бути один перемикач профілю: `AI_ONE_ENV_FILE`.
- Джерела (за пріоритетом):
  1) process-ENV
  2) dispatcher `.env`
- Жодної додаткової евристики через `AI_ONE_MODE`.
"""

from __future__ import annotations

from pathlib import Path

from app.env import select_env_file


def test_select_env_file_prefers_process_env_over_dispatcher(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path

    (project_root / ".env").write_text("AI_ONE_ENV_FILE=.env.local\n", encoding="utf-8")
    (project_root / ".env.local").write_text("AI_ONE_MODE=local\n", encoding="utf-8")

    monkeypatch.setenv("AI_ONE_ENV_FILE", ".env.prod")
    assert select_env_file(project_root) == project_root / ".env.prod"


def test_select_env_file_uses_dispatcher_env_when_process_env_missing(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path

    (project_root / ".env").write_text("AI_ONE_ENV_FILE=.env.local\n", encoding="utf-8")
    (project_root / ".env.local").write_text("AI_ONE_MODE=local\n", encoding="utf-8")

    monkeypatch.delenv("AI_ONE_ENV_FILE", raising=False)
    assert select_env_file(project_root) == project_root / ".env.local"


def test_select_env_file_falls_back_to_dotenv(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path

    monkeypatch.delenv("AI_ONE_ENV_FILE", raising=False)
    assert select_env_file(project_root) == project_root / ".env"
