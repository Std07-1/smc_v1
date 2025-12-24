"""Тест: gated-empty SmcHint не має затирати останній валідний стан у UI.

Критично для UX у вихідні/market close:
- Stage0 може виставити gates (NO_5M_DATA/STALE_5M/INSUFFICIENT_5M).
- Пайплайн не повинен перезаписувати `smc_hint` блоками None, якщо раніше
  вже був валідний computed стан.
"""

from app.smc_producer import _preserve_previous_hint_if_gated


def test_preserve_previous_hint_when_new_is_gated_empty() -> None:
    previous = {
        "structure": {"trend": "UP", "bias": "BULL", "range_state": "RANGE"},
        "liquidity": {"amd_phase": "DISTRIBUTION", "pools": [{"level": 1.0}]},
        "zones": {"active_zones": [{"price_min": 1.0, "price_max": 2.0}]},
        "meta": {"gates": [], "history_state": "ok"},
    }

    new_gated = {
        "structure": None,
        "liquidity": None,
        "zones": None,
        "signals": [],
        "meta": {
            "history_state": "stale_tail",
            "gates": [{"code": "STALE_5M", "message": "tail stale"}],
            "tf_health": {"5m": {"has_data": True, "bars": 123}},
        },
    }

    merged, preserved = _preserve_previous_hint_if_gated(
        previous_hint=previous,
        new_hint=new_gated,
    )

    assert preserved is True
    assert isinstance(merged, dict)
    assert merged.get("structure") == previous["structure"]
    assert merged.get("liquidity") == previous["liquidity"]
    assert merged.get("zones") == previous["zones"]

    meta = merged.get("meta")
    assert isinstance(meta, dict)
    assert meta.get("history_state") == "stale_tail"
    assert meta.get("gates") == [{"code": "STALE_5M", "message": "tail stale"}]
    assert meta.get("smc_hint_preserved") is True


def test_do_not_preserve_when_previous_is_empty() -> None:
    merged, preserved = _preserve_previous_hint_if_gated(
        previous_hint=None,
        new_hint={
            "structure": None,
            "liquidity": None,
            "zones": None,
            "meta": {"gates": [{"code": "NO_5M_DATA", "message": "no"}]},
        },
    )
    assert preserved is False
    assert isinstance(merged, dict)
    assert merged.get("meta", {}).get("gates")
