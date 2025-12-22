"""Тести для Випадку G: WICK_CLUSTER як трекер (стабільні ID).

Фокус:
- lifecycle_journal має будувати стабільні pool-id для WICK_CLUSTER, якщо є meta.cluster_id;
- трекер у smc_liquidity.sfp_wick має матчити кластери між барами по proximity.

Це мінімізує rebucket/flicker/context_flip у report_XAUUSD.
"""

from __future__ import annotations

from smc_core.config import SmcCoreConfig


def test_pool_id_quantized_uses_cluster_id_for_wick_cluster() -> None:
    from smc_core.lifecycle_journal import _pool_id_quantized

    p1 = {
        "liq_type": "WICK_CLUSTER",
        "role": "PRIMARY",
        "level": 110.0,
        "first_time": "t1",
        "last_time": "t1",
        "meta": {"side": "HIGH", "cluster_id": "wc:HIGH:110.00"},
    }
    p2 = {
        "liq_type": "WICK_CLUSTER",
        "role": "PRIMARY",
        "level": 110.01,
        "first_time": "t1",
        "last_time": "t2",
        "meta": {"side": "HIGH", "cluster_id": "wc:HIGH:110.00"},
    }

    # tick не важливий, бо для WICK_CLUSTER беремо cluster_id.
    assert _pool_id_quantized(p1, tick=0.01) == _pool_id_quantized(p2, tick=0.01)


def test_wick_cluster_tracker_matches_prev_by_proximity() -> None:
    from smc_liquidity.sfp_wick import _track_wick_clusters

    cfg = SmcCoreConfig(
        liquidity_wick_cluster_track_enabled=True,
        liquidity_wick_cluster_track_tol_pct=0.002,
        liquidity_wick_cluster_track_max_abs_move_atr=0.6,
    )

    prev = [
        {
            "cluster_id": "wc:HIGH:110.00",
            "level": 110.0,
            "side": "HIGH",
            "count": 3,
            "max_wick": 2.0,
            "first_ts": None,
            "last_ts": None,
            "source": "range",
        }
    ]
    cur = [
        {
            "level": 110.15,
            "side": "HIGH",
            "count": 2,
            "max_wick": 1.8,
            "first_ts": None,
            "last_ts": None,
            "source": "range",
        }
    ]

    out = _track_wick_clusters(
        clusters=cur,
        prev_clusters=prev,
        price_ref=110.0,
        atr_last=5.0,
        cfg=cfg,
    )
    assert isinstance(out, list) and out
    assert out[0].get("cluster_id") == "wc:HIGH:110.00"
