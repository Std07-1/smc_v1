"""Тести для допоміжних ENV-парсерів у `app.main`."""

from __future__ import annotations

from app.main import _env_int


def test__env_int_returns_default_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("SMC_VIEWER_HTTP_PORT", raising=False)
    assert _env_int("SMC_VIEWER_HTTP_PORT", 8080) == 8080


def test__env_int_returns_default_when_empty(monkeypatch) -> None:
    monkeypatch.setenv("SMC_VIEWER_HTTP_PORT", "")
    assert _env_int("SMC_VIEWER_HTTP_PORT", 8080) == 8080


def test__env_int_returns_default_when_invalid(monkeypatch) -> None:
    monkeypatch.setenv("SMC_VIEWER_HTTP_PORT", "abc")
    assert _env_int("SMC_VIEWER_HTTP_PORT", 8080) == 8080


def test__env_int_parses_int(monkeypatch) -> None:
    monkeypatch.setenv("SMC_VIEWER_HTTP_PORT", " 18080 ")
    assert _env_int("SMC_VIEWER_HTTP_PORT", 8080) == 18080
