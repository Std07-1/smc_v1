"""Вибір env-файлу для запуску.

Мета:
- мати 2 профілі (.env.local / .env.prod) і перемикати їх одним `AI_ONE_MODE`;
- не парсити випадковий `.env`, якщо він містить не-ENV дані (наприклад PEM-блоки),
  що ламають python-dotenv.

Пріоритети:
1) `AI_ONE_ENV_FILE` (явний шлях)
2) за `AI_ONE_MODE`: `.env.local` або `.env.prod`
3) якщо відповідний файл відсутній: fallback на `.env.*.example`
4) останній fallback: `.env`
"""

from __future__ import annotations

import os
from pathlib import Path


def _read_env_dispatch(project_root: Path) -> dict[str, str]:
    """Дуже простий парсер `.env` тільки для dispatcher-ключів.

    Навмисно не використовуємо python-dotenv тут, щоб не ламатися на випадкових
    шматках не-ENV (наприклад PEM), якщо хтось їх покладе у `.env`.
    """

    env_path = project_root / ".env"
    if not env_path.exists():
        return {}

    result: dict[str, str] = {}
    try:
        text = env_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        result[key] = value
    return result


def select_env_file(project_root: Path) -> Path:
    """Повертає шлях до env-файлу для поточного запуску."""

    # 1) Явний override через процес-ENV
    override = os.getenv("AI_ONE_ENV_FILE")
    if override:
        return Path(override).expanduser()

    # 2) Один перемикач через `.env` (dispatcher): AI_ONE_ENV_FILE=.env.local/.env.prod
    dispatch = _read_env_dispatch(project_root)
    dispatch_file = dispatch.get("AI_ONE_ENV_FILE")
    if dispatch_file:
        candidate = Path(dispatch_file).expanduser()
        if not candidate.is_absolute():
            candidate = project_root / candidate
        return candidate

    raw_mode = os.getenv("AI_ONE_MODE")
    if raw_mode is None:
        # Без явного режиму орієнтуємось на наявність файлів:
        # - на VPS очікуємо `.env.prod`
        # - локально частіше є `.env.local` або `.env.local.example`
        preferred_prod = project_root / ".env.prod"
        if preferred_prod.exists():
            return preferred_prod
        preferred_local = project_root / ".env.local"
        if preferred_local.exists():
            return preferred_local
        preferred_default = project_root / ".env"
        if preferred_default.exists():
            return preferred_default
        preferred_local_example = project_root / ".env.local.example"
        if preferred_local_example.exists():
            return preferred_local_example
        return preferred_default

    mode = str(raw_mode).strip().lower()
    if mode in {"dev"}:
        mode = "local"
    if mode not in {"local", "prod"}:
        mode = "prod"

    preferred = project_root / (".env.local" if mode == "local" else ".env.prod")
    if preferred.exists():
        return preferred

    preferred_example = project_root / (
        ".env.local.example" if mode == "local" else ".env.prod.example"
    )
    if preferred_example.exists():
        return preferred_example

    return project_root / ".env"
