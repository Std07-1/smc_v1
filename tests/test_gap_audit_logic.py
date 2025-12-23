"""Юніт-тести для логіки пошуку гепів у послідовності open_time.

Важливо: тут перевіряємо саме строгий критерій "крок рівно TF".
"""

from __future__ import annotations

from tools.gap_audit import analyze_open_times


def test_gap_audit_strict_ok_sequence() -> None:
    base = 1_700_000_000_000
    seq = [base + i * 60_000 for i in range(10)]
    rep = analyze_open_times(seq, expected_step_ms=60_000)
    assert rep.gaps == 0
    assert rep.non_monotonic == 0


def test_gap_audit_detects_missing_minute_gap() -> None:
    base = 1_700_000_000_000
    seq = [base + i * 60_000 for i in range(5)]
    # пропускаємо одну хвилину
    seq.append(base + 6 * 60_000)
    rep = analyze_open_times(seq, expected_step_ms=60_000)
    assert rep.gaps == 1
    assert rep.max_gap_ms == 2 * 60_000


def test_gap_audit_counts_duplicates_but_not_as_gap() -> None:
    base = 1_700_000_000_000
    seq = [base, base, base + 60_000, base + 120_000]
    rep = analyze_open_times(seq, expected_step_ms=60_000)
    assert rep.duplicates == 1
    assert rep.gaps == 0
    assert rep.non_monotonic == 0
