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
    select_levels_for_tf_v1,
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


def test_build_viewer_state_levels_selected_v1_respects_caps_on_3_3c() -> None:
    """3.3c: `levels_selected_v1` має тримати caps по TF (без шуму)."""

    # Даємо OHLCV frames, щоб distance-gate мав ATR/DR і selected не був порожнім.
    replay_cursor_ms = 1_766_612_700_000
    base_ms = int(replay_cursor_ms - (25 * 5 * 60 * 1000))
    bars_5m: list[dict[str, Any]] = []
    for i in range(25):
        t = base_ms + i * (5 * 60 * 1000)
        # Конструкція з великим TR, щоб ATR був достатній для gate.
        bars_5m.append(
            {
                "time": int(t),
                "open": 2410.0,
                "high": 2420.0,
                "low": 2400.0,
                "close": 2412.0,
                "complete": True,
            }
        )

    bars_4h: list[dict[str, Any]] = [
        {
            "time": int(replay_cursor_ms - (4 * 60 * 60 * 1000)),
            "open": 2400.0,
            "high": 2500.0,
            "low": 2300.0,
            "close": 2410.0,
            "complete": True,
        }
    ]

    asset = _make_basic_asset(
        smc_liquidity={
            "amd_phase": "MANIP",
            "pools": [],
            "magnets": [
                {
                    "kind": "MAG",
                    "level": 0.0,
                    "meta": {"symbol": "xauusd"},
                    "pools": [
                        {"liq_type": "RANGE_EXTREME", "level": 2415.0},
                        {"liq_type": "RANGE_EXTREME", "level": 2400.0},
                        {
                            "liq_type": "EQH",
                            "source_swings": [
                                {"price": 2410.0},
                                {"price": 2412.0},
                            ],
                        },
                        {
                            "liq_type": "EQL",
                            "source_swings": [
                                {"price": 2405.0},
                                {"price": 2407.0},
                            ],
                        },
                    ],
                }
            ],
        }
    )

    asset["ohlcv_frames_by_tf"] = {"5m": bars_5m, "4h": bars_4h}
    meta = _make_basic_meta(replay_cursor_ms=replay_cursor_ms)

    state: SmcViewerState = build_viewer_state(asset, meta, fxcm_block=None, cache=None)

    selected = state.get("levels_selected_v1")

    assert isinstance(selected, list) and selected

    close_ts_expected = float(replay_cursor_ms) / 1000.0

    # Перевіряємо caps/rank/selected_at_close_ts.
    by_tf: dict[str, list[dict[str, Any]]] = {}
    for s in selected:
        assert isinstance(s, dict)
        tf = str(s.get("owner_tf") or "").lower()
        by_tf.setdefault(tf, []).append(s)

        reasons = s.get("reason")
        assert isinstance(reasons, list) and reasons
        assert float(s.get("selected_at_close_ts") or 0.0) == pytest.approx(
            close_ts_expected
        )

    assert "1m" not in by_tf or not by_tf.get("1m")

    def _counts(rows: list[dict[str, Any]]) -> tuple[int, int]:
        lines = 0
        bands = 0
        for r in rows:
            k = str(r.get("kind") or "").lower()
            if k == "line":
                lines += 1
            elif k == "band":
                bands += 1
        return lines, bands

    for tf, rows in by_tf.items():
        ranks = sorted([int(r.get("rank") or 0) for r in rows])
        assert ranks == list(range(1, len(rows) + 1)), f"tf={tf} ranks={ranks}"
        lines, bands = _counts(rows)
        if tf == "5m":
            assert lines <= 3
            assert bands <= 2
        if tf in {"1h", "4h"}:
            assert lines <= 6
            assert bands <= 2


def test_levels_selected_v1_distance_soft_allows_session_pair_3_3g() -> None:
    """3.3g: distance-гейт має бути м'яким, але детермінованим.

    Якщо SESSION пара (H+L) знаходиться поза hard-gate, але в межах soft-gate,
    вона має потрапити у selection і отримати reason `DISTANCE_SOFT_OK`.
    """

    # ATR(5m) ~= 1.0 (TR=1 на кожному барі), тоді:
    # hard=2.5*ATR=2.5, soft=4.0*ATR=4.0
    # Сесійну пару ставимо на відстані 3.0 (SOFT), RANGE на 1.0 (IN).
    ref_price = 100.0
    # Важливо: timestamp має потрапляти у LONDON (07–13 UTC), інакше active session буде ASIA/NY.
    close_ts = 1_700_036_000.0

    bars_5m: list[dict[str, Any]] = []
    base_ms = int(close_ts * 1000) - (20 * 5 * 60 * 1000)
    for i in range(20):
        t = base_ms + i * (5 * 60 * 1000)
        bars_5m.append(
            {
                "time": int(t),
                "open": 100.0,
                "high": 101.0,
                "low": 100.0,
                "close": 100.0,
                "complete": True,
            }
        )

    frames_by_tf = {"5m": bars_5m}

    merged: list[dict[str, Any]] = [
        {
            "id": "5m_lsh",
            "owner_tf": "5m",
            "kind": "line",
            "label": "LSH",
            "source": "SESSION",
            "price": ref_price + 3.0,
            "top": None,
            "bot": None,
        },
        {
            "id": "5m_lsl",
            "owner_tf": "5m",
            "kind": "line",
            "label": "LSL",
            "source": "SESSION",
            "price": ref_price - 3.0,
            "top": None,
            "bot": None,
        },
        {
            "id": "5m_rng_h",
            "owner_tf": "5m",
            "kind": "line",
            "label": "RANGE_H",
            "source": "RANGE",
            "price": ref_price + 1.0,
            "top": None,
            "bot": None,
        },
    ]

    selected = select_levels_for_tf_v1(
        "5m",
        cast(list, merged),
        ref_price,
        close_ts,
        frames_by_tf=frames_by_tf,
        symbol="XAUUSD",
    )

    by_id = {str(x.get("id") or ""): x for x in selected}
    assert "5m_lsh" in by_id
    assert "5m_lsl" in by_id

    r1 = set(cast(list, by_id["5m_lsh"].get("reason") or []))
    r2 = set(cast(list, by_id["5m_lsl"].get("reason") or []))
    assert "PINNED_SESSION_ACTIVE" in r1
    assert "PINNED_SESSION_ACTIVE" in r2
    assert "DISTANCE_SOFT_OK" in r1
    assert "DISTANCE_SOFT_OK" in r2


