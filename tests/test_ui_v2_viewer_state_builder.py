"""Тести для UI_v2.viewer_state_builder.

Перевіряємо:
- базову побудову SmcViewerState;
- бекфіл подій/зон через ViewerStateCache;
- пріоритет FXCM-блоку над meta.fxcm і вплив на session.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from core.contracts.viewer_state import (
    VIEWER_STATE_SCHEMA_VERSION,
    FxcmMeta,
    SmcViewerState,
    UiSmcAssetPayload,
    UiSmcMeta,
)
from UI_v2.viewer_state_builder import (
    ViewerStateCache,
    build_viewer_state,
)


def _make_basic_asset(**overrides: Any) -> UiSmcAssetPayload:
    """Формує мінімальний UiSmcAssetPayload для тестів."""

    base: UiSmcAssetPayload = {
        "symbol": "XAUUSD",
        "stats": {
            "session_tag": "London",
            "current_price": 2412.5,
        },
        "smc_hint": {
            "structure": {},
            "liquidity": {},
            "zones": {},
            "signals": [],
            "meta": {},
        },
        "smc_structure": {
            "trend": "up",
            "bias": "long",
            "range_state": "dev_up",
            "legs": [
                {
                    "label": "L1",
                    "direction": "up",
                    "from_index": 10,
                    "to_index": 20,
                    "strength": 0.8,
                }
            ],
            "swings": [
                {
                    "kind": "HH",
                    "price": 2410.0,
                    "time": "2025-12-08T07:55:00+00:00",
                }
            ],
            "ranges": [
                {
                    "high": 2415.0,
                    "low": 2400.0,
                    "state": "inside",
                    "start_time": "2025-12-08T07:00:00+00:00",
                    "end_time": "2025-12-08T08:00:00+00:00",
                }
            ],
            "events": [
                {
                    "event_type": "BOS_UP",
                    "direction": "up",
                    "price": 2412.5,
                    "time": 1_701_721_500_000,
                    "status": "confirmed",
                }
            ],
            "ote_zones": [
                {
                    "direction": "up",
                    "role": "primary",
                    "ote_min": 0.62,
                    "ote_max": 0.79,
                }
            ],
        },
        "smc_liquidity": {
            "amd_phase": "MANIP",
            "pools": [
                {
                    "level": 2415.0,
                    "liq_type": "EQH",
                    "role": "target",
                    "strength": 0.9,
                    "meta": {},
                }
            ],
            "magnets": [
                {
                    "kind": "FVG",
                    "level": 2413.0,
                    "meta": {},
                }
            ],
        },
        "smc_zones": {
            "zones": [
                {
                    "kind": "OB",
                    "direction": "up",
                    "price_min": 2408.0,
                    "price_max": 2410.0,
                }
            ]
        },
        "price": 2412.5,
        "price_str": "2412.5",
        "live_price_mid": 2412.5,
        "live_price_mid_str": "2412.5",
        "live_price_bid": 2412.4,
        "live_price_bid_str": "2412.4",
        "live_price_ask": 2412.6,
        "live_price_ask_str": "2412.6",
        "live_price_spread": 0.2,
    }
    base.update(overrides)  # type: ignore
    return base


def _make_basic_meta(**overrides: Any) -> UiSmcMeta:
    base: UiSmcMeta = {
        "ts": "2025-12-08T08:05:00+00:00",
        "seq": 123,
        "schema_version": "smc_state_v1",
    }
    base.update(overrides)  # type: ignore
    return base


def test_build_viewer_state_basic() -> None:
    """Базовий сценарій: будуємо SmcViewerState з повного asset + meta."""

    asset = _make_basic_asset()
    meta = _make_basic_meta()

    state: SmcViewerState = build_viewer_state(asset, meta, fxcm_block=None, cache=None)

    assert state["symbol"] == "XAUUSD"  # type: ignore
    assert state["payload_ts"] == meta["ts"]  # type: ignore
    assert state["payload_seq"] == meta["seq"]  # type: ignore
    assert state["schema"] == VIEWER_STATE_SCHEMA_VERSION  # type: ignore
    assert state["price"] == pytest.approx(2412.5)  # type: ignore
    assert state["session"] == "London"  # type: ignore

    structure = cast(dict, state["structure"])  # type: ignore
    assert structure["trend"] == "up"
    assert structure["bias"] == "long"
    assert structure["range_state"] == "dev_up"
    assert isinstance(structure["legs"], list) and structure["legs"]
    assert isinstance(structure["swings"], list) and structure["swings"]
    assert isinstance(structure["ranges"], list) and structure["ranges"]
    assert isinstance(structure["events"], list) and structure["events"]
    assert isinstance(structure["ote_zones"], list) and structure["ote_zones"]

    liquidity = cast(dict, state["liquidity"])  # type: ignore
    assert liquidity["amd_phase"] == "MANIP"
    assert isinstance(liquidity["pools"], list) and liquidity["pools"]
    assert isinstance(liquidity["magnets"], list) and liquidity["magnets"]
    first_pool = cast(dict, liquidity["pools"][0])
    assert first_pool["price"] == pytest.approx(2415.0)
    assert first_pool["type"] == "EQH"
    assert first_pool["liq_type"] == "EQH"

    zones = cast(dict, state["zones"])  # type: ignore
    assert "raw" in zones
    raw_zones = cast(dict, zones["raw"])
    assert "zones" in raw_zones
    assert isinstance(raw_zones["zones"], list) and raw_zones["zones"]

    assert "fxcm" not in state or state["fxcm"] is None


def test_build_viewer_state_includes_pipeline_local_from_stats() -> None:
    asset = _make_basic_asset(
        stats={
            "session_tag": "London",
            "current_price": 2412.5,
            "pipeline_state_local": "WARMUP",
            "pipeline_ready_bars": 120,
            "pipeline_required_bars": 200,
            "pipeline_ready_ratio": 0.6,
        }
    )
    meta = _make_basic_meta()

    state: SmcViewerState = build_viewer_state(asset, meta, fxcm_block=None, cache=None)

    pipeline_local = cast(dict, state.get("pipeline_local"))
    assert pipeline_local["state"] == "WARMUP"
    assert pipeline_local["ready_bars"] == 120
    assert pipeline_local["required_bars"] == 200
    assert pipeline_local["ready_ratio"] == pytest.approx(0.6)


def test_build_viewer_state_cache_backfills_events_and_zones() -> None:
    """Кеш має бекфілити події та зони, якщо в новому пейлоаді їх немає."""

    cache = ViewerStateCache()
    asset_with_events = _make_basic_asset()
    meta1 = _make_basic_meta(seq=1)

    state1 = build_viewer_state(asset_with_events, meta1, fxcm_block=None, cache=cache)

    events1 = state1["structure"]["events"]  # type: ignore[index]
    zones1 = state1["zones"]["raw"]  # type: ignore[index]

    assert events1
    assert zones1

    asset_without_events = _make_basic_asset(
        smc_structure={
            "trend": "up",
            "bias": "long",
            "range_state": "dev_up",
        },
        smc_liquidity={"amd_phase": "MANIP", "pools": [], "magnets": []},
        smc_zones={},
    )
    meta2 = _make_basic_meta(seq=2)

    state2 = build_viewer_state(
        asset_without_events, meta2, fxcm_block=None, cache=cache
    )

    events2 = state2["structure"]["events"]  # type: ignore[index]
    zones2 = state2["zones"]["raw"]  # type: ignore[index]

    assert events2 == events1
    assert zones2 == zones1


def test_build_viewer_state_includes_tf_meta_and_liquidity_targets() -> None:
    asset = _make_basic_asset(
        smc_hint={
            "structure": {},
            "liquidity": {},
            "zones": {},
            "signals": [],
            "meta": {
                "tf_plan": {
                    "tf_exec": "1m",
                    "tf_structure": "5m",
                    "tf_context": ("1h", "4h"),
                },
                "tf_effective": ["1m", "1h"],
                "gates": [{"code": "NO_5M_DATA", "message": "Немає 5m"}],
                "history_state": "missing",
                "age_ms": 12_000,
                "last_open_time_ms": 1_700_000_000_000,
                "last_ts": "2025-12-08T08:05:00+00:00",
                "lag_ms": 12_000,
                "bars_5m": 0,
                "tf_health": {
                    "1m": {
                        "has_data": True,
                        "bars": 100,
                        "last_ts": "-",
                        "lag_ms": 0,
                    },
                    "5m": {
                        "has_data": False,
                        "bars": 0,
                        "last_ts": "-",
                        "lag_ms": None,
                    },
                    "1h": {
                        "has_data": True,
                        "bars": 10,
                        "last_ts": "-",
                        "lag_ms": 0,
                    },
                    "4h": {
                        "has_data": False,
                        "bars": 0,
                        "last_ts": "-",
                        "lag_ms": None,
                    },
                },
            },
        },
        smc_liquidity={
            "amd_phase": "MANIP",
            "pools": [],
            "magnets": [],
            "meta": {
                "liquidity_targets": [
                    {
                        "role": "internal",
                        "tf": "5m",
                        "side": "above",
                        "price": 2415.0,
                        "type": "EQH",
                        "strength": 55.5,
                        "reason": ["test"],
                    },
                    {
                        "role": "external",
                        "tf": "1h",
                        "side": "below",
                        "price": 2400.0,
                        "type": "PIVOT",
                        "strength": 44.4,
                        "reason": ["test"],
                    },
                ]
            },
        },
    )
    meta = _make_basic_meta()

    state: SmcViewerState = build_viewer_state(asset, meta, fxcm_block=None, cache=None)

    assert isinstance(state.get("tf_plan"), dict)
    assert state["tf_plan"].get("tf_structure") == "5m"  # type: ignore[index]
    assert state.get("tf_effective") == ["1m", "1h"]

    gates = state.get("gates")
    assert isinstance(gates, list) and gates
    assert gates[0].get("code") == "NO_5M_DATA"

    assert state.get("history_state") == "missing"
    assert state.get("bars_5m") == 0
    assert state.get("lag_ms") == 12_000

    tf_health = state.get("tf_health")
    assert isinstance(tf_health, dict)
    assert set(tf_health.keys()) >= {"1m", "5m", "1h", "4h"}

    liquidity = cast(dict, state["liquidity"])  # type: ignore[index]
    targets = liquidity.get("targets")
    assert isinstance(targets, list) and len(targets) == 2
    assert targets[0].get("role") == "internal"


def test_build_viewer_state_fxcm_priority_and_session_override() -> None:
    """FXCM-блок має пріоритет і може переписувати session."""

    asset = _make_basic_asset()
    meta = _make_basic_meta(
        fxcm={
            "market_state": "closed",
            "process_state": "idle",
            "price_state": "stale",
            "ohlcv_state": "idle",
            "lag_seconds": 1.5,
            "last_bar_close_utc": "2025-12-08T07:59:00+00:00",
            "next_open_utc": "2025-12-09T00:00:00+00:00",
            "session": {
                "tag": "Asia",
                "name": "Asia",
                "next_open_utc": "2025-12-09T00:00:00+00:00",
                "seconds_to_open": 0.0,
                "seconds_to_close": 3600.0,
            },
        }
    )

    fxcm_block: FxcmMeta = {
        "market_state": "open",
        "process_state": "streaming",
        "price_state": "live",
        "ohlcv_state": "streaming",
        "lag_seconds": 0.3,
        "last_bar_close_utc": "2025-12-08T08:04:00+00:00",
        "next_open_utc": "2025-12-09T00:00:00+00:00",
        "session": {
            "tag": "London",
            "name": "London",
            "next_open_utc": "2025-12-09T00:00:00+00:00",
            "seconds_to_open": 0.0,
            "seconds_to_close": 7200.0,
        },
    }

    cache = ViewerStateCache()

    state = build_viewer_state(asset, meta, fxcm_block=fxcm_block, cache=cache)

    fxcm_state = cast(dict, state["fxcm"])  # type: ignore
    assert fxcm_state["market_state"] == "open"
    assert fxcm_state["process_state"] == "streaming"
    assert state["session"] == "London"  # type: ignore
    assert cache.last_fxcm_meta == fxcm_block


def test_build_viewer_state_preserves_pipeline_meta() -> None:
    """Pipeline-поля з meta мають доходити до viewer_state.meta."""

    asset = _make_basic_asset()
    meta = _make_basic_meta(
        pipeline_state="WARMUP",
        pipeline_ready_assets=2,
        pipeline_min_ready=3,
        pipeline_assets_total=5,
        pipeline_ready_pct=0.4,
    )

    state: SmcViewerState = build_viewer_state(asset, meta, fxcm_block=None, cache=None)

    meta_block = cast(dict[str, Any], state["meta"])  # type: ignore
    assert meta_block["pipeline_state"] == "WARMUP"
    assert meta_block["pipeline_ready_assets"] == 2
    assert meta_block["pipeline_min_ready"] == 3
    assert meta_block["pipeline_assets_total"] == 5
    assert meta_block["pipeline_ready_pct"] == 0.4


def test_build_viewer_state_hides_newborn_zones_and_pools_until_next_close() -> None:
    """Випадок C: нові зони/пули не мають потрапляти в UI одразу.

    Політика:
    - zones: MIN_CLOSE_STEPS_BEFORE_SHOW=1 (вперше на close -> сховано; наступний close -> видно)
    - pools: close-only + MIN_CLOSE_STEPS_BEFORE_SHOW=2 (видно починаючи з 3-го close, якщо стабільні)
    """

    cache = ViewerStateCache()

    asset1 = _make_basic_asset(
        smc_hint={
            "structure": {},
            "liquidity": {},
            "zones": {},
            "signals": [],
            "meta": {"smc_compute_kind": "close"},
        },
        smc_liquidity={
            "amd_phase": "MANIP",
            "pools": [
                {
                    "level": 2415.0,
                    "liq_type": "WICK_CLUSTER",
                    "role": "PRIMARY",
                    "strength": 0.5,
                    "meta": {"side": "above"},
                }
            ],
            "magnets": [],
        },
        smc_zones={
            "active_zones": [
                {
                    "zone_id": "z_test_1",
                    "zone_type": "OB",
                    "direction": "LONG",
                    "role": "PRIMARY",
                    "timeframe": "5m",
                    "price_min": 2408.0,
                    "price_max": 2410.0,
                }
            ]
        },
    )
    meta1 = _make_basic_meta(seq=1)

    state1 = build_viewer_state(asset1, meta1, fxcm_block=None, cache=cache)

    # Перший close: новонароджене сховане.
    liquidity1 = cast(dict, state1["liquidity"])  # type: ignore[index]
    assert liquidity1.get("pools") == []
    pools_meta1 = cast(dict, liquidity1.get("pools_meta") or {})
    assert pools_meta1.get("truth_count") == 1
    assert pools_meta1.get("shown_count") == 0
    zones1 = cast(dict, state1["zones"])  # type: ignore[index]
    raw1 = cast(dict, zones1.get("raw"))
    assert raw1.get("active_zones") == []

    # Другий close з тими ж сутностями:
    # - zones мають стати видимими
    # - pools все ще newborn (age=1 < 2) -> сховано
    asset2 = _make_basic_asset(
        smc_hint={
            "structure": {},
            "liquidity": {},
            "zones": {},
            "signals": [],
            "meta": {"smc_compute_kind": "close"},
        },
        smc_liquidity=asset1["smc_liquidity"],
        smc_zones=asset1["smc_zones"],
    )
    meta2 = _make_basic_meta(seq=2)
    state2 = build_viewer_state(asset2, meta2, fxcm_block=None, cache=cache)

    liquidity2 = cast(dict, state2["liquidity"])  # type: ignore[index]
    pools2 = liquidity2.get("pools")
    assert pools2 == []

    zones2 = cast(dict, state2["zones"])  # type: ignore[index]
    raw2 = cast(dict, zones2.get("raw"))
    active2 = raw2.get("active_zones")
    assert isinstance(active2, list) and len(active2) == 1

    # Третій close: pools мають стати видимими (age=2).
    asset3 = _make_basic_asset(
        smc_hint={
            "structure": {},
            "liquidity": {},
            "zones": {},
            "signals": [],
            "meta": {"smc_compute_kind": "close"},
        },
        smc_liquidity=asset1["smc_liquidity"],
        smc_zones=asset1["smc_zones"],
    )
    meta3 = _make_basic_meta(seq=3)
    state3 = build_viewer_state(asset3, meta3, fxcm_block=None, cache=cache)
    liquidity3 = cast(dict, state3["liquidity"])  # type: ignore[index]
    pools3 = liquidity3.get("pools")
    assert isinstance(pools3, list) and len(pools3) == 1


def test_build_viewer_state_preview_does_not_promote_newborn() -> None:
    """Preview не має промоутити нові сутності до "born".

    Якщо сутність з'явилась лише на preview, ми її не показуємо і не рахуємо як born.
    """

    cache = ViewerStateCache()

    asset_preview = _make_basic_asset(
        smc_hint={
            "structure": {},
            "liquidity": {},
            "zones": {},
            "signals": [],
            "meta": {"smc_compute_kind": "preview"},
        },
        smc_liquidity={
            "amd_phase": "MANIP",
            "pools": [
                {
                    "level": 2415.0,
                    "liq_type": "WICK_CLUSTER",
                    "role": "PRIMARY",
                    "strength": 0.5,
                    "meta": {"side": "above"},
                }
            ],
            "magnets": [],
        },
        smc_zones={
            "active_zones": [
                {
                    "zone_id": "z_preview_only",
                    "zone_type": "OB",
                    "direction": "LONG",
                    "role": "PRIMARY",
                    "timeframe": "5m",
                    "price_min": 2408.0,
                    "price_max": 2410.0,
                }
            ]
        },
    )
    meta1 = _make_basic_meta(seq=1)
    st1 = build_viewer_state(asset_preview, meta1, fxcm_block=None, cache=cache)

    liq1 = cast(dict, st1["liquidity"])  # type: ignore[index]
    assert liq1.get("pools") == []
    raw1 = cast(dict, cast(dict, st1["zones"])["raw"])  # type: ignore[index]
    assert raw1.get("active_zones") == []

    # Тепер перший close з тими ж сутностями: все ще newborn -> сховано.
    asset_close = _make_basic_asset(
        smc_hint={
            "structure": {},
            "liquidity": {},
            "zones": {},
            "signals": [],
            "meta": {"smc_compute_kind": "close"},
        },
        smc_liquidity=asset_preview["smc_liquidity"],
        smc_zones=asset_preview["smc_zones"],
    )
    meta2 = _make_basic_meta(seq=2)
    st2 = build_viewer_state(asset_close, meta2, fxcm_block=None, cache=cache)
    liq2 = cast(dict, st2["liquidity"])  # type: ignore[index]
    assert liq2.get("pools") == []
    raw2 = cast(dict, cast(dict, st2["zones"])["raw"])  # type: ignore[index]
    assert raw2.get("active_zones") == []

    # Другий close: zones можуть стати видимими (age>=1), але pools все ще сховані (age<2).
    asset_close2 = _make_basic_asset(
        smc_hint={
            "structure": {},
            "liquidity": {},
            "zones": {},
            "signals": [],
            "meta": {"smc_compute_kind": "close"},
        },
        smc_liquidity=asset_preview["smc_liquidity"],
        smc_zones=asset_preview["smc_zones"],
    )
    meta3 = _make_basic_meta(seq=3)
    st3 = build_viewer_state(asset_close2, meta3, fxcm_block=None, cache=cache)
    liq3 = cast(dict, st3["liquidity"])  # type: ignore[index]
    assert liq3.get("pools") == []


def test_build_viewer_state_merges_overlapping_zones_into_stack() -> None:
    """Крок 5: zones overlap "килим" -> merge у presentation.

    Якщо дві зони однакового типу/напряму/ролі/TF мають високий IoU по діапазону,
    у viewer_state має лишитись одна канонічна зона з `meta.stack=N`.
    """

    asset = _make_basic_asset(
        smc_hint={
            "structure": {},
            "liquidity": {},
            "zones": {},
            "signals": [],
            "meta": {"smc_compute_kind": "close"},
        },
        smc_liquidity={"amd_phase": "MANIP", "pools": [], "magnets": []},
        smc_zones={
            "active_zones": [
                {
                    "zone_id": "z1",
                    "zone_type": "OB",
                    "direction": "LONG",
                    "role": "PRIMARY",
                    "timeframe": "5m",
                    "price_min": 100.0,
                    "price_max": 110.0,
                },
                {
                    "zone_id": "z2",
                    "zone_type": "OB",
                    "direction": "LONG",
                    "role": "PRIMARY",
                    "timeframe": "5m",
                    "price_min": 101.0,
                    "price_max": 109.0,
                },
            ]
        },
    )

    st = build_viewer_state(asset, _make_basic_meta(seq=1), fxcm_block=None, cache=None)
    zones = cast(dict, st["zones"])  # type: ignore[index]
    assert "zones_meta" in zones
    zones_meta = cast(dict, zones.get("zones_meta") or {})
    assert zones_meta.get("truth_count") == 2
    assert zones_meta.get("shown_count") == 1
    assert zones_meta.get("merged_clusters_count") == 1
    assert zones_meta.get("merged_away_count") == 1
    assert zones_meta.get("max_stack") == 2
    assert zones_meta.get("filtered_missing_bounds_count") == 0

    raw = cast(dict, zones["raw"])
    active = cast(list, raw.get("active_zones"))
    assert isinstance(active, list) and len(active) == 1
    z = cast(dict, active[0])
    assert z.get("price_min") == 100.0
    assert z.get("price_max") == 110.0
    meta = cast(dict, z.get("meta") or {})
    assert meta.get("stack") == 2


def test_build_viewer_state_zones_meta_no_merge_counts_are_zero() -> None:
    asset = _make_basic_asset(
        smc_hint={
            "structure": {},
            "liquidity": {},
            "zones": {},
            "signals": [],
            "meta": {"smc_compute_kind": "close"},
        },
        smc_liquidity={"amd_phase": "MANIP", "pools": [], "magnets": []},
        smc_zones={
            "active_zones": [
                {
                    "zone_id": "z1",
                    "zone_type": "OB",
                    "direction": "LONG",
                    "role": "PRIMARY",
                    "timeframe": "5m",
                    "price_min": 100.0,
                    "price_max": 110.0,
                },
                {
                    "zone_id": "z2",
                    "zone_type": "OB",
                    "direction": "LONG",
                    "role": "PRIMARY",
                    "timeframe": "5m",
                    "price_min": 120.0,
                    "price_max": 130.0,
                },
            ]
        },
    )

    st = build_viewer_state(asset, _make_basic_meta(seq=1), fxcm_block=None, cache=None)
    zones = cast(dict, st["zones"])  # type: ignore[index]
    zones_meta = cast(dict, zones.get("zones_meta") or {})
    assert zones_meta.get("truth_count") == 2
    assert zones_meta.get("shown_count") == 2
    assert zones_meta.get("merged_clusters_count") == 0
    assert zones_meta.get("merged_away_count") == 0
    assert zones_meta.get("max_stack") == 1


def test_build_viewer_state_marks_cap_evicted_pools_as_hidden_ttl() -> None:
    """Крок 2: cap-евікшн не має виглядати як «зникнення».

    Якщо pool був показаний (у топ-K), а на наступному close вилетів за MAX_POOLS,
    ми маємо позначити його як hidden (reason=evicted_cap) на TTL кроків.
    """

    def _make_pool(level: float) -> dict[str, Any]:
        return {
            "level": level,
            "liq_type": "WICK_CLUSTER",
            "role": "PRIMARY",
            "strength": 0.5,
            "n_touches": 1,
            "meta": {"side": "above", "cluster_id": f"cid_{level:.0f}"},
        }

    cache = ViewerStateCache()

    pools_all = [_make_pool(2400.0 + i) for i in range(10)]

    # Детермінований top-K у presentation: фіксуємо ranking через strength,
    # щоб на 3-му close показувались саме pools_all[0..7].
    for i in range(10):
        pools_all[i]["strength"] = 100.0 - float(i) if i < 8 else 0.0

    asset = _make_basic_asset(
        smc_hint={
            "structure": {},
            "liquidity": {},
            "zones": {},
            "signals": [],
            "meta": {"smc_compute_kind": "close"},
        },
        smc_liquidity={
            "amd_phase": "MANIP",
            "pools": pools_all,
            "magnets": [],
        },
        smc_zones={"active_zones": []},
    )

    # Доведемо pools до matured-only стадії: на 3-му close вони стають видимими.
    st1 = build_viewer_state(
        asset, _make_basic_meta(seq=1), fxcm_block=None, cache=cache
    )
    st2 = build_viewer_state(
        asset, _make_basic_meta(seq=2), fxcm_block=None, cache=cache
    )
    st3 = build_viewer_state(
        asset, _make_basic_meta(seq=3), fxcm_block=None, cache=cache
    )

    liq3 = cast(dict, st3["liquidity"])  # type: ignore[index]
    pools3 = liq3.get("pools")
    assert isinstance(pools3, list) and len(pools3) == 8
    meta3 = cast(dict, liq3.get("pools_meta") or {})
    assert meta3.get("hidden_count") == 0

    # Крок 4: selection pools у UI тепер детерміновано сортується за strength/n_touches,
    # тому для cap-евікшну робимо його через зміну truth-поля strength.
    # Піднімаємо strength для двох «хвостових» пулів, щоб вони витіснили 2 з top-K.
    # Очікуємо, що вилетять найслабші з показаних: pools_all[6] і pools_all[7].
    pools_all[8]["strength"] = 200.0
    pools_all[9]["strength"] = 199.0

    # Симулюємо «дотик під час hidden» для витіснених (колишніх top-K):
    pools_all[6]["n_touches"] = 2
    pools_all[7]["n_touches"] = 2

    pools_reordered = list(pools_all)
    asset4 = _make_basic_asset(
        smc_hint={
            "structure": {},
            "liquidity": {},
            "zones": {},
            "signals": [],
            "meta": {"smc_compute_kind": "close"},
        },
        smc_liquidity={
            "amd_phase": "MANIP",
            "pools": pools_reordered,
            "magnets": [],
        },
        smc_zones={"active_zones": []},
    )

    st4 = build_viewer_state(
        asset4, _make_basic_meta(seq=4), fxcm_block=None, cache=cache
    )
    liq4 = cast(dict, st4["liquidity"])  # type: ignore[index]
    meta4 = cast(dict, liq4.get("pools_meta") or {})
    assert meta4.get("hidden_count") == 2
    hidden_reasons = cast(dict, meta4.get("hidden_reasons") or {})
    assert hidden_reasons.get("evicted_cap") == 2

    assert meta4.get("touched_while_hidden_count") == 2
    touched_reasons = cast(dict, meta4.get("touched_while_hidden_reasons") or {})
    assert touched_reasons.get("evicted_cap") == 2
