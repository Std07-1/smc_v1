"""Юніт-тести інструмента перевірки TF coverage (без I/O)."""

from __future__ import annotations

from tools.tf_coverage_report import compute_tf_coverage


def test_compute_tf_coverage_no_gaps() -> None:
    tf_ms = 60_000
    # 10 барів з кроком 1m
    open_times = [1_000_000 + i * tf_ms for i in range(10)]
    cov = compute_tf_coverage(open_times, tf_ms=tf_ms, window_ms=9 * tf_ms)

    assert cov.has_data is True
    assert cov.gaps == 0
    assert cov.missing_bars == 0
    assert cov.bars == 10


def test_compute_tf_coverage_with_gap() -> None:
    tf_ms = 300_000
    base = 2_000_000
    # 5m: пропускаємо один бар посередині
    open_times = [
        base + 0 * tf_ms,
        base + 1 * tf_ms,
        base + 3 * tf_ms,
        base + 4 * tf_ms,
    ]
    cov = compute_tf_coverage(open_times, tf_ms=tf_ms, window_ms=10 * tf_ms)

    assert cov.has_data is True
    assert cov.gaps >= 1
    assert cov.missing_bars >= 1
    assert cov.offgrid == 0


def test_compute_tf_coverage_offgrid_counts() -> None:
    tf_ms = 60_000
    base = 3_000_000
    # Другий бар зсунутий на 10s -> offgrid
    open_times = [base, base + tf_ms + 10_000, base + 2 * tf_ms + 10_000]
    cov = compute_tf_coverage(open_times, tf_ms=tf_ms, window_ms=10 * tf_ms)

    assert cov.has_data is True
    assert cov.offgrid >= 1
