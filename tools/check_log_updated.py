"""Рейка: `Log.md` має оновлюватись разом зі змінами в системі.

Цей скрипт призначений для запуску як pre-commit hook і/або вручну.
Політика:
- Якщо у git diff є зміни у «системних» файлах (код/конфіги/тести),
  то `Log.md` має бути в переліку змінених файлів.
- Артефакти/дані (reports/, datastore/, tmp/ тощо) не вимагають Log.md.

Увага:
- Скрипт використовує `git diff` лише для читання (ніяких commit/push).
- Повідомлення українською (процесна вимога репо).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_IGNORE_PREFIXES: tuple[str, ...] = (
    "reports/",
    "datastore/",
    "tmp/",
    "**/__pycache__/",  # лише як підказка, фактична перевірка нижче
    ".pytest_cache/",
)

# «Системні» каталоги/файли, зміни в яких мають супроводжуватись Log.md.
_WATCH_PREFIXES: tuple[str, ...] = (
    "app/",
    "config/",
    "core/",
    "data/",
    "smc_core/",
    "smc_execution/",
    "smc_liquidity/",
    "smc_structure/",
    "smc_zones/",
    "UI/",
    "UI_v2/",
    "tests/",
    "tools/",
)

_WATCH_ROOT_FILES: tuple[str, ...] = (
    "requirements.txt",
    "requirements-dev.txt",
    "pytest.ini",
    "mypy.ini",
    "ruff.toml",
    "runtime.txt",
    "Procfile",
    "mcp_config.json",
)


def _norm_rel(path: str) -> str:
    return path.replace("\\", "/").lstrip("/")


def _find_repo_root(start: Path) -> Path | None:
    cur = start
    while True:
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent


def _run_git(repo_root: Path, args: list[str]) -> list[str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        raise RuntimeError(f"git {' '.join(args)}: {err or 'невідома помилка'}")
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def _collect_changed_files(repo_root: Path) -> list[str]:
    changed: set[str] = set()

    for args in (
        ["diff", "--name-only"],
        ["diff", "--name-only", "--cached"],
    ):
        for p in _run_git(repo_root, args):
            changed.add(_norm_rel(p))

    return sorted(changed)


def _is_ignored(path: str) -> bool:
    p = _norm_rel(path)
    if "/__pycache__/" in f"/{p}/":
        return True
    if p.startswith(".pytest_cache/"):
        return True
    for pref in _IGNORE_PREFIXES:
        pref_n = _norm_rel(pref).replace("**/", "")
        if pref_n and p.startswith(pref_n):
            return True
    return False


def _is_watched(path: str) -> bool:
    p = _norm_rel(path)
    if p == "Log.md":
        return False
    if _is_ignored(p):
        return False

    if p in _WATCH_ROOT_FILES:
        return True

    return any(p.startswith(pref) for pref in _WATCH_PREFIXES)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Рейка: заборонити зміни системи без оновлення Log.md"
    )
    parser.add_argument(
        "--repo",
        default=None,
        help="Шлях до repo (за замовчуванням: auto-detect від CWD)",
    )
    parser.add_argument(
        "--print-changed",
        action="store_true",
        help="Надрукувати список змінених файлів (діагностика)",
    )

    args = parser.parse_args(argv)

    start = Path(args.repo).resolve() if args.repo else Path(os.getcwd()).resolve()
    repo_root = _find_repo_root(start)
    if repo_root is None:
        sys.stderr.write(
            "[LOG-GATE] Не знайдено .git — неможливо перевірити рейку Log.md.\n"
            "[LOG-GATE] Рекомендація: запускати перевірку всередині git-репозиторію.\n"
        )
        return 3

    try:
        changed = _collect_changed_files(repo_root)
    except Exception as exc:
        sys.stderr.write(f"[LOG-GATE] Помилка під час читання git diff: {exc}\n")
        return 3

    if args.print_changed:
        for p in changed:
            print(p)

    if not changed:
        return 0

    has_log = "Log.md" in changed
    watched_changed = [p for p in changed if _is_watched(p)]

    if watched_changed and not has_log:
        sys.stderr.write(
            "\n[LOG-GATE] ПОМИЛКА: Зміни в системі без оновлення Log.md\n"
            "[LOG-GATE] Додай короткий запис у Log.md (що/де/чому/перевірки/ризики).\n\n"
            "[LOG-GATE] Виявлені системні зміни:\n"
        )
        for p in watched_changed[:120]:
            sys.stderr.write(f"  - {p}\n")
        if len(watched_changed) > 120:
            sys.stderr.write(f"  ... і ще {len(watched_changed) - 120} файлів\n")
        sys.stderr.write("\n")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