def test_build_viewer_state_levels_selected_v1_freezes_on_close_3_3d() -> None:
    """3.3d: на preview не змінюємо selected (freeze-on-close), на close — оновлюємо."""

    cache = ViewerStateCache()

    # Бар-рамки потрібні, щоб selection не був порожнім (distance-gate має ATR).
    replay_cursor_ms_1 = 1_766_612_700_000
    base_ms = int(replay_cursor_ms_1 - (25 * 5 * 60 * 1000))
    bars_5m: list[dict[str, Any]] = []
    for i in range(25):
        t = base_ms + i * (5 * 60 * 1000)
        bars_5m.append(
            {
                "time": int(t),
                "open": 2410.0,
                "high": 2420.0,
                "low": 2400.0,
                "close": 2412.0,
                "complete": True,
            }
        )

    bars_4h: list[dict[str, Any]] = [
        {
            "time": int(replay_cursor_ms_1 - (4 * 60 * 60 * 1000)),
            "open": 2400.0,
            "high": 2500.0,
            "low": 2300.0,
            "close": 2410.0,
            "complete": True,
        }
    ]

    # Close#1: базовий RANGE.
    asset_close_1 = _make_basic_asset(
        smc_hint={
            "structure": {},
            "liquidity": {},
            "zones": {},
            "signals": [],
            "meta": {"smc_compute_kind": "close"},
        },
        smc_liquidity={
            "amd_phase": "MANIP",
            "pools": [],
            "magnets": [
                {
                    "kind": "MAG",
                    "level": 0.0,
                    "meta": {"symbol": "xauusd"},
                    "pools": [
                        {"liq_type": "RANGE_EXTREME", "level": 2415.0},
                        {"liq_type": "RANGE_EXTREME", "level": 2400.0},
                    ],
                }
            ],
        },
    )
    asset_close_1["ohlcv_frames_by_tf"] = {"5m": bars_5m, "4h": bars_4h}
    meta_1 = _make_basic_meta(replay_cursor_ms=replay_cursor_ms_1, seq=1)

    state_close_1 = build_viewer_state(
        asset_close_1, meta_1, fxcm_block=None, cache=cache
    )
    selected_close_1 = state_close_1.get("levels_selected_v1")
    assert isinstance(selected_close_1, list) and selected_close_1
    close_ts_1 = float(replay_cursor_ms_1) / 1000.0
    assert float(
        selected_close_1[0].get("selected_at_close_ts") or 0.0
    ) == pytest.approx(close_ts_1)

    # Preview між close: змінюємо RANGE (якби не freeze, selection мав би змінитися).
    replay_cursor_ms_2 = replay_cursor_ms_1 + (5 * 60 * 1000)
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
            "pools": [],
            "magnets": [
                {
                    "kind": "MAG",
                    "level": 0.0,
                    "meta": {"symbol": "xauusd"},
                    "pools": [
                        {"liq_type": "RANGE_EXTREME", "level": 2420.0},
                        {"liq_type": "RANGE_EXTREME", "level": 2405.0},
                    ],
                }
            ],
        },
    )
    asset_preview["ohlcv_frames_by_tf"] = {"5m": bars_5m, "4h": bars_4h}
    meta_2 = _make_basic_meta(replay_cursor_ms=replay_cursor_ms_2, seq=2)

    state_preview = build_viewer_state(
        asset_preview, meta_2, fxcm_block=None, cache=cache
    )
    selected_preview = state_preview.get("levels_selected_v1")
    assert selected_preview == selected_close_1

    # Close#2: тепер маємо оновити selection і selected_at_close_ts.
    asset_close_2 = dict(asset_preview)
    asset_close_2["smc_hint"] = dict(asset_preview["smc_hint"])  # type: ignore[index]
    asset_close_2["smc_hint"]["meta"] = {"smc_compute_kind": "close"}  # type: ignore[index]

    state_close_2 = build_viewer_state(
        asset_close_2, meta_2, fxcm_block=None, cache=cache
    )
    selected_close_2 = state_close_2.get("levels_selected_v1")
    assert isinstance(selected_close_2, list) and selected_close_2
    assert selected_close_2 != selected_close_1
    close_ts_2 = float(replay_cursor_ms_2) / 1000.0
    assert float(
        selected_close_2[0].get("selected_at_close_ts") or 0.0
    ) == pytest.approx(close_ts_2)


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
