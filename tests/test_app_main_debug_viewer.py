"""Тести для запуску debug viewer в окремому процесі."""

from app.main import _build_debug_viewer_popen_kwargs


def test_build_debug_viewer_kwargs_posix() -> None:
    """На POSIX маємо стартувати в окремій сесії без creationflags."""

    kwargs = _build_debug_viewer_popen_kwargs(platform="posix", creation_flag=0)

    assert kwargs["stdout"] is None
    assert kwargs["stderr"] is None
    assert kwargs["stdin"] is None
    assert kwargs["start_new_session"] is True
    assert "creationflags" not in kwargs


def test_build_debug_viewer_kwargs_windows() -> None:
    """На Windows обов'язково додаємо CREATE_NEW_CONSOLE."""

    creation_flag = 0x08000000
    kwargs = _build_debug_viewer_popen_kwargs(
        platform="nt", creation_flag=creation_flag
    )

    assert kwargs.get("creationflags") == creation_flag
    assert "start_new_session" not in kwargs
