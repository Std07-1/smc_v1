"""Юніт-тести чистої логіки UI_v2 chart_adapter.

Ціль: захистити інваріанти (range-нормалізація, hit-test price-axis/pane,
вибір effective price range) без браузера і без Node.js.

JS виконуємо через quickjs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

try:
    import quickjs  # type: ignore
except Exception:  # pragma: no cover
    quickjs = None


@pytest.mark.skipif(quickjs is None, reason="quickjs не встановлено")
def test_normalize_range_enforces_min_span() -> None:
    ctx = quickjs.Context()
    logic_path = Path("UI_v2/web_client/chart_adapter_logic.js")
    ctx.eval(logic_path.read_text(encoding="utf-8"))

    # min == max → розширюємо до minPriceSpan
    res_json = ctx.eval(
        "JSON.stringify(ChartAdapterLogic.normalizeRange({min: 1.0, max: 1.0}, 0.0001))"
    )
    res = json.loads(res_json)
    assert pytest.approx(res["max"] - res["min"], rel=0, abs=1e-12) == 0.0001

    # занадто вузький діапазон → нормалізуємо
    res_json = ctx.eval(
        "JSON.stringify(ChartAdapterLogic.normalizeRange({min: 1.0, max: 1.00001}, 0.0001))"
    )
    res = json.loads(res_json)
    assert pytest.approx(res["max"] - res["min"], rel=0, abs=1e-12) == 0.0001

    # Інвертований діапазон (min>max) у поточній логіці нормалізується навколо mid.
    res_json = ctx.eval(
        "JSON.stringify(ChartAdapterLogic.normalizeRange({min: 2, max: 1}, 0.0001))"
    )
    res = json.loads(res_json)
    assert pytest.approx(res["max"] - res["min"], rel=0, abs=1e-12) == 0.0001
    assert pytest.approx((res["max"] + res["min"]) / 2, rel=0, abs=1e-12) == 1.5


@pytest.mark.skipif(quickjs is None, reason="quickjs не встановлено")
def test_hit_test_axis_and_pane_with_fallback_width() -> None:
    ctx = quickjs.Context()
    logic_path = Path("UI_v2/web_client/chart_adapter_logic.js")
    ctx.eval(logic_path.read_text(encoding="utf-8"))

    # Симуляція стану одразу після init: paneWidth=0, priceScaleWidth=0.
    # Маємо fallback: axis = правий край шириною 56px.
    args = {
        "x": 950,
        "y": 100,
        "width": 1000,
        "height": 500,
        "paneWidth": 0,
        "paneHeight": 0,
        "priceScaleWidth": 0,
    }
    args_json = json.dumps(args)

    assert ctx.eval(f"ChartAdapterLogic.isPointerInPriceAxis({args_json}, 56)") is True
    assert ctx.eval(f"ChartAdapterLogic.isPointerInsidePane({args_json}, 56)") is False

    # Точка в pane (не в осі)
    args["x"] = 100
    args_json = json.dumps(args)
    assert ctx.eval(f"ChartAdapterLogic.isPointerInPriceAxis({args_json}, 56)") is False
    assert ctx.eval(f"ChartAdapterLogic.isPointerInsidePane({args_json}, 56)") is True


@pytest.mark.skipif(quickjs is None, reason="quickjs не встановлено")
def test_compute_effective_price_range_priorities() -> None:
    ctx = quickjs.Context()
    logic_path = Path("UI_v2/web_client/chart_adapter_logic.js")
    ctx.eval(logic_path.read_text(encoding="utf-8"))

    # 1) manualRange має пріоритет
    args = {
        "manualRange": {"min": 10, "max": 20},
        "lastAutoRange": {"min": 1, "max": 2},
        "paneHeight": 400,
        "topPrice": 5,
        "bottomPrice": 7,
    }
    res_json = ctx.eval(
        f"JSON.stringify(ChartAdapterLogic.computeEffectivePriceRange({json.dumps(args)}))"
    )
    res = json.loads(res_json)
    assert res["range"]["min"] == 10
    assert res["range"]["max"] == 20

    # 2) якщо manualRange нема — беремо lastAutoRange
    args["manualRange"] = None
    res_json = ctx.eval(
        f"JSON.stringify(ChartAdapterLogic.computeEffectivePriceRange({json.dumps(args)}))"
    )
    res = json.loads(res_json)
    assert res["range"]["min"] == 1
    assert res["range"]["max"] == 2

    # 3) якщо і lastAutoRange нема — fallback рахуємо з top/bottom
    args["lastAutoRange"] = None
    args["topPrice"] = 100
    args["bottomPrice"] = 90
    res_json = ctx.eval(
        f"JSON.stringify(ChartAdapterLogic.computeEffectivePriceRange({json.dumps(args)}))"
    )
    res = json.loads(res_json)
    assert res["range"]["min"] == 90
    assert res["range"]["max"] == 100
    assert res["nextLastAutoRange"]["min"] == 90
    assert res["nextLastAutoRange"]["max"] == 100

    # 4) якщо paneHeight невалідний — range=null
    args["paneHeight"] = 0
    res_json = ctx.eval(
        f"JSON.stringify(ChartAdapterLogic.computeEffectivePriceRange({json.dumps(args)}))"
    )
    res = json.loads(res_json)
    assert res["range"] is None


@pytest.mark.skipif(quickjs is None, reason="quickjs не встановлено")
def test_compute_wheel_zoom_range_direction_and_anchor() -> None:
    ctx = quickjs.Context()
    logic_path = Path("UI_v2/web_client/chart_adapter_logic.js")
    ctx.eval(logic_path.read_text(encoding="utf-8"))

    base = {
        "range": {"min": 90.0, "max": 110.0},
        "anchor": 100.0,
        "minPriceSpan": 0.0001,
        "intensity": 0.002,
        "maxDelta": 600,
    }

    # deltaY < 0 => zoom in: span зменшується
    args = {**base, "deltaY": -120.0}
    res_json = ctx.eval(
        f"JSON.stringify(ChartAdapterLogic.computeWheelZoomRange({json.dumps(args)}))"
    )
    res_in = json.loads(res_json)
    span_in = res_in["max"] - res_in["min"]
    assert span_in < 20.0

    # deltaY > 0 => zoom out: span збільшується
    args = {**base, "deltaY": 120.0}
    res_json = ctx.eval(
        f"JSON.stringify(ChartAdapterLogic.computeWheelZoomRange({json.dumps(args)}))"
    )
    res_out = json.loads(res_json)
    span_out = res_out["max"] - res_out["min"]
    assert span_out > 20.0

    # anchor має залишатися в межах діапазону
    assert res_in["min"] <= 100.0 <= res_in["max"]
    assert res_out["min"] <= 100.0 <= res_out["max"]


@pytest.mark.skipif(quickjs is None, reason="quickjs не встановлено")
def test_compute_wheel_pan_range_shifts_range() -> None:
    ctx = quickjs.Context()
    logic_path = Path("UI_v2/web_client/chart_adapter_logic.js")
    ctx.eval(logic_path.read_text(encoding="utf-8"))

    base = {
        "range": {"min": 90.0, "max": 110.0},
        "paneHeight": 200.0,
        "minPriceSpan": 0.0001,
        "panFactor": 0.5,
    }

    # deltaY > 0 => offset від'ємний => діапазон зсувається вниз
    args = {**base, "deltaY": 20.0}
    res_json = ctx.eval(
        f"JSON.stringify(ChartAdapterLogic.computeWheelPanRange({json.dumps(args)}))"
    )
    res_down = json.loads(res_json)
    assert res_down["min"] < 90.0
    assert res_down["max"] < 110.0

    # deltaY < 0 => offset додатній => діапазон зсувається вгору
    args = {**base, "deltaY": -20.0}
    res_json = ctx.eval(
        f"JSON.stringify(ChartAdapterLogic.computeWheelPanRange({json.dumps(args)}))"
    )
    res_up = json.loads(res_json)
    assert res_up["min"] > 90.0
    assert res_up["max"] > 110.0
