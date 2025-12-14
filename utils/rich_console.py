"""Спільний Rich Console для всього пайплайна.

Критично: Rich Live (status bar) і RichHandler (логи) мають писати в ОДИН Console,
інакше у PowerShell/VS Code можливі артефакти: дублювання панелей, "затирання"
або обрізання логів під час refresh.
"""

from __future__ import annotations

from rich.console import Console

_RICH_CONSOLE: Console | None = None


def get_rich_console() -> Console:
    """Повертає singleton Console(stderr=True) для Rich.

    `force_terminal=True` використовується для стабільної роботи Rich Live у VS Code.
    """

    global _RICH_CONSOLE
    if _RICH_CONSOLE is not None:
        return _RICH_CONSOLE

    _RICH_CONSOLE = Console(
        stderr=True,
        force_terminal=True,
        color_system="standard",
    )
    return _RICH_CONSOLE


__all__ = ("get_rich_console",)
