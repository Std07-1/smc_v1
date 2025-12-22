"""Unit-тести для Випадку D: «зона надто широка» (span_atr).

Перевіряємо два інваріанти:
- active_zones не включає надто широкі зони (щоб не забивали top-K UI);
- POI/FTA не бере надто широкі зони в список POI.
"""

from __future__ import annotations

import pandas as pd

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import SmcInput, SmcStructureState, SmcZone, SmcZoneType
from smc_zones import compute_zones_state
from smc_zones.poi_fta import build_active_poi_zones

BASE_TS = pd.Timestamp("2024-01-01T00:00:00Z")


def _ts(i: int) -> pd.Timestamp:
    return BASE_TS + pd.Timedelta(minutes=i)


def _frame_5m(values: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    idx = pd.DatetimeIndex([_ts(i * 5) for i in range(len(values))], tz="UTC")
    return pd.DataFrame(values, columns=["open", "high", "low", "close"], index=idx)


def _zone(*, zone_id: str, lo: float, hi: float) -> SmcZone:
    return SmcZone(
        zone_type=SmcZoneType.ORDER_BLOCK,
        price_min=float(lo),
        price_max=float(hi),
        timeframe="5m",
        origin_time=_ts(0),
        direction="LONG",  # type: ignore[arg-type]
        role="PRIMARY",
        strength=1.0,
        confidence=1.0,
        components=["test"],
        zone_id=zone_id,
        meta={},
    )


def test_poi_builder_archives_wide_zones_by_span_atr() -> None:
    cfg = SmcCoreConfig(max_zone_span_atr=2.0)

    df5m = _frame_5m(
        [
            (100.0, 100.2, 99.8, 100.0),
            (100.0, 100.3, 99.9, 100.1),
            (100.1, 100.25, 100.0, 100.15),
        ]
    )

    snapshot = SmcInput(symbol="xauusd", tf_primary="5m", ohlc_by_tf={"5m": df5m})
    structure = SmcStructureState(primary_tf="5m", bias="LONG", meta={"atr_last": 1.0})

    wide = _zone(zone_id="z_wide", lo=90.0, hi=96.0)  # span_atr=6.0
    # Свіжа LONG-зона нижче поточної ціни: low_min > price_max => filled_pct=0.
    ok = _zone(zone_id="z_ok", lo=98.0, hi=99.0)  # span_atr=1.0

    poi_zones, _, meta = build_active_poi_zones(
        snapshot=snapshot,
        structure=structure,
        liquidity=None,
        zones=[wide, ok],
        cfg=cfg,
    )

    assert all(z.zone_id != "z_wide" for z in poi_zones)
    assert any(z.zone_id == "z_ok" for z in poi_zones)
    assert int(meta.get("poi_archived_wide_span_atr") or 0) >= 1


def test_active_zones_filters_wide_span_atr(monkeypatch) -> None:
    cfg = SmcCoreConfig(max_zone_span_atr=2.0, ob_max_active_distance_atr=None)

    df5m = _frame_5m(
        [
            (100.0, 100.2, 99.8, 100.0),
            (100.0, 100.3, 99.9, 100.1),
            (100.1, 100.25, 100.0, 100.15),
        ]
    )

    snapshot = SmcInput(symbol="xauusd", tf_primary="5m", ohlc_by_tf={"5m": df5m})
    structure = SmcStructureState(primary_tf="5m", bias="LONG", meta={"atr_last": 1.0})

    wide = _zone(zone_id="z_wide", lo=90.0, hi=96.0)
    ok = _zone(zone_id="z_ok", lo=98.0, hi=99.0)

    # Детектори підміняємо, щоб тест був ізольований і детермінований.
    monkeypatch.setattr(
        "smc_zones.detect_order_blocks",
        lambda *args, **kwargs: [wide, ok],
    )
    monkeypatch.setattr(
        "smc_zones.detect_breakers",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "smc_zones.detect_fvg_zones",
        lambda *args, **kwargs: [],
    )

    st = compute_zones_state(
        snapshot=snapshot, structure=structure, liquidity=None, cfg=cfg
    )

    assert any(z.zone_id == "z_ok" for z in st.active_zones)
    assert all(z.zone_id != "z_wide" for z in st.active_zones)

    meta = st.meta or {}
    assert meta.get("max_zone_span_atr") == 2.0
    assert int(meta.get("zones_filtered_by_span_atr") or 0) >= 1
