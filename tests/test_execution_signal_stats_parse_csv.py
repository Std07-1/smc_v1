"""Тести парсерів CSV для grid-режиму QA статистики."""

from __future__ import annotations

from tools.qa_execution_signal_stats import _parse_csv_floats, _parse_csv_ints


def test_parse_csv_ints_trims_and_skips_empty() -> None:
    assert _parse_csv_ints(" 15, 30, , 60 ") == [15, 30, 60]


def test_parse_csv_floats_trims_and_skips_empty() -> None:
    assert _parse_csv_floats("0.5, 1.0, ,1.5") == [0.5, 1.0, 1.5]
