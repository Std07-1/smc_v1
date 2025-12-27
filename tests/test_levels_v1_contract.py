"""Тести для Levels-V1 контрактних визначень (Крок 3.1).

Це механічні тести (гейт):
- pool_type -> label дає тільки EQH/EQL,
- whitelist labels не пропускає зайвого,
- генерація id детермінована і стабільна.
"""

from __future__ import annotations

from core.contracts.levels_v1 import (
    LEVEL_LABELS_BAND_V1,
    LEVEL_LABELS_LINE_V1,
    is_allowed_level_label_v1,
    make_level_id_band_v1,
    make_level_id_line_v1,
    normalize_pool_type_to_level_label_v1,
    round_price_for_level_id,
)


def test_pool_type_maps_only_to_eqh_eql() -> None:
    assert normalize_pool_type_to_level_label_v1("EQH") == "EQH"
    assert normalize_pool_type_to_level_label_v1("eqh_p") == "EQH"
    assert normalize_pool_type_to_level_label_v1("EQL") == "EQL"
    assert normalize_pool_type_to_level_label_v1("EQL_P") == "EQL"

    assert normalize_pool_type_to_level_label_v1("WICK_CLUSTER") is None
    assert normalize_pool_type_to_level_label_v1("SLQ") is None
    assert normalize_pool_type_to_level_label_v1("RANGE_EXTREME") is None
    assert normalize_pool_type_to_level_label_v1("SESSION_LOW") is None


def test_whitelist_labels_only_allows_defined_sets() -> None:
    for lab in LEVEL_LABELS_LINE_V1:
        assert is_allowed_level_label_v1(lab)
    for lab in LEVEL_LABELS_BAND_V1:
        assert is_allowed_level_label_v1(lab)

    assert not is_allowed_level_label_v1("WICK_CLUSTER")
    assert not is_allowed_level_label_v1("SLQ")
    assert not is_allowed_level_label_v1("RANGE_EXTREME")
    assert not is_allowed_level_label_v1("")
    assert not is_allowed_level_label_v1(None)


def test_level_id_is_deterministic() -> None:
    a = make_level_id_line_v1(tf="5m", label="PDH", price=4533.8449, symbol="XAUUSD")
    b = make_level_id_line_v1(tf="5m", label="PDH", price=4533.8449, symbol="XAUUSD")
    assert a == b

    c = make_level_id_band_v1(
        tf="1h", label="EQH", bot=4513.401, top=4513.409, symbol="XAUUSD"
    )
    d = make_level_id_band_v1(
        tf="1h", label="EQH", bot=4513.401, top=4513.409, symbol="XAUUSD"
    )
    assert c == d


def test_rounding_prefers_tick_size_when_present() -> None:
    # tick_size=0.25 => 100.12 -> 100.0; 100.13 -> 100.25
    assert round_price_for_level_id(100.12, tick_size=0.25, symbol="XAUUSD") == 100.0
    assert round_price_for_level_id(100.13, tick_size=0.25, symbol="XAUUSD") == 100.25


def test_public_api_exports_exist() -> None:
    # Public API boundary: імпортуємо з core.contracts, а не з внутрішніх модулів.
    from core.contracts import (  # noqa: WPS433 (локальний імпорт у тесті)
        LEVEL_LABELS_V1,
        LevelSource,
        LevelTfV1,
        is_allowed_level_label_v1,
        make_level_id_line_v1,
        normalize_pool_type_to_level_label_v1,
    )

    assert "PDH" in LEVEL_LABELS_V1
    assert LevelSource
    assert LevelTfV1
    assert is_allowed_level_label_v1("PDH")
    assert make_level_id_line_v1(tf="5m", label="PDH", price=1.23, symbol="XAUUSD")
    assert normalize_pool_type_to_level_label_v1("EQH_P") == "EQH"
