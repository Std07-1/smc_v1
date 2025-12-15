"""Сумісність імпорту для старого `get_rich_console`.

Rich-консоль у цьому репозиторії більше не використовується для логування.
Цей модуль лишається як lightweight shim, щоб не ламати імпорти.
"""

from __future__ import annotations

from typing import Any


class _PlainConsole:
    def print(self, *args: Any, **kwargs: Any) -> None:
        print(*args, **kwargs)


_CONSOLE = _PlainConsole()


def get_rich_console() -> Any:
    return _CONSOLE


__all__ = ("get_rich_console",)
