"""Вибір env-файлу для запуску.

Політика (без двозначностей):
- Є **один** перемикач профілю: `AI_ONE_ENV_FILE`.
- Його можна задати:
    1) у process-ENV (найвищий пріоритет)
    2) у dispatcher-файлі `.env` (рядок `AI_ONE_ENV_FILE=.env.local|.env.prod`)

Усі інші налаштування (namespace, FXCM канали, порти тощо) живуть у вибраному
профільному env-файлі (`.env.local`/`.env.prod`).

Ціль: уникнути евристик на кшталт "або `.env.local`, або `.env.prod`" через
`AI_ONE_MODE`, які плутають запуск і роблять поведінку непрозорою.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class EnvFileSelection:
    """Результат вибору env-файлу.

    source:
    - process_env: `AI_ONE_ENV_FILE` заданий у process-ENV
    - dispatcher_env: `AI_ONE_ENV_FILE` взято з dispatcher `.env`
    - fallback: дефолт `.env`
    """

    path: Path
    source: str
    exists: bool
    ref: str | None = None


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


def select_env_file_with_trace(project_root: Path) -> EnvFileSelection:
    """Повертає вибраний env-файл разом із трасою рішення."""

    # 1) Явний override через process-ENV
    override = os.getenv("AI_ONE_ENV_FILE")
    if override:
        candidate = Path(str(override)).expanduser()
        if not candidate.is_absolute():
            candidate = project_root / candidate
        return EnvFileSelection(
            path=candidate,
            source="process_env",
            exists=candidate.exists(),
            ref=str(override),
        )

    # 2) Dispatcher `.env`: AI_ONE_ENV_FILE=.env.local/.env.prod
    dispatch = _read_env_dispatch(project_root)
    dispatch_file = dispatch.get("AI_ONE_ENV_FILE")
    if dispatch_file:
        candidate = Path(str(dispatch_file)).expanduser()
        if not candidate.is_absolute():
            candidate = project_root / candidate
        return EnvFileSelection(
            path=candidate,
            source="dispatcher_env",
            exists=candidate.exists(),
            ref=str(dispatch_file),
        )

    # 3) Фолбек лише на `.env` (без евристик за режимом)
    candidate = project_root / ".env"
    return EnvFileSelection(
        path=candidate,
        source="fallback",
        exists=candidate.exists(),
        ref=None,
    )


def select_env_file(project_root: Path) -> Path:
    """Повертає шлях до env-файлу для поточного запуску."""

    return select_env_file_with_trace(project_root).path
