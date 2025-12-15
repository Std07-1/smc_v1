"""Тести для гейтінгу історії в smc_producer.

Фокус: stale_tail у вихідні/поза сесією не має блокувати UI.
"""

from __future__ import annotations

from app.smc_producer import _history_ok_for_compute


def test_history_ok_accepts_ok_always() -> None:
    assert _history_ok_for_compute(history_state="ok", allow_stale_tail=False) is True
    assert _history_ok_for_compute(history_state="ok", allow_stale_tail=True) is True


def test_history_ok_rejects_stale_tail_when_not_allowed() -> None:
    assert (
        _history_ok_for_compute(history_state="stale_tail", allow_stale_tail=False)
        is False
    )


def test_history_ok_accepts_stale_tail_when_allowed() -> None:
    assert (
        _history_ok_for_compute(history_state="stale_tail", allow_stale_tail=True)
        is True
    )
