"""Юніт-тести для утиліти fxcm_warmup."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tools import fxcm_warmup as fxw


def test_sanitize_symbols_filters_noise() -> None:
    """Перевіряємо, що службові значення/пробіли відкидаються."""

    raw: list[Any] = [" XAUUSD ", "", "eurusd", None, " GBPUSD"]
    assert fxw._sanitize_symbols(raw) == ["xauusd", "eurusd", "gbpusd"]  # type: ignore[arg-type]


def test_history_window_respects_last_open(monkeypatch) -> None:
    """Вікно warmup обмежується останнім open_time та не виходить у майбутнє."""

    fixed_now = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)

    class FixedDatetime(datetime):  # type: ignore[misc]
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return fixed_now if tz else datetime.now()

    monkeypatch.setattr(fxw, "datetime", FixedDatetime)
    interval_ms = 60_000
    number = 10
    last_open = int(datetime(2025, 1, 1, 11, 58, tzinfo=UTC).timestamp() * 1000)

    start_dt, end_dt = fxw._history_window(last_open, number, interval_ms)
    assert end_dt == fixed_now
    assert start_dt < end_dt
    assert (end_dt - start_dt).total_seconds() <= number * 60
