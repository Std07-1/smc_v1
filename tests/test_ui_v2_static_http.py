"""Тести для роздачі статичного UI через ViewerStateHttpServer."""

from __future__ import annotations

from pathlib import Path

from UI_v2.viewer_state_server import ViewerStateHttpServer


class _FakeStore:
    async def get_all_states(self):  # pragma: no cover
        return {}

    async def get_state(self, symbol: str):  # pragma: no cover
        return None


def _extract_header(response: bytes, name: str) -> str | None:
    head = response.split(b"\r\n\r\n", 1)[0].decode("ascii", errors="replace")
    for line in head.split("\r\n"):
        if line.lower().startswith(name.lower() + ":"):
            return line.split(":", 1)[1].strip()
    return None


def test_static_root_serves_index_html(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    server = ViewerStateHttpServer(store=_FakeStore(), web_root=tmp_path)  # type: ignore[arg-type]

    result = server._try_handle_static("/")

    assert result is not None
    response, status = result
    assert status == 200
    assert response.startswith(b"HTTP/1.1 200 OK")
    assert _extract_header(response, "Content-Type") == "text/html; charset=utf-8"
    assert response.endswith(b"<html>ok</html>")


def test_static_serves_js_with_content_type(tmp_path: Path) -> None:
    (tmp_path / "app.js").write_text("console.log('ok');", encoding="utf-8")
    server = ViewerStateHttpServer(store=_FakeStore(), web_root=tmp_path)  # type: ignore[arg-type]

    result = server._try_handle_static("/app.js")

    assert result is not None
    response, status = result
    assert status == 200
    content_type = _extract_header(response, "Content-Type")
    assert content_type is not None
    assert content_type in {
        "text/javascript; charset=utf-8",
        "application/javascript",
        "application/javascript; charset=utf-8",
    }


def test_static_blocks_path_traversal(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("ok", encoding="utf-8")
    server = ViewerStateHttpServer(store=_FakeStore(), web_root=tmp_path)  # type: ignore[arg-type]

    result = server._try_handle_static("/../secrets.txt")

    assert result is not None
    _response, status = result
    assert status == 404
