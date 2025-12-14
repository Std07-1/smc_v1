"""Сумісність імпорту для shared Rich Console.

Канонічна реалізація singleton Console знаходиться в `utils.rich_console`.
Цей модуль лишається як тонкий shim, щоб не ламати старі імпорти.
"""

from __future__ import annotations

from utils.rich_console import get_rich_console

__all__ = ("get_rich_console",)
