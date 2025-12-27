"""Тести для baseline harness (as-is) рівнів.

Ціль тестів:
- Детермінованість: однакові вхідні дані → однаковий geometry_hash.
- Стабільність хеша від порядку pools.

Це НЕ тестує якість правил відбору як таких — лише стабільність інструменту.
"""

from __future__ import annotations

from tools.levels_baseline_harness import (
    candidates_geometry_hash,
    candidates_items_from_viewer_state,
    geometry_hash,
    rendered_levels_items,
    select_pools_for_render_as_levels,
    shadow_items_from_viewer_state,
)


def test_geometry_hash_is_order_invariant_for_pools() -> None:
    # Мінімальні OHLCV для price window.
    ohlcv = [
        {"time": 1, "open": 100, "high": 101, "low": 99, "close": 100},
        {"time": 2, "open": 100, "high": 102, "low": 98, "close": 101},
        {"time": 3, "open": 101, "high": 103, "low": 99, "close": 102},
    ]

    pools_a = [
        {
            "price": 105.0,
            "type": "PDH",
            "role": "PRIMARY",
            "strength": 80,
            "touches": 3,
        },
        {"price": 95.0, "type": "PDL", "role": "PRIMARY", "strength": 70, "touches": 2},
        {
            "price": 110.0,
            "type": "EQH",
            "role": "COUNTER",
            "strength": 40,
            "touches": 1,
        },
        {"price": 90.0, "type": "EQL", "role": "COUNTER", "strength": 50, "touches": 2},
    ]
    pools_b = list(reversed(pools_a))

    sel_a = select_pools_for_render_as_levels(
        pools_a, ref_price=100.0, ohlcv_bars=ohlcv
    )
    sel_b = select_pools_for_render_as_levels(
        pools_b, ref_price=100.0, ohlcv_bars=ohlcv
    )

    items_a = rendered_levels_items(sel_a)
    items_b = rendered_levels_items(sel_b)

    assert geometry_hash(items_a) == geometry_hash(items_b)


def test_geometry_hash_changes_when_prices_change() -> None:
    ohlcv = [
        {"time": 1, "open": 100, "high": 101, "low": 99, "close": 100},
        {"time": 2, "open": 100, "high": 102, "low": 98, "close": 101},
        {"time": 3, "open": 101, "high": 103, "low": 99, "close": 102},
    ]

    pools_1 = [
        {
            "price": 105.0,
            "type": "PDH",
            "role": "PRIMARY",
            "strength": 80,
            "touches": 3,
        },
        {"price": 95.0, "type": "PDL", "role": "PRIMARY", "strength": 70, "touches": 2},
    ]
    pools_2 = [
        {
            "price": 105.1,
            "type": "PDH",
            "role": "PRIMARY",
            "strength": 80,
            "touches": 3,
        },
        {"price": 95.0, "type": "PDL", "role": "PRIMARY", "strength": 70, "touches": 2},
    ]

    items_1 = rendered_levels_items(
        select_pools_for_render_as_levels(pools_1, ref_price=100.0, ohlcv_bars=ohlcv)
    )
    items_2 = rendered_levels_items(
        select_pools_for_render_as_levels(pools_2, ref_price=100.0, ohlcv_bars=ohlcv)
    )

    assert geometry_hash(items_1) != geometry_hash(items_2)


def test_shadow_items_hash_is_deterministic() -> None:
    viewer_state = {
        "levels_shadow_v1": [
            {
                "tf": "5m",
                "kind": "line",
                "label": "RANGE_EXTREME",
                "price": 4533.73,
                "role": "PRIMARY",
                "render_hint": {
                    "title": "RANGE_ P",
                    "axis_label": True,
                    "line_visible": True,
                },
            },
            {
                "tf": "5m",
                "kind": "band",
                "label": "EQH",
                "top": 4513.404193548388,
                "bot": 4513.404193548388,
                "role": "PRIMARY",
                "render_hint": {
                    "title": "EQH P",
                    "axis_label": False,
                    "line_visible": True,
                },
            },
        ]
    }

    a = shadow_items_from_viewer_state(viewer_state, tf="5m")
    b = shadow_items_from_viewer_state(viewer_state, tf="5m")
    assert geometry_hash(a) == geometry_hash(b)


def test_candidates_items_are_empty_when_field_missing() -> None:
    viewer_state = {}
    items = candidates_items_from_viewer_state(viewer_state, owner_tf="5m")
    assert items == []
    assert candidates_geometry_hash(items) == candidates_geometry_hash([])


def test_candidates_hash_is_deterministic_and_tf_filtered() -> None:
    viewer_state = {
        "levels_candidates_v1": [
            {
                "owner_tf": "5m",
                "kind": "line",
                "label": "EQH",
                "source": "POOL_DERIVED",
                "price": 123.456789,
            },
            {
                "owner_tf": "1h",
                "kind": "band",
                "label": "EQL",
                "source": "POOL_DERIVED",
                "top": 200.0,
                "bot": 100.0,
            },
        ]
    }

    a = candidates_items_from_viewer_state(viewer_state, owner_tf="5m")
    b = candidates_items_from_viewer_state(viewer_state, owner_tf="5m")
    assert len(a) == 1
    assert a == b
    assert candidates_geometry_hash(a) == candidates_geometry_hash(b)
