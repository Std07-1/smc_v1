"""Тести для SSOT-хелперів часу/серіалізації у core.serialization.

Фіксуємо контракт форматів, щоб випадково не повернути `Z` там, де потрібен
людський рядок, і щоб iso-offset лишався стабільним.
"""

from __future__ import annotations

import math

from core.serialization import (
    coerce_dict,
    duration_ms_to_hms,
    duration_seconds_to_hms,
    safe_float,
    safe_int,
    try_iso_to_human_utc,
    utc_ms_to_human_utc,
    utc_seconds_to_human_utc,
)


def test_utc_seconds_to_human_utc_no_z() -> None:
    assert utc_seconds_to_human_utc(0.0) == "1970-01-01 00:00:00"


def test_utc_ms_to_human_utc_no_z() -> None:
    assert utc_ms_to_human_utc(0) == "1970-01-01 00:00:00"


def test_try_iso_to_human_utc_parses_z_and_offset() -> None:
    assert try_iso_to_human_utc("1970-01-01T00:00:00Z") == "1970-01-01 00:00:00"
    assert try_iso_to_human_utc("1970-01-01T00:00:00+00:00") == "1970-01-01 00:00:00"
    assert try_iso_to_human_utc("not-a-date") is None


def test_duration_seconds_to_hms_formats_hms_and_days() -> None:
    assert duration_seconds_to_hms(0) == "00:00:00"
    assert duration_seconds_to_hms(5) == "00:00:05"
    assert duration_seconds_to_hms(3661) == "01:01:01"
    assert duration_seconds_to_hms(86_400) == "01 00:00:00"
    assert duration_seconds_to_hms(-1) == "-"


def test_duration_ms_to_hms_formats_hms() -> None:
    assert duration_ms_to_hms(0) == "00:00:00"
    assert duration_ms_to_hms(5_000) == "00:00:05"
    assert duration_ms_to_hms(-1) == "-"


def test_safe_helpers_and_coerce_dict() -> None:
    assert safe_int(None) is None
    assert safe_int("7") == 7

    assert safe_float(None) is None
    assert safe_float("1.5") == 1.5
    assert safe_float(float("inf"), finite=True) is None
    assert safe_float(float("nan"), finite=True) is None
    assert safe_float(float("nan"), finite=False) is not None
    assert math.isnan(safe_float(float("nan"), finite=False) or 0.0)

    sample = {"a": 1}
    assert coerce_dict(sample) is sample
    assert coerce_dict(None) == {}
