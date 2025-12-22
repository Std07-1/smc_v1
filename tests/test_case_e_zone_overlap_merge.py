"""Тести для Випадку E: дублі/перекриття зон.

Ціль:
- якщо дві зони одного типу/ролі/напрямку/TF мають великий overlap (IoU),
  Stage4 повинен нормалізувати їх як одну (merge-by-overlap).

Це тестує саме Stage4 фасад (smc_zones.compute_zones_state), ізольовано від
детекторів через monkeypatch.
"""

from __future__ import annotations

import pandas as pd

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import SmcInput, SmcStructureState, SmcZone, SmcZoneType
from smc_zones import compute_zones_state


def _frame(*, close: float = 105.0, n: int = 40) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame(
        {
            "open": [close] * n,
            "high": [close + 1.0] * n,
            "low": [close - 1.0] * n,
            "close": [close] * n,
            "volume": [1.0] * n,
        },
        index=idx,
    )


def _zone(
    *,
    zone_id: str,
    price_min: float,
    price_max: float,
    strength: float,
    origin_time: pd.Timestamp,
) -> SmcZone:
    return SmcZone(
        zone_type=SmcZoneType.ORDER_BLOCK,
        price_min=float(price_min),
        price_max=float(price_max),
        timeframe="5m",
        origin_time=origin_time,
        direction="LONG",
        role="PRIMARY",
        strength=float(strength),
        confidence=1.0,
        components=[],
        zone_id=str(zone_id),
        meta={},
    )


def test_merge_by_overlap_iou_keeps_best_and_marks_merged_from(monkeypatch) -> None:
    df = _frame()
    now_ts = df.index[-1]

    z_best = _zone(
        zone_id="z_best",
        price_min=100.0,
        price_max=110.0,
        strength=2.0,
        origin_time=now_ts,
    )

    # IoU для [100..110] vs [101..109]: inter=8, union=10 => 0.8
    z_dup = _zone(
        zone_id="z_dup",
        price_min=101.0,
        price_max=109.0,
        strength=0.1,
        origin_time=now_ts,
    )

    monkeypatch.setattr(
        "smc_zones.detect_order_blocks", lambda *a, **k: [z_best, z_dup]
    )
    monkeypatch.setattr("smc_zones.detect_breakers", lambda *a, **k: [])
    monkeypatch.setattr("smc_zones.detect_fvg_zones", lambda *a, **k: [])

    snapshot = SmcInput(
        symbol="XAUUSD", tf_primary="5m", ohlc_by_tf={"5m": df}, context={}
    )
    structure = SmcStructureState(meta={"atr_last": 5.0})
    cfg = SmcCoreConfig(
        zone_merge_iou_threshold=0.6,
        # не заважаємо Case E
        max_zone_span_atr=None,
        ob_max_active_distance_atr=None,
        max_lookback_bars=300,
    )

    st = compute_zones_state(
        snapshot=snapshot, structure=structure, liquidity=None, cfg=cfg
    )

    assert len(st.zones) == 1
    assert st.zones[0].zone_id == "z_best"
    assert "merged_from" in (st.zones[0].meta or {})
    assert "z_dup" in list(st.zones[0].meta.get("merged_from") or [])

    merge_meta = (st.meta or {}).get("merge")
    assert isinstance(merge_meta, dict)
    assert int(merge_meta.get("merged_losers") or 0) == 1
