"""Тести для спільного Rich Console.

Мета: гарантувати, що пайплайн використовує один і той самий Console для логів
і Live status bar, щоб уникати артефактів у терміналі.
"""

from __future__ import annotations

from utils.rich_console import get_rich_console


def test_get_rich_console_is_singleton() -> None:
    c1 = get_rich_console()
    c2 = get_rich_console()
    assert c1 is c2
