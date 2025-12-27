"""Побудова агрегованого SmcViewerState з UiSmcStatePayload.

Цей модуль не має залежностей від Rich/консолі й може використовуватися
як у консольному viewer'i, так і в HTTP/WS-серверах.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from config.config import (
    SMC_DAILY_START_HOUR_UTC,
    SMC_SESSION_WINDOWS_UTC,
    TICK_SIZE_BRACKETS,
    TICK_SIZE_DEFAULT,
    TICK_SIZE_MAP,
)
from core.contracts import (
    LevelLabelBandV1,
    LevelLabelLineV1,
    get_day_window_utc,
    get_prev_day_window_utc,
    get_session_window_utc,
    make_level_id_band_v1,
    make_level_id_line_v1,
)
from core.contracts.viewer_state import (
    VIEWER_STATE_SCHEMA_VERSION,
    FxcmMeta,
    LevelCandidateV1,
    LevelSelectedV1,
    LevelViewShadowV1,
    SmcViewerLiquidity,
    SmcViewerPipelineLocal,
    SmcViewerScenario,
    SmcViewerState,
    SmcViewerStructure,
    SmcViewerZones,
    UiSmcAssetPayload,
    UiSmcMeta,
)
from core.serialization import (
    coerce_dict,
    iso_z_to_dt,
    safe_float,
    safe_int,
    try_iso_to_human_utc,
    utc_ms_to_human_utc,
)


class NoSymbolError(ValueError):
    """Виняток: у payload відсутній `symbol` (контракт viewer_state порушено)."""


# Обмеження розмірів списків у viewer_state, щоб state залишався легковаговим.
MAX_EVENTS: int = 20
MAX_LEGS: int = 6
MAX_SWINGS: int = 6
MAX_RANGES: int = 5
MAX_OTE_ZONES: int = 6
MAX_POOLS: int = 8
MAX_LIQUIDITY_TARGETS: int = 6
MAX_EXECUTION_EVENTS: int = 12


# Levels-V1 / 3.3c0: SSOT таблиця політик selection по TF (caps + distance + пріоритети).
# Важливо: це policy саме для `levels_selected_v1` (UI ще не робить cutover до 3.3d).
LEVELS_SELECTED_POLICY_V1: dict[str, dict[str, Any]] = {
    # TF=4h/1h (фон)
    "4h": {
        "caps": {"lines_total": 6, "bands_total": 2},
        "always_labels": ["PDH", "PDL"],
        "optional_labels": ["EDH", "EDL"],
        "range_labels": ["RANGE_H", "RANGE_L"],
        "eq_band_labels": ["EQH", "EQL"],
        # 3.3g: distance-гейт "як у трейдера" (soft, але детермінований):
        # - hard: відсікаємо "дуже далеко";
        # - soft: дозволяємо як слабкий кандидат (може потрапити у слот, якщо є бюджет).
        "distance_gate": {
            "mode": "dr4h_or_atr5m",
            "dr4h_mult": 1.5,
            "atr5m_mult": 6.0,
            "soft_dr4h_mult": 2.0,
            "soft_atr5m_mult": 8.0,
        },
    },
    "1h": {
        "caps": {"lines_total": 6, "bands_total": 2},
        "always_labels": ["PDH", "PDL"],
        "optional_labels": ["EDH", "EDL"],
        "range_labels": ["RANGE_H", "RANGE_L"],
        "eq_band_labels": ["EQH", "EQL"],
        "distance_gate": {
            "mode": "dr4h_or_atr5m",
            "dr4h_mult": 1.5,
            "atr5m_mult": 6.0,
            "soft_dr4h_mult": 2.0,
            "soft_atr5m_mult": 8.0,
        },
    },
    # TF=5m (structure)
    "5m": {
        "caps": {"lines_total": 3, "bands_total": 2},
        "always_labels": ["PDH", "PDL"],
        "relevant_priority": ["ACTIVE_SESSION", "ED", "RANGE"],
        "range_labels": ["RANGE_H", "RANGE_L"],
        "eq_band_labels": ["EQH", "EQL"],
        "distance_gate": {"mode": "atr5m", "atr5m_mult": 2.5, "soft_atr5m_mult": 4.0},
    },
    # TF=1m (exec) — selected поки вимкнено (1m використовує selected 5m/HTF у UI пізніше).
    "1m": {
        "caps": {"lines_total": 0, "bands_total": 0},
        "disabled": True,
    },
}

# Випадок C (QA): не показуємо «новонароджене», доки не пройде мінімум N close-кроків.
# N=1 означає: створилось на close N, показуємо починаючи з close N+1.
#
# Важливо: pools — головний шумогенератор (фліккер). Тому для pools робимо
# жорсткіше matured-only за замовчуванням: age>=2 close-кроки.
MIN_CLOSE_STEPS_BEFORE_SHOW_ZONES: int = 1
MIN_CLOSE_STEPS_BEFORE_SHOW_POOLS: int = 2

# Крок 5 (QA): zones стабільні між preview/close, але мають сильний overlap.
# Робимо dedup/merge у presentation-layer за IoU по price-range.
ZONES_MERGE_IOU_THRESHOLD: float = 0.75

# Крок 2 (QA): якщо pool «вилетів» через cap/top-K, не вважаємо це «зникненням».
# Тримаємо його як hidden у кеші ще N close-кроків, щоб UI міг пояснити причину.
POOLS_HIDDEN_TTL_CLOSE_STEPS: int = 8


@dataclass
class ViewerStateCache:
    """Невеликий кеш для бекфілу подій/зон/FXCM-стану.

    Це дозволяє уникати «мигання» UI, коли в новому пейлоаді немає
    свіжих подій або зон, але їхній попередній стан іще актуальний.
    """

    last_events: list[dict[str, Any]] = field(default_factory=list)
    last_execution_events: list[dict[str, Any]] = field(default_factory=list)
    last_zones_raw: dict[str, Any] = field(default_factory=dict)
    last_fxcm_meta: FxcmMeta | None = None

    # QA/UI стабілізація: лічильник close-кроків та "вік" сутностей.
    close_step: int = 0
    born_step_by_key: dict[str, int] = field(default_factory=dict)

    # QA/UI стабілізація: пояснюємо cap-евікшн (не «пропало», а «приховано»).
    pools_last_shown_keys: set[str] = field(default_factory=set)
    pools_hidden_until_step_by_key: dict[str, int] = field(default_factory=dict)
    pools_hidden_reason_by_key: dict[str, str] = field(default_factory=dict)

    # QA/UI стабілізація: touch маркери — строго з truth (n_touches/last_time).
    pools_last_touch_sig_by_key: dict[str, tuple[int, int | None]] = field(
        default_factory=dict
    )

    # Levels-V1 / 3.3d: freeze-on-close для selected.
    # - На close оновлюємо selection.
    # - На preview віддаємо попередній close-стан (щоб не було preview-vs-close фліккеру).
    last_levels_selected_v1: list[LevelSelectedV1] = field(default_factory=list)


def build_viewer_state(
    asset: UiSmcAssetPayload,
    payload_meta: UiSmcMeta,
    *,
    fxcm_block: FxcmMeta | None = None,
    cache: ViewerStateCache | None = None,
) -> SmcViewerState:
    """Формує SmcViewerState для одного активу.

    Припущення:
    - asset вже пройшов через publish_smc_state.py (price / *_str готові);
    - smc_hint plain (див. smc_hint_contract.md) або дубльований у smc_* полях;
    - fxcm_block (якщо передано) має пріоритет над payload_meta["fxcm"].
    """

    asset_dict: dict[str, Any] = dict(asset)
    payload_meta_dict: dict[str, Any] = coerce_dict(payload_meta)

    smc_hint = coerce_dict(asset_dict.get("smc_hint"))
    smc_structure = coerce_dict(
        asset_dict.get("smc_structure") or smc_hint.get("structure")
    )
    smc_liquidity = coerce_dict(
        asset_dict.get("smc_liquidity") or smc_hint.get("liquidity")
    )
    smc_zones = coerce_dict(asset_dict.get("smc_zones") or smc_hint.get("zones"))
    smc_execution = coerce_dict(
        asset_dict.get("smc_execution") or smc_hint.get("execution")
    )

    smc_hint_meta = coerce_dict(smc_hint.get("meta"))
    tf_health = smc_hint_meta.get("tf_health")

    compute_kind = str(smc_hint_meta.get("smc_compute_kind") or "")
    is_preview = compute_kind.lower() == "preview"

    if cache is not None and not is_preview:
        cache.close_step = int(cache.close_step) + 1

    tf_plan = coerce_dict(smc_hint_meta.get("tf_plan"))
    tf_effective_any = smc_hint_meta.get("tf_effective")
    gates_any = smc_hint_meta.get("gates")

    history_state_any = smc_hint_meta.get("history_state")
    age_ms_any = smc_hint_meta.get("age_ms")
    last_open_time_ms_any = smc_hint_meta.get("last_open_time_ms")
    last_ts_any = smc_hint_meta.get("last_ts")
    lag_ms_any = smc_hint_meta.get("lag_ms")
    bars_5m_any = smc_hint_meta.get("bars_5m")

    stats = coerce_dict(asset_dict.get("stats"))

    scenario_block: SmcViewerScenario | None = None
    if stats:
        scenario_id = stats.get("scenario_id")
        if isinstance(scenario_id, str) and scenario_id:
            scenario_block = cast(
                SmcViewerScenario,
                {
                    "scenario_id": str(scenario_id),
                    "direction": str(stats.get("scenario_direction") or "NEUTRAL"),
                    "confidence": safe_float(stats.get("scenario_confidence")) or 0.0,
                    "why": (
                        [str(v) for v in (stats.get("scenario_why") or [])][:5]
                        if isinstance(stats.get("scenario_why"), list)
                        else []
                    ),
                    "key_levels": coerce_dict(stats.get("scenario_key_levels")),
                    "last_change_ts": stats.get("scenario_last_change_ts"),
                    "unclear_reason": (
                        str(stats.get("scenario_unclear_reason"))
                        if stats.get("scenario_unclear_reason") is not None
                        else None
                    ),
                    "raw_scenario_id": stats.get("scenario_raw_id"),
                    "raw_direction": stats.get("scenario_raw_direction"),
                    "raw_confidence": safe_float(stats.get("scenario_raw_confidence")),
                    "raw_why": (
                        [str(v) for v in (stats.get("scenario_raw_why") or [])][:5]
                        if isinstance(stats.get("scenario_raw_why"), list)
                        else []
                    ),
                    "raw_key_levels": coerce_dict(stats.get("scenario_raw_key_levels")),
                    "raw_inputs_ok": (
                        bool(stats.get("scenario_raw_inputs_ok"))
                        if stats.get("scenario_raw_inputs_ok") is not None
                        else None
                    ),
                    "raw_gates": (
                        stats.get("scenario_raw_gates")
                        if isinstance(stats.get("scenario_raw_gates"), list)
                        else []
                    ),
                    "raw_unclear_reason": (
                        str(stats.get("scenario_raw_unclear_reason"))
                        if stats.get("scenario_raw_unclear_reason") is not None
                        else None
                    ),
                    "pending_id": stats.get("scenario_pending_id"),
                    "pending_count": safe_int(stats.get("scenario_pending_count")) or 0,
                    "ttl_sec": safe_int(stats.get("scenario_state_ttl_sec")) or 0,
                    "confirm_bars": safe_int(stats.get("scenario_confirm_bars")) or 0,
                    "switch_delta": safe_float(stats.get("scenario_switch_delta"))
                    or 0.0,
                    "anti_flip": coerce_dict(stats.get("scenario_anti_flip")),
                    "last_eval": coerce_dict(stats.get("scenario_last_eval")),
                },
            )

    pipeline_local: SmcViewerPipelineLocal = {}
    if stats:
        state_local = stats.get("pipeline_state_local")
        ready_bars = stats.get("pipeline_ready_bars")
        required_bars = stats.get("pipeline_required_bars")
        ready_ratio = stats.get("pipeline_ready_ratio")

        if state_local is not None:
            pipeline_local["state"] = str(state_local)
        if isinstance(ready_bars, (int, float)):
            pipeline_local["ready_bars"] = int(ready_bars)
        if isinstance(required_bars, (int, float)):
            pipeline_local["required_bars"] = int(required_bars)
        if isinstance(ready_ratio, (int, float)):
            pipeline_local["ready_ratio"] = float(ready_ratio)

    price_value = _extract_price(asset_dict, stats)

    raw_events = _simplify_events(smc_structure.get("events"))
    events = _persist_events(raw_events, cache)

    raw_exec_events = _simplify_execution_events(smc_execution.get("execution_events"))
    exec_events = _persist_execution_events(raw_exec_events, cache)

    smc_zones_filtered = _filter_newborn_zones(
        smc_zones,
        cache=cache,
        is_preview=is_preview,
        min_close_steps_before_show=MIN_CLOSE_STEPS_BEFORE_SHOW_ZONES,
    )
    smc_zones_merged = _merge_overlapping_zones(
        smc_zones_filtered, iou_threshold=ZONES_MERGE_IOU_THRESHOLD
    )
    zones_raw = _persist_zones(smc_zones_merged, cache)

    # Zones meta (non-breaking): truth vs shown counters + merge policy.
    truth_active_any = (
        smc_zones_filtered.get("active_zones")
        if isinstance(smc_zones_filtered, dict)
        else None
    )
    truth_active: list[dict[str, Any]] = (
        [z for z in truth_active_any if isinstance(z, dict)]
        if isinstance(truth_active_any, list)
        else []
    )
    filtered_missing_bounds_count = 0
    for z in truth_active:
        if (
            safe_float(z.get("price_min")) is None
            or safe_float(z.get("price_max")) is None
        ):
            filtered_missing_bounds_count += 1

    shown_active_any = (
        zones_raw.get("active_zones") if isinstance(zones_raw, dict) else None
    )
    shown_active: list[dict[str, Any]] = (
        [z for z in shown_active_any if isinstance(z, dict)]
        if isinstance(shown_active_any, list)
        else []
    )

    max_stack = 0
    merged_clusters_count = 0
    for z in shown_active:
        meta = z.get("meta") if isinstance(z.get("meta"), dict) else {}
        stack = safe_int((meta or {}).get("stack")) or 1
        if stack > 1:
            merged_clusters_count += 1
        if stack > max_stack:
            max_stack = int(stack)
    if not shown_active:
        max_stack = 0

    truth_count = len(truth_active)
    shown_count = len(shown_active)
    dropped_by_cap_count = 0
    merged_away_count = max(0, truth_count - shown_count)

    zones_meta: dict[str, Any] = {
        "truth_count": int(truth_count),
        "shown_count": int(shown_count),
        "merged_clusters_count": int(merged_clusters_count),
        "merged_away_count": int(merged_away_count),
        "max_stack": int(max_stack),
        "dropped_by_cap_count": int(dropped_by_cap_count),
        "filtered_missing_bounds_count": int(filtered_missing_bounds_count),
        "policy": {
            "scope_key": "active_zones",
            "merge_iou_threshold": float(ZONES_MERGE_IOU_THRESHOLD),
            "max_zones_shown": None,
            "min_close_steps_before_show": int(MIN_CLOSE_STEPS_BEFORE_SHOW_ZONES),
        },
    }

    session_value = _resolve_session(asset_dict, stats)

    fxcm_source = _resolve_fxcm_source(payload_meta_dict, fxcm_block, cache)
    if fxcm_source is not None and cache is not None:
        cache.last_fxcm_meta = fxcm_source

    # Якщо FXCM знає поточну сесію, використовуємо її як більш надійне джерело.
    fxcm_session = fxcm_source.get("session") if isinstance(fxcm_source, dict) else None
    if isinstance(fxcm_session, dict):
        tag = fxcm_session.get("tag") or fxcm_session.get("name")
        if tag:
            session_value = str(tag)

    meta_snapshot: UiSmcMeta = cast(UiSmcMeta, dict(payload_meta_dict))
    if fxcm_source is not None:
        meta_snapshot["fxcm"] = fxcm_source

    structure_block: SmcViewerStructure = cast(
        SmcViewerStructure,
        {
            "trend": smc_structure.get("trend"),
            "bias": smc_structure.get("bias"),
            "range_state": smc_structure.get("range_state"),
            "legs": _simplify_legs(smc_structure.get("legs")),
            "swings": _simplify_swings(smc_structure.get("swings")),
            "ranges": _simplify_ranges(smc_structure.get("ranges")),
            "events": events,
            "ote_zones": _simplify_otes(smc_structure.get("ote_zones")),
        },
    )

    pools_truth_any = smc_liquidity.get("pools")
    pools_truth: list[dict[str, Any]] = (
        [p for p in pools_truth_any if isinstance(p, dict)]
        if isinstance(pools_truth_any, list)
        else []
    )

    pools_filtered_newborn_count = 0
    pools_filtered_preview_count = 0

    # Pools presentation policy (QA ROI): close-only + matured-only.
    # - preview pools не показуємо взагалі (щоб прибрати preview-vs-close фліккер)
    # - close pools показуємо лише якщо age>=2 close-кроки
    if is_preview:
        pools_for_presentation: list[dict[str, Any]] = []
        pools_filtered_preview_count = len(pools_truth)
    else:
        pools_for_presentation, pools_filtered_newborn_count = _filter_newborn_pools(
            pools_truth,
            cache=cache,
            is_preview=is_preview,
            min_close_steps_before_show=MIN_CLOSE_STEPS_BEFORE_SHOW_POOLS,
        )

    # Крок 4 (QA): стабільний (детермінований) top-K у presentation.
    # Це зменшує фліккер, коли truth повертає pools у нестабільному порядку.
    pools_ranked = list(pools_for_presentation)
    if pools_ranked:
        pools_ranked.sort(
            key=lambda p: (
                float(safe_float(p.get("strength")) or 0.0),
                int(safe_int(p.get("n_touches")) or 0),
                _pool_key(p),
            ),
            reverse=True,
        )

    pools_visible_raw = pools_ranked[:MAX_POOLS]
    pools_simplified = _simplify_pools(pools_visible_raw)
    pools_dropped_by_cap_count = max(0, len(pools_ranked) - len(pools_visible_raw))

    pools_hidden_count = 0
    pools_hidden_reasons: dict[str, int] = {}
    pools_touched_while_hidden_count = 0
    pools_touched_while_hidden_reasons: dict[str, int] = {}

    # Крок 2: якщо pool «вилетів» із топ-K (cap), маркуємо як hidden з TTL.
    # Важливо: це лише presentation-логіка, truth не змінюється.
    if cache is not None and not is_preview:
        close_step = int(cache.close_step or 0)
        current_keys: set[str] = set()
        shown_keys: set[str] = set()
        beyond_cap_keys: set[str] = set()

        for p in pools_ranked:
            if not isinstance(p, dict):
                continue
            current_keys.add(_pool_key(p))

        for p in pools_visible_raw:
            if not isinstance(p, dict):
                continue
            shown_keys.add(_pool_key(p))

        for p in pools_ranked[MAX_POOLS:]:
            if not isinstance(p, dict):
                continue
            beyond_cap_keys.add(_pool_key(p))

        # Додаємо до hidden тільки ті, що були показані раніше і тепер вилетіли через cap.
        evicted_cap_keys = beyond_cap_keys.intersection(cache.pools_last_shown_keys)
        if evicted_cap_keys:
            until = close_step + int(POOLS_HIDDEN_TTL_CLOSE_STEPS)
            for key in evicted_cap_keys:
                prev_until = cache.pools_hidden_until_step_by_key.get(key)
                if prev_until is None or int(prev_until) < until:
                    cache.pools_hidden_until_step_by_key[key] = int(until)
                cache.pools_hidden_reason_by_key[key] = "evicted_cap"

        # Якщо pool знову показаний — прибираємо його з hidden.
        for key in list(cache.pools_hidden_until_step_by_key.keys()):
            if key in shown_keys:
                cache.pools_hidden_until_step_by_key.pop(key, None)
                cache.pools_hidden_reason_by_key.pop(key, None)

        # Експайримо hidden за TTL або якщо pool більше не присутній у truth.
        for key, until_step in list(cache.pools_hidden_until_step_by_key.items()):
            if key not in current_keys or int(until_step) < close_step:
                cache.pools_hidden_until_step_by_key.pop(key, None)
                cache.pools_hidden_reason_by_key.pop(key, None)

        # Оновлюємо показані ключі для наступного кроку.
        cache.pools_last_shown_keys = set(shown_keys)

        pools_hidden_count = len(cache.pools_hidden_until_step_by_key)
        for reason in cache.pools_hidden_reason_by_key.values():
            pools_hidden_reasons[reason] = int(pools_hidden_reasons.get(reason, 0)) + 1

        def _pool_touch_sig(pool: dict[str, Any]) -> tuple[int, int | None]:
            n_touches = safe_int(pool.get("n_touches")) or 0
            last_time = pool.get("last_time")
            if isinstance(last_time, str) and last_time.strip():
                try:
                    dt = iso_z_to_dt(last_time)
                    return int(n_touches), int(dt.timestamp() * 1000)
                except (TypeError, ValueError):
                    return int(n_touches), None
            return int(n_touches), None

        current_touch_keys: set[str] = set()
        for p in pools_truth:
            if not isinstance(p, dict):
                continue
            key = _pool_key(p)
            current_touch_keys.add(key)
            sig = _pool_touch_sig(p)
            prev = cache.pools_last_touch_sig_by_key.get(key)
            touched_now = False
            if prev is not None:
                prev_n, prev_last_ms = prev
                cur_n, cur_last_ms = sig
                if cur_n > int(prev_n):
                    touched_now = True
                elif (
                    cur_last_ms is not None
                    and prev_last_ms is not None
                    and int(cur_last_ms) > int(prev_last_ms)
                ):
                    touched_now = True

            if touched_now and key in cache.pools_hidden_until_step_by_key:
                reason = cache.pools_hidden_reason_by_key.get(key) or "unknown"
                pools_touched_while_hidden_count += 1
                pools_touched_while_hidden_reasons[reason] = (
                    int(pools_touched_while_hidden_reasons.get(reason, 0)) + 1
                )

            cache.pools_last_touch_sig_by_key[key] = sig

        # Прибираємо старі ключі, щоб кеш не ріс безконтрольно.
        for key in list(cache.pools_last_touch_sig_by_key.keys()):
            if key not in current_touch_keys:
                cache.pools_last_touch_sig_by_key.pop(key, None)

    liquidity_block: SmcViewerLiquidity = cast(
        SmcViewerLiquidity,
        {
            "amd_phase": smc_liquidity.get("amd_phase"),
            "pools": pools_simplified,
            "pools_meta": {
                "truth_count": len(pools_truth),
                "shown_count": len(pools_simplified),
                "filtered_preview_count": int(pools_filtered_preview_count),
                "filtered_newborn_count": int(pools_filtered_newborn_count),
                "dropped_by_cap_count": int(pools_dropped_by_cap_count),
                "hidden_count": int(pools_hidden_count),
                "hidden_reasons": dict(pools_hidden_reasons),
                "touched_while_hidden_count": int(pools_touched_while_hidden_count),
                "touched_while_hidden_reasons": dict(
                    pools_touched_while_hidden_reasons
                ),
                "policy": {
                    "mode": "close_only+matured",
                    "min_close_steps_before_show": int(
                        MIN_CLOSE_STEPS_BEFORE_SHOW_POOLS
                    ),
                    "hidden_ttl_close_steps": int(POOLS_HIDDEN_TTL_CLOSE_STEPS),
                },
            },
            # Магніти поки передаємо «як є», без додаткової агрегації.
            "magnets": smc_liquidity.get("magnets") or [],
            "targets": _simplify_liquidity_targets(
                coerce_dict(smc_liquidity.get("meta")).get("liquidity_targets")
            ),
        },
    )

    # Levels-V1 / Крок 2 (shadow): канонічна форма рівня, 1:1 з поточною UI-проєкцією.
    # ВАЖЛИВО: UI це поле поки не використовує.
    levels_shadow_v1 = _build_levels_shadow_v1(
        pools=pools_simplified,
        ref_price=price_value,
        asof_ts=(str(payload_meta_dict.get("ts") or "") if payload_meta_dict else ""),
    )

    # Levels-V1 / Крок 3.2 (candidates): переносимо “правду” в presentation шар.
    # ВАЖЛИВО: UI поки не використовує ці кандидати напряму.
    ohlcv_frames_any = asset_dict.get("ohlcv_frames_by_tf")
    frames_by_tf: dict[str, list[dict[str, Any]]] | None = None
    if isinstance(ohlcv_frames_any, dict):
        frames_by_tf = {}
        for tf_key, bars_any in ohlcv_frames_any.items():
            if not isinstance(tf_key, str):
                continue
            if not isinstance(bars_any, list):
                continue
            frames_by_tf[tf_key] = [b for b in bars_any if isinstance(b, dict)]

    levels_candidates_v1 = _build_levels_candidates_v1(
        symbol=str(asset_dict.get("symbol") or ""),
        asset_or_state=asset_dict,
        liquidity=smc_liquidity,
        payload_meta=payload_meta_dict,
        ref_price=price_value,
        pools=pools_simplified,
        frames_by_tf=frames_by_tf,
    )

    # Levels-V1 / Крок 3.3 (selected): SSOT selection.
    # ВАЖЛИВО: UI поки не використовує (cutover заборонено до 3.3d).
    if cache is not None and is_preview:
        levels_selected_v1 = list(cache.last_levels_selected_v1)
    else:
        levels_selected_v1 = _build_levels_selected_v1(
            symbol=str(asset_dict.get("symbol") or ""),
            payload_meta=payload_meta_dict,
            ref_price=price_value,
            candidates_v1=levels_candidates_v1,
            frames_by_tf=frames_by_tf,
        )
        if cache is not None:
            cache.last_levels_selected_v1 = list(levels_selected_v1)

    zones_block: SmcViewerZones = cast(
        SmcViewerZones, {"raw": zones_raw, "zones_meta": zones_meta}
    )

    execution_block: dict[str, Any] = {}
    if smc_execution:
        # Передаємо як lightweight extension: meta + персист останніх подій.
        meta_exec = coerce_dict(smc_execution.get("meta"))
        if meta_exec:
            execution_block["meta"] = meta_exec
        if exec_events:
            execution_block["execution_events"] = exec_events

    symbol_value = asset_dict.get("symbol")
    if symbol_value is None or not str(symbol_value).strip():
        raise NoSymbolError("NO_SYMBOL")
    symbol_norm: str = str(symbol_value).upper()

    viewer_state: SmcViewerState = cast(
        SmcViewerState,
        {
            "symbol": symbol_norm,
            "payload_ts": payload_meta_dict.get("ts"),
            "payload_seq": payload_meta_dict.get("seq"),
            "schema": VIEWER_STATE_SCHEMA_VERSION,
            "meta": meta_snapshot,
            "price": price_value,
            "session": session_value,
            "structure": structure_block,
            "liquidity": liquidity_block,
            "zones": zones_block,
            **({"levels_shadow_v1": levels_shadow_v1} if levels_shadow_v1 else {}),
            **(
                {"levels_candidates_v1": levels_candidates_v1}
                if levels_candidates_v1
                else {}
            ),
            **(
                {"levels_selected_v1": levels_selected_v1} if levels_selected_v1 else {}
            ),
            **({"execution": execution_block} if execution_block else {}),
            "pipeline_local": pipeline_local,
        },
    )

    if scenario_block is not None:
        viewer_state["scenario"] = scenario_block

    if isinstance(tf_health, dict) and tf_health:
        viewer_state["tf_health"] = tf_health

    if tf_plan:
        viewer_state["tf_plan"] = tf_plan
    if isinstance(tf_effective_any, list) and tf_effective_any:
        viewer_state["tf_effective"] = [
            str(v) for v in tf_effective_any if v is not None
        ]
    if isinstance(gates_any, list):
        viewer_state["gates"] = [g for g in gates_any if isinstance(g, dict)]

    if history_state_any is not None:
        viewer_state["history_state"] = str(history_state_any)
    age_ms_value = safe_int(age_ms_any)
    if age_ms_value is not None:
        viewer_state["age_ms"] = age_ms_value
    last_open_ms_value = safe_int(last_open_time_ms_any)
    if last_open_ms_value is not None:
        viewer_state["last_open_time_ms"] = last_open_ms_value
    if isinstance(last_ts_any, str) and last_ts_any:
        viewer_state["last_ts"] = last_ts_any
    lag_ms_value = safe_int(lag_ms_any)
    if lag_ms_value is not None:
        viewer_state["lag_ms"] = lag_ms_value
    bars_5m_value = safe_int(bars_5m_any)
    if bars_5m_value is not None:
        viewer_state["bars_5m"] = bars_5m_value

    if fxcm_source is not None:
        viewer_state["fxcm"] = fxcm_source

    return viewer_state


# ── Допоміжні функції --------------------------------------------------------


def _format_utc_from_ms(value: Any) -> str | None:
    millis = safe_int(value)
    if millis is None:
        return None
    return utc_ms_to_human_utc(millis)


def extract_range_from_liquidity_magnets_v1(
    liquidity: Any,
) -> tuple[float, float] | None:
    """3.2.4b1: канонічно дістає (high, low) з liquidity.magnets[*].pools.

    Джерело правди: всі pool.level, де pool.liq_type == RANGE_EXTREME.
    Без будь-яких fallback-джерел (key_levels/context/htf_lite).
    """

    if not isinstance(liquidity, dict):
        return None

    magnets_any = liquidity.get("magnets")
    if not isinstance(magnets_any, list):
        return None

    levels: list[float] = []
    symbol_guess: str | None = None

    for m in magnets_any:
        if not isinstance(m, dict):
            continue

        if symbol_guess is None:
            meta_any = m.get("meta")
            if isinstance(meta_any, dict):
                sym = meta_any.get("symbol")
                if isinstance(sym, str) and sym.strip():
                    symbol_guess = sym.strip().lower()

        pools_any = m.get("pools")
        if not isinstance(pools_any, list):
            continue

        for p in pools_any:
            if not isinstance(p, dict):
                continue
            if str(p.get("liq_type") or "").strip().upper() != "RANGE_EXTREME":
                continue
            lvl = safe_float(p.get("level"), finite=True)
            if lvl is None:
                continue
            levels.append(float(lvl))

    if not levels:
        return None

    tick = None
    if symbol_guess is not None:
        tick_any = dict(TICK_SIZE_MAP).get(symbol_guess)
        if isinstance(tick_any, (int, float)) and float(tick_any) > 0:
            tick = float(tick_any)

    if tick is None:
        abs_ref = max(abs(float(x)) for x in levels)
        tick = float(TICK_SIZE_DEFAULT)
        for threshold, step in list(TICK_SIZE_BRACKETS):
            try:
                if abs_ref < float(threshold):
                    tick = float(step)
                    break
            except (TypeError, ValueError):
                continue

    tol_abs = max(1e-9, float(tick) * 0.5)

    levels_sorted = sorted(levels)
    unique_levels: list[float] = []
    for v in levels_sorted:
        if not unique_levels:
            unique_levels.append(float(v))
            continue
        if abs(float(v) - float(unique_levels[-1])) <= tol_abs:
            continue
        unique_levels.append(float(v))

    if len(unique_levels) < 2:
        return None

    low = float(min(unique_levels))
    high = float(max(unique_levels))
    if not (high > low):
        return None

    return high, low


def extend_range_candidates_v1(
    *,
    candidates: list[LevelCandidateV1],
    symbol: str | None,
    asof_ts: float,
    liquidity: Any,
) -> None:
    """3.2.4b1: додає RANGE кандидати (0 або 6 записів) з liquidity magnets."""

    got = extract_range_from_liquidity_magnets_v1(liquidity)
    if got is None:
        return

    rh, rl = got
    if not (rh > rl):
        return

    window_ts = None

    label_high: LevelLabelLineV1 = "RANGE_H"
    label_low: LevelLabelLineV1 = "RANGE_L"
    for owner_tf in ("5m", "1h", "4h"):
        candidates.append(
            {
                "id": make_level_id_line_v1(
                    tf=owner_tf,  # type: ignore[arg-type]
                    label=label_high,
                    price=float(rh),
                    symbol=str(symbol or "") or None,
                ),
                "owner_tf": owner_tf,  # type: ignore[typeddict-item]
                "kind": "line",
                "label": label_high,
                "source": "RANGE",
                "price": float(rh),
                "top": None,
                "bot": None,
                "asof_ts": float(asof_ts),
                "window_ts": window_ts,
            }
        )
        candidates.append(
            {
                "id": make_level_id_line_v1(
                    tf=owner_tf,  # type: ignore[arg-type]
                    label=label_low,
                    price=float(rl),
                    symbol=str(symbol or "") or None,
                ),
                "owner_tf": owner_tf,  # type: ignore[typeddict-item]
                "kind": "line",
                "label": label_low,
                "source": "RANGE",
                "price": float(rl),
                "top": None,
                "bot": None,
                "asof_ts": float(asof_ts),
                "window_ts": window_ts,
            }
        )


def extract_eq_bands_from_liquidity_magnets_v1(
    liquidity: Any,
) -> dict[str, tuple[float, float]] | None:
    """3.2.5b: канонічно дістає EQH/EQL bands (top, bot) з liquidity.magnets[*].pools.

    Джерело правди:
    - pool.liq_type in {EQH, EQL}
    - межі band відновлюємо з `pool.source_swings[*].price` як (max, min)

    Anti-fake policy:
    - якщо немає достатньо swing-цін (>=2 унікальні з tick-tolerance) — band НЕ емімо.
    """

    if not isinstance(liquidity, dict):
        return None

    magnets_any = liquidity.get("magnets")
    if not isinstance(magnets_any, list):
        return None

    swing_prices_by_label: dict[str, list[float]] = {"EQH": [], "EQL": []}
    symbol_guess: str | None = None

    for m in magnets_any:
        if not isinstance(m, dict):
            continue

        if symbol_guess is None:
            meta_any = m.get("meta")
            if isinstance(meta_any, dict):
                sym = meta_any.get("symbol")
                if isinstance(sym, str) and sym.strip():
                    symbol_guess = sym.strip().lower()

        pools_any = m.get("pools")
        if not isinstance(pools_any, list):
            continue

        for p in pools_any:
            if not isinstance(p, dict):
                continue
            liq_type = str(p.get("liq_type") or "").strip().upper()
            if liq_type not in {"EQH", "EQL"}:
                continue

            # Пріоритет: source_swings[*].price як truth-ширина.
            swings_any = p.get("source_swings")
            if not isinstance(swings_any, list):
                continue
            for s in swings_any:
                if not isinstance(s, dict):
                    continue
                px = safe_float(s.get("price"), finite=True)
                if px is None:
                    continue
                swing_prices_by_label[liq_type].append(float(px))

    # Якщо немає даних хоча б по одному з лейблів — не емімо частково.
    if not swing_prices_by_label["EQH"] or not swing_prices_by_label["EQL"]:
        return None

    # tick-size (для dedup і мінімальної ширини)
    tick = None
    if symbol_guess is not None:
        tick_any = dict(TICK_SIZE_MAP).get(symbol_guess)
        if isinstance(tick_any, (int, float)) and float(tick_any) > 0:
            tick = float(tick_any)

    if tick is None:
        abs_ref = 0.0
        for arr in swing_prices_by_label.values():
            if arr:
                abs_ref = max(abs_ref, max(abs(float(x)) for x in arr))
        tick = float(TICK_SIZE_DEFAULT)
        for threshold, step in list(TICK_SIZE_BRACKETS):
            try:
                if abs_ref < float(threshold):
                    tick = float(step)
                    break
            except (TypeError, ValueError):
                continue

    tol_abs = max(1e-9, float(tick) * 0.5)

    def _dedup(values: list[float]) -> list[float]:
        levels_sorted = sorted([float(x) for x in values])
        unique_levels: list[float] = []
        for v in levels_sorted:
            if not unique_levels:
                unique_levels.append(float(v))
                continue
            if abs(float(v) - float(unique_levels[-1])) <= tol_abs:
                continue
            unique_levels.append(float(v))
        return unique_levels

    out: dict[str, tuple[float, float]] = {}
    for label in ("EQH", "EQL"):
        unique = _dedup(swing_prices_by_label[label])
        if len(unique) < 2:
            return None
        bot = float(min(unique))
        top = float(max(unique))
        if not (top > bot):
            return None
        # Мінімальна ширина: більше за tol_abs (інакше це практично line).
        if not ((top - bot) > float(tol_abs)):
            return None
        out[label] = (top, bot)

    return out


def extend_eq_band_candidates_v1(
    *,
    candidates: list[LevelCandidateV1],
    symbol: str | None,
    asof_ts: float,
    liquidity: Any,
) -> None:
    """3.2.5b: додає EQ band candidates (0 або 6 записів) з liquidity magnets."""

    bands = extract_eq_bands_from_liquidity_magnets_v1(liquidity)
    if bands is None:
        return

    window_ts = None

    for owner_tf in ("5m", "1h", "4h"):
        for label in ("EQH", "EQL"):
            top, bot = bands[str(label)]
            if not (float(top) > float(bot)):
                continue
            band_label: LevelLabelBandV1 = "EQH" if str(label) == "EQH" else "EQL"
            candidates.append(
                {
                    "id": make_level_id_band_v1(
                        tf=owner_tf,  # type: ignore[arg-type]
                        label=band_label,
                        bot=float(bot),
                        top=float(top),
                        symbol=str(symbol or "") or None,
                    ),
                    "owner_tf": owner_tf,  # type: ignore[typeddict-item]
                    "kind": "band",
                    "label": band_label,
                    "source": "POOL_DERIVED",
                    "price": None,
                    "top": float(top),
                    "bot": float(bot),
                    "asof_ts": float(asof_ts),
                    "window_ts": window_ts,
                }
            )


def _build_levels_shadow_v1(
    *,
    pools: list[dict[str, Any]],
    ref_price: float | None,
    asof_ts: str,
) -> list[LevelViewShadowV1]:
    """Крок 2: формує `levels_shadow_v1` як as-is проєкцію з liquidity.pools.

    Принципи:
    - жодних змін SMC-core;
    - UI не змінюється;
    - це shadow-контракт для harness/порівняння baseline.

    Примітка:
    - Ми не маємо OHLCV tail всередині builder'а, тому тут використовується
      консервативна оцінка вікна по ref_price (ref_component), без волатильнісної
      складової. Це достатньо для "as-is" стабільного набору на baseline.
    """

    if not isinstance(ref_price, (int, float)):
        return []

    ref = float(ref_price)
    if not (ref == ref):
        return []

    def _clamp01(value: Any) -> float:
        v = safe_float(value)
        if v is None:
            return 0.0
        return max(0.0, min(1.0, float(v)))

    def _role_weight(role: Any) -> float:
        r = str(role or "").upper()
        if r == "PRIMARY":
            return 1.0
        if r == "COUNTER":
            return 0.6
        return 0.5

    # Спрощена оцінка (без OHLCV tail) — лише ref_component.
    price_window_abs = max(abs(ref) * 0.0015, 0.5)
    merge_tol_abs = max(abs(ref) * 0.00025, price_window_abs * 0.08, 0.2)

    def _pool_score(pool: dict[str, Any]) -> float:
        price = safe_float(pool.get("price"))
        if price is None:
            return float("-inf")
        strength = safe_float(pool.get("strength"))
        strength_norm = _clamp01((strength / 100.0) if strength is not None else 0.3)
        dist_norm_raw = abs(price - ref) / max(1e-9, float(price_window_abs) or 1.0)
        dist_norm = min(6.0, max(0.0, dist_norm_raw))
        return (
            _role_weight(pool.get("role")) * (1.0 + strength_norm) / (1.0 + dist_norm)
        )

    def _choose_better_pool(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
        ra = _role_weight(a.get("role"))
        rb = _role_weight(b.get("role"))
        if ra != rb:
            return a if ra > rb else b

        sa = safe_float(a.get("strength"))
        sb = safe_float(b.get("strength"))
        if sa is not None and sb is not None and sa != sb:
            return a if sa > sb else b

        ta = safe_float(a.get("touches"))
        tb = safe_float(b.get("touches"))
        if ta is not None and tb is not None and ta != tb:
            return a if ta > tb else b

        pa = safe_float(a.get("price"))
        pb = safe_float(b.get("price"))
        da = abs(float(pa) - ref) if pa is not None else float("inf")
        db = abs(float(pb) - ref) if pb is not None else float("inf")
        return a if da <= db else b

    def _dedup_by_price(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        cleaned: list[dict[str, Any]] = []
        for p in candidates:
            if not isinstance(p, dict):
                continue
            price = safe_float(p.get("price"))
            if price is None:
                continue
            cp = dict(p)
            cp["price"] = float(price)
            cleaned.append(cp)
        cleaned.sort(key=lambda x: float(x.get("price") or 0.0))
        if not cleaned:
            return []

        out: list[dict[str, Any]] = []
        tol = max(0.0, float(merge_tol_abs) or 0.0)
        for p in cleaned:
            if not out:
                out.append(p)
                continue
            last = out[-1]
            if abs(float(p["price"]) - float(last["price"])) <= tol:
                out[-1] = _choose_better_pool(last, p)
            else:
                out.append(p)
        return out

    def _is_strong_enough(p: dict[str, Any]) -> bool:
        if bool(p.get("_isTarget")):
            return True
        role = str(p.get("role") or "").upper()
        if role in {"PRIMARY", "P"}:
            return True
        strength = safe_float(p.get("strength") or p.get("strength_score"))
        touches = safe_float(p.get("touches") or p.get("touch_count"))
        if touches is not None and touches >= 2:
            return True
        if strength is not None and strength >= 20:
            return True
        return False

    prefiltered = [p for p in pools if isinstance(p, dict) and _is_strong_enough(p)]
    deduped = _dedup_by_price(prefiltered)

    above = [
        p
        for p in deduped
        if safe_float(p.get("price")) is not None and float(p["price"]) >= ref
    ]
    below = [
        p
        for p in deduped
        if safe_float(p.get("price")) is not None and float(p["price"]) < ref
    ]

    def _scored(arr: list[dict[str, Any]]) -> list[tuple[dict[str, Any], float]]:
        rows = [(p, _pool_score(p)) for p in arr]
        rows = [(p, s) for (p, s) in rows if s == s and s != float("-inf")]
        rows.sort(key=lambda x: x[1], reverse=True)
        return rows

    above_scored = _scored(above)
    below_scored = _scored(below)

    def _pick_primary(
        rows: list[tuple[dict[str, Any], float]],
    ) -> dict[str, Any] | None:
        for p, _s in rows:
            if str(p.get("role") or "").upper() == "PRIMARY":
                return p
        return None

    local_above: list[dict[str, Any]] = []
    local_below: list[dict[str, Any]] = []
    pa = _pick_primary(above_scored)
    pb = _pick_primary(below_scored)
    if pa is not None:
        local_above.append(pa)
    if pb is not None:
        local_below.append(pb)

    def _fill(
        rows: list[tuple[dict[str, Any], float]],
        target: list[dict[str, Any]],
        max_count: int,
    ) -> None:
        for p, _s in rows:
            if len(target) >= max_count:
                break
            price = safe_float(p.get("price"))
            if price is None:
                continue
            if any(float(x.get("price") or 0.0) == float(price) for x in target):
                continue
            target.append(p)

    _fill(above_scored, local_above, 3)
    _fill(below_scored, local_below, 3)

    local = [*local_above, *local_below]

    def _nearest(arr: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not arr:
            return None
        return sorted(arr, key=lambda p: abs(float(p.get("price") or 0.0) - ref))[0]

    local_nearest_above = _nearest(local_above)
    local_nearest_below = _nearest(local_below)

    def _is_local(p: dict[str, Any]) -> bool:
        price = safe_float(p.get("price"))
        if price is None:
            return False
        return any(safe_float(x.get("price")) == float(price) for x in local)

    def _far_enough(p: dict[str, Any]) -> bool:
        price = safe_float(p.get("price"))
        if price is None:
            return False
        return abs(float(price) - ref) >= float(price_window_abs) * 1.2

    def _pick_global(rows: list[tuple[dict[str, Any], float]]) -> dict[str, Any] | None:
        for p, _s in rows:
            if _is_local(p):
                continue
            if not _far_enough(p):
                continue
            return p
        return None

    global_out: list[dict[str, Any]] = []
    ga = _pick_global(above_scored)
    gb = _pick_global(below_scored)
    if ga is not None:
        global_out.append(ga)
    if gb is not None:
        global_out.append(gb)

    def _short_title(pool: dict[str, Any]) -> str:
        t = str(pool.get("type") or pool.get("kind") or "POOL").upper()
        role = str(pool.get("role") or "").upper()
        role_mark = "P" if role == "PRIMARY" else "C" if role == "COUNTER" else ""
        type_short = t[:6] if len(t) > 6 else t
        return f"{type_short}{(' ' + role_mark) if role_mark else ''}".strip()

    def _decorate_local(p: dict[str, Any]) -> dict[str, Any]:
        price = safe_float(p.get("price"))
        if price is None:
            return dict(p)
        axis_label = (
            local_nearest_above is not None
            and safe_float(local_nearest_above.get("price")) == float(price)
        ) or (
            local_nearest_below is not None
            and safe_float(local_nearest_below.get("price")) == float(price)
        )
        out = dict(p)
        out["_axisLabel"] = bool(axis_label)
        out["_lineVisible"] = True
        return out

    def _decorate_global(p: dict[str, Any]) -> dict[str, Any]:
        out = dict(p)
        out["_axisLabel"] = True
        out["_lineVisible"] = False
        return out

    selected = [
        *[_decorate_local(p) for p in local],
        *[_decorate_global(p) for p in global_out],
    ]
    # Стабілізуємо порядок.
    selected.sort(
        key=lambda p: (safe_float(p.get("price")) or 0.0, str(p.get("type") or ""))
    )

    tfs: list[str] = ["1m", "5m", "1h", "4h"]
    levels: list[LevelViewShadowV1] = []
    for tf in tfs:
        for p in selected:
            label = str(p.get("type") or p.get("kind") or "").upper() or "POOL"
            role = str(p.get("role") or "") or None
            price = safe_float(p.get("price"))

            is_band = label in {"EQH", "EQL"}
            kind = "band" if is_band else "line"
            style_hint = "band_thin" if is_band else "dotted"

            title = _short_title(p)
            axis_label = bool(p.get("_axisLabel"))
            line_visible = bool(p.get("_lineVisible"))

            if is_band:
                top = float(price) if price is not None else None
                bot = float(price) if price is not None else None
                price_line = None
            else:
                top = None
                bot = None
                price_line = float(price) if price is not None else None

            price_for_id = float(price) if price is not None else 0.0
            level_id = f"shadow_v1:{tf}:{label}:{price_for_id:.6f}:{role or ''}"

            levels.append(
                {
                    "id": level_id,
                    "tf": tf,  # type: ignore[typeddict-item]
                    "kind": kind,  # type: ignore[typeddict-item]
                    "label": label,
                    "style_hint": style_hint,  # type: ignore[typeddict-item]
                    "price": price_line,
                    "top": top,
                    "bot": bot,
                    "source": "pool_selected_as_is",
                    "asof_ts": str(asof_ts or ""),
                    "role": role,
                    "render_hint": {
                        "title": title,
                        "axis_label": axis_label,
                        "line_visible": line_visible,
                    },
                }
            )

    return levels


def _build_levels_candidates_v1(
    *,
    symbol: str,
    asset_or_state: dict[str, Any] | None,
    liquidity: dict[str, Any] | None,
    payload_meta: dict[str, Any],
    ref_price: float | None,
    pools: list[dict[str, Any]],
    frames_by_tf: dict[str, list[dict[str, Any]]] | None = None,
) -> list[LevelCandidateV1]:
    """Крок 3.2.2–3.2.4b1: кандидати рівнів для UI.

    Важливо (scope):
    - додаємо DAILY (PDH/PDL, EDH/EDL) + SESSION (ASH/ASL, LSH/LSL, NYH/NYL) + RANGE (RANGE_H/RANGE_L);
    - без caps/distance/merge;
    - без 1m (LevelTfV1 = {5m, 1h, 4h}).
    """

    _ = ref_price
    _ = pools

    asof_ts = _resolve_asof_ts(payload_meta)
    if asof_ts is None:
        return []

    candidates: list[LevelCandidateV1] = []
    # Крок 3.2.4b1: RANGE має єдине джерело — liquidity.magnets[*].pools[liq_type=RANGE_EXTREME].
    extend_range_candidates_v1(
        candidates=candidates,
        symbol=str(symbol or "") or None,
        asof_ts=float(asof_ts),
        liquidity=liquidity,
    )

    # Крок 3.2.5b: EQ bands мають єдине джерело — liquidity.magnets[*].pools[liq_type=EQH/EQL] + source_swings.
    extend_eq_band_candidates_v1(
        candidates=candidates,
        symbol=str(symbol or "") or None,
        asof_ts=float(asof_ts),
        liquidity=liquidity,
    )

    # DAILY/SESSION потребують OHLCV frames.
    if not isinstance(frames_by_tf, dict) or not frames_by_tf:
        candidates.sort(
            key=lambda c: (
                str(c.get("owner_tf") or ""),
                str(c.get("label") or ""),
                float(c.get("price") or 0.0),
            )
        )
        return candidates

    candidates.extend(
        build_prev_day_pdh_pdl_candidates_v1(
            symbol=str(symbol or "") or None,
            asof_ts=float(asof_ts),
            day_start_hour_utc=int(SMC_DAILY_START_HOUR_UTC),
            frames_by_tf=frames_by_tf,
        )
    )

    candidates.extend(
        build_today_edh_edl_candidates_v1(
            symbol=str(symbol or "") or None,
            asof_ts=float(asof_ts),
            day_start_hour_utc=int(SMC_DAILY_START_HOUR_UTC),
            frames_by_tf=frames_by_tf,
        )
    )

    # Крок 3.2.3b: SESSION кандидати за SSOT UTC-вікнами (ASIA/LONDON/NY).
    labels_by_tag: dict[str, tuple[LevelLabelLineV1, LevelLabelLineV1]] = {
        "ASIA": ("ASH", "ASL"),
        "LONDON": ("LSH", "LSL"),
        "NY": ("NYH", "NYL"),
    }

    for tag, (start_h, end_h) in dict(SMC_SESSION_WINDOWS_UTC).items():
        labels = labels_by_tag.get(str(tag))
        if labels is None:
            continue
        label_high, label_low = labels
        candidates.extend(
            build_session_high_low_candidates_v1(
                symbol=str(symbol or "") or None,
                asof_ts=float(asof_ts),
                session_start_hour_utc=int(start_h),
                session_end_hour_utc=int(end_h),
                label_high=label_high,
                label_low=label_low,
                frames_by_tf=frames_by_tf,
            )
        )

    # Стабілізуємо порядок, щоб id/hash були детерміновані.
    candidates.sort(
        key=lambda c: (
            str(c.get("owner_tf") or ""),
            str(c.get("label") or ""),
            float(c.get("price") or 0.0),
        )
    )
    return candidates


def _build_levels_selected_v1(
    *,
    symbol: str,
    payload_meta: dict[str, Any],
    ref_price: float | None,
    candidates_v1: list[LevelCandidateV1],
    frames_by_tf: dict[str, list[dict[str, Any]]] | None = None,
) -> list[LevelSelectedV1]:
    """Крок 3.3c: selection policy по TF (caps + distance + пріоритети).

    Важливо:
    - 1m selected поки вимкнено (policy 3.3c0), щоб зменшити ризик.
    - UI cutover заборонено до 3.3d; це лише підготовка payload.
    """

    # Fallback для selected_at_close_ts (секунди).
    asof_fallback_s = _resolve_asof_ts(payload_meta)

    out: list[LevelSelectedV1] = []
    for owner_tf in ("5m", "1h", "4h", "1m"):
        out.extend(
            select_levels_for_tf_v1(
                owner_tf,
                candidates_v1,
                ref_price,
                asof_fallback_s,
                frames_by_tf=frames_by_tf,
                symbol=str(symbol or "") or None,
            )
        )

    # Детермінізм: стабілізуємо порядок у межах TF за rank.
    out.sort(key=lambda s: (str(s.get("owner_tf") or ""), int(s.get("rank") or 0)))
    return out


def find_active_session_tag_utc(asof_ts: float | None) -> str | None:
    """Повертає активний session tag (ASIA/LONDON/NY) для UTC часу.

    Використовує SSOT `SMC_SESSION_WINDOWS_UTC`.
    """

    if asof_ts is None:
        return None
    try:
        dt = datetime.fromtimestamp(float(asof_ts), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None

    hour = int(dt.hour) % 24

    # Порядок важливий лише якщо колись буде overlap (не очікуємо).
    for tag in ("ASIA", "LONDON", "NY"):
        if tag not in dict(SMC_SESSION_WINDOWS_UTC):
            continue
        start_h, end_h = dict(SMC_SESSION_WINDOWS_UTC)[tag]
        s = int(start_h) % 24
        e = int(end_h) % 24
        if s == e:
            continue
        if s < e:
            if s <= hour < e:
                return tag
        else:
            # Wrap-around через 00:00
            if hour >= s or hour < e:
                return tag
    return None


def _compute_atr_5m(
    frames_by_tf: dict[str, list[dict[str, Any]]] | None,
    *,
    asof_ts: float | None,
    period: int = 14,
) -> float | None:
    if not isinstance(frames_by_tf, dict):
        return None
    bars = [b for b in (frames_by_tf.get("5m") or []) if isinstance(b, dict)]
    if not bars:
        return None

    rows: list[tuple[float, dict[str, Any]]] = []
    for b in bars:
        if b.get("complete") is False:
            continue
        t = _bar_time_s(b)
        if t is None:
            continue
        if asof_ts is not None and float(t) > float(asof_ts):
            continue
        rows.append((float(t), b))
    rows.sort(key=lambda x: x[0])

    if len(rows) < (period + 1):
        return None

    # Беремо останні period+1 барів, щоб мати prev_close.
    rows = rows[-(period + 1) :]

    tr_values: list[float] = []
    prev_close: float | None = None
    for _t, b in rows:
        h = safe_float(b.get("high"), finite=True)
        low_value = safe_float(b.get("low"), finite=True)
        c = safe_float(b.get("close"), finite=True)
        if h is None or low_value is None:
            continue
        high = float(h)
        low = float(low_value)
        if prev_close is None:
            # Перший бар у вікні: TR = high-low.
            tr = high - low
        else:
            tr = max(
                high - low,
                abs(high - float(prev_close)),
                abs(low - float(prev_close)),
            )
        if tr == tr and tr > 0:
            tr_values.append(float(tr))
        prev_close = float(c) if c is not None else prev_close

    if len(tr_values) < period:
        return None

    atr = sum(tr_values[-period:]) / float(period)
    if atr == atr and atr > 0:
        return float(atr)
    return None


def _compute_dr_4h(
    frames_by_tf: dict[str, list[dict[str, Any]]] | None,
    *,
    asof_ts: float | None,
) -> float | None:
    """DR_4h як range (high-low) останнього complete 4h бару до asof_ts."""

    if not isinstance(frames_by_tf, dict):
        return None
    bars = [b for b in (frames_by_tf.get("4h") or []) if isinstance(b, dict)]
    if not bars:
        return None

    best_t: float | None = None
    best_bar: dict[str, Any] | None = None
    for b in bars:
        if b.get("complete") is False:
            continue
        t = _bar_time_s(b)
        if t is None:
            continue
        if asof_ts is not None and float(t) > float(asof_ts):
            continue
        if best_t is None or float(t) > float(best_t):
            best_t = float(t)
            best_bar = b

    if best_bar is None:
        return None

    high_value = safe_float(best_bar.get("high"), finite=True)
    low_value = safe_float(best_bar.get("low"), finite=True)
    if high_value is None or low_value is None:
        return None
    dr = float(high_value) - float(low_value)
    if dr == dr and dr > 0:
        return float(dr)
    return None


def _level_distance_abs(candidate: Any, *, ref_price: float) -> float | None:
    kind = str(candidate.get("kind") or "").lower()
    if kind == "band":
        top = safe_float(candidate.get("top"), finite=True)
        bot = safe_float(candidate.get("bot"), finite=True)
        if top is None or bot is None:
            return None
        hi = float(max(float(top), float(bot)))
        lo = float(min(float(top), float(bot)))
        if lo <= float(ref_price) <= hi:
            return 0.0
        return min(abs(float(ref_price) - lo), abs(float(ref_price) - hi))

    price = safe_float(candidate.get("price"), finite=True)
    if price is None:
        return None
    return abs(float(ref_price) - float(price))


def select_levels_for_tf_v1(
    tf: str,
    merged: list[LevelCandidateV1],
    ref_price: float | None,
    asof_ts: float | None,
    *,
    frames_by_tf: dict[str, list[dict[str, Any]]] | None = None,
    symbol: str | None = None,
) -> list[LevelSelectedV1]:
    """3.3c1: вибирає selected-рівні для конкретного TF.

    Повертає список `LevelSelectedV1` з `rank` та `reason[]` за правилами 3.3c0.
    """

    tf_norm = str(tf or "").strip().lower()
    policy = LEVELS_SELECTED_POLICY_V1.get(tf_norm)
    if not isinstance(policy, dict):
        return []
    if bool(policy.get("disabled")):
        return []

    # Fallback для close_ts.
    close_ts = float(asof_ts) if isinstance(asof_ts, (int, float)) else None

    # Витягуємо candidates для owner_tf.
    candidates_tf: list[LevelCandidateV1] = [
        c
        for c in (merged or [])
        if isinstance(c, dict)
        and str(c.get("owner_tf") or "").strip().lower() == tf_norm
    ]
    if not candidates_tf:
        return []

    # Метрики для distance gate.
    atr_5m = _compute_atr_5m(frames_by_tf, asof_ts=close_ts)
    dr_4h = _compute_dr_4h(frames_by_tf, asof_ts=close_ts)

    gate_abs: float | None = None
    soft_gate_abs: float | None = None
    dg = policy.get("distance_gate")
    if isinstance(dg, dict):
        mode = str(dg.get("mode") or "")
        if mode == "atr5m":
            mult = safe_float(dg.get("atr5m_mult"), finite=True)
            if atr_5m is not None and mult is not None and float(mult) > 0:
                gate_abs = float(atr_5m) * float(mult)
            soft_mult = safe_float(dg.get("soft_atr5m_mult"), finite=True)
            if atr_5m is not None and soft_mult is not None and float(soft_mult) > 0:
                soft_gate_abs = float(atr_5m) * float(soft_mult)
        elif mode == "dr4h_or_atr5m":
            dr_mult = safe_float(dg.get("dr4h_mult"), finite=True)
            atr_mult = safe_float(dg.get("atr5m_mult"), finite=True)
            if dr_4h is not None and dr_mult is not None and float(dr_mult) > 0:
                gate_abs = float(dr_4h) * float(dr_mult)
                soft_dr_mult = safe_float(dg.get("soft_dr4h_mult"), finite=True)
                if soft_dr_mult is not None and float(soft_dr_mult) > 0:
                    soft_gate_abs = float(dr_4h) * float(soft_dr_mult)
            elif atr_5m is not None and atr_mult is not None and float(atr_mult) > 0:
                gate_abs = float(atr_5m) * float(atr_mult)
                soft_atr_mult = safe_float(dg.get("soft_atr5m_mult"), finite=True)
                if soft_atr_mult is not None and float(soft_atr_mult) > 0:
                    soft_gate_abs = float(atr_5m) * float(soft_atr_mult)

    raw_caps = policy.get("caps")
    caps = raw_caps if isinstance(raw_caps, dict) else {}
    cap_lines = int(caps.get("lines_total") or 0)
    cap_bands = int(caps.get("bands_total") or 0)

    def _is_line(c: Any) -> bool:
        return str(c.get("kind") or "").lower() == "line"

    def _is_band(c: Any) -> bool:
        return str(c.get("kind") or "").lower() == "band"

    def _label(c: Any) -> str:
        return str(c.get("label") or "").upper()

    def _distance_bucket(c: Any) -> str:
        """3.3g: м'який distance-гейт.

        Повертає:
        - IN: в hard-gate
        - SOFT: між hard і soft (можна вибрати, але як слабкий пріоритет)
        - OUT: надто далеко

        Примітка: якщо немає ref_price або метрики gate_abs, робимо гейт м'яким і
        не відсікаємо кандидатів (IN), щоб selection залишався працездатним.
        """

        lab = _label(c)
        if lab in {"PDH", "PDL"} and tf_norm in {"1h", "4h"}:
            return "IN_PINNED"

        if ref_price is None or gate_abs is None:
            return "IN"

        d = _level_distance_abs(c, ref_price=float(ref_price))
        if d is None:
            return "OUT"

        if float(d) <= float(gate_abs):
            return "IN"

        if soft_gate_abs is not None and float(d) <= float(soft_gate_abs):
            return "SOFT"

        return "OUT"

    buckets_by_id: dict[str, str] = {}
    for c in candidates_tf:
        cid = str(c.get("id") or "")
        if cid:
            buckets_by_id[cid] = _distance_bucket(c)

    # 3.3g: розрізняємо hard vs soft (не все однаково "в gate").
    gated_hard = [
        c for c in candidates_tf if _distance_bucket(c) in {"IN", "IN_PINNED"}
    ]
    gated_soft = [
        c for c in candidates_tf if _distance_bucket(c) in {"IN", "IN_PINNED", "SOFT"}
    ]

    # Групи line.
    range_labels = [str(x).upper() for x in (policy.get("range_labels") or [])]

    active_tag = find_active_session_tag_utc(close_ts)
    session_pair_by_tag: dict[str, tuple[str, str]] = {
        "ASIA": ("ASH", "ASL"),
        "LONDON": ("LSH", "LSL"),
        "NY": ("NYH", "NYL"),
    }
    session_labels: list[str] = []
    if active_tag is not None and active_tag in session_pair_by_tag:
        a, b = session_pair_by_tag[active_tag]
        session_labels = [a, b]

    def _pick_by_labels(
        labels: list[str], *, from_list: list[LevelCandidateV1]
    ) -> list[LevelCandidateV1]:
        want = set([str(x).upper() for x in labels if str(x).strip()])
        return [c for c in from_list if _is_line(c) and _label(c) in want]

    # Session/Daily можуть бути в soft-gate (трейдерський контекст), Range/Bands — лише hard.
    session_lines = _pick_by_labels(session_labels, from_list=gated_soft)
    range_lines = _pick_by_labels(range_labels, from_list=gated_hard)

    # Daily маяки: PDH/PDL (pinned) або fallback EDH/EDL.
    # 5m: тільки якщо проходять gate; 1h/4h: PDH/PDL pinned навіть якщо поза gate.
    daily_pdhpdl = _pick_by_labels(
        ["PDH", "PDL"],
        from_list=(candidates_tf if tf_norm in {"1h", "4h"} else gated_soft),
    )
    daily_edhedl = _pick_by_labels(["EDH", "EDL"], from_list=gated_soft)

    def _sort_by_distance_then_label(
        arr: list[LevelCandidateV1],
    ) -> list[LevelCandidateV1]:
        def _k(c: LevelCandidateV1) -> tuple[Any, ...]:
            d = (
                _level_distance_abs(c, ref_price=float(ref_price))
                if ref_price is not None
                else None
            )
            return (
                float(d) if d is not None else float("inf"),
                _label(c),
                str(c.get("source") or ""),
                str(c.get("id") or ""),
            )

        return sorted(arr, key=_k)

    selected_lines: list[tuple[LevelCandidateV1, list[str]]] = []

    def _append_line(
        c: LevelCandidateV1, reasons: list[str], *, budget: list[int]
    ) -> None:
        if budget[0] <= 0:
            return
        selected_lines.append((c, reasons))
        budget[0] -= 1

    # 3.3f: slot-композиція (5m/1h/4h):
    # - Slot C (Session): active session high/low (пара), якщо в gate.
    # - Slot D (Daily): PDH/PDL pinned (5m: тільки в gate; 1h/4h: pinned), інакше fallback EDH/EDL (в gate).
    # - Slot B (Range): 1 з RANGE_H/RANGE_L — найближчий (filler).
    line_budget = [max(0, int(cap_lines))]

    # Slot C: pinned session pair.
    if tf_norm in {"5m", "1h", "4h"} and session_labels:
        sess_by_label: dict[str, LevelCandidateV1] = {}
        for c in session_lines:
            lab = _label(c)
            if lab in set([str(x).upper() for x in session_labels]):
                sess_by_label.setdefault(lab, c)

        if len(sess_by_label) >= 2:
            for lab in [str(x).upper() for x in session_labels]:
                if lab in sess_by_label:
                    _append_line(
                        sess_by_label[lab],
                        ["PINNED_SESSION_ACTIVE"],
                        budget=line_budget,
                    )

    # Slot D: daily pinned (PDH/PDL) або fallback EDH/EDL.
    daily_sorted = _sort_by_distance_then_label(daily_pdhpdl)
    daily_reason = ["PINNED_PDH_PDL"]
    if not daily_sorted:
        daily_sorted = _sort_by_distance_then_label(daily_edhedl)
        daily_reason = ["FALLBACK_EDH_EDL"]

    if daily_sorted and line_budget[0] > 0:
        # Вимагаємо хоча б 1 (на 5m), а якщо є бюджет — додаємо 2.
        _append_line(daily_sorted[0], list(daily_reason), budget=line_budget)
        if line_budget[0] > 0:
            # Додаємо другу лінію, якщо це інша (за id).
            first_id = str(daily_sorted[0].get("id") or "")
            for c in daily_sorted[1:]:
                if str(c.get("id") or "") and str(c.get("id") or "") != first_id:
                    _append_line(c, list(daily_reason), budget=line_budget)
                    break

    # Slot B: range nearest (filler).
    if line_budget[0] > 0:
        options = _sort_by_distance_then_label(range_lines)
        if options:
            _append_line(options[0], ["RANGE_NEAREST"], budget=line_budget)

    # Дедуп по id.
    dedup_lines: list[tuple[LevelCandidateV1, list[str]]] = []
    seen_ids: set[str] = set()
    for c, reasons in selected_lines:
        cid = str(c.get("id") or "")
        if not cid or cid in seen_ids:
            continue
        seen_ids.add(cid)
        dedup_lines.append((c, reasons))

    # Cap lines.
    if cap_lines > 0:
        dedup_lines = dedup_lines[:cap_lines]
    else:
        dedup_lines = []

    # Bands (EQH/EQL): максимум 1 band/side, загальний cap.
    band_labels = [str(x).upper() for x in (policy.get("eq_band_labels") or [])]
    bands_tf = [c for c in gated_hard if _is_band(c) and _label(c) in set(band_labels)]
    bands_tf = _sort_by_distance_then_label(bands_tf)

    picked_band_by_label: dict[str, LevelCandidateV1] = {}
    for c in bands_tf:
        lab = _label(c)
        if lab not in {"EQH", "EQL"}:
            continue
        if lab in picked_band_by_label:
            continue
        picked_band_by_label[lab] = c
        if len(picked_band_by_label) >= 2:
            break

    picked_bands: list[LevelCandidateV1] = []
    for lab in ("EQH", "EQL"):
        if lab in picked_band_by_label:
            picked_bands.append(picked_band_by_label[lab])
    if cap_bands > 0:
        picked_bands = picked_bands[:cap_bands]
    else:
        picked_bands = []

    # Формуємо LevelSelectedV1 і виставляємо rank.
    selected: list[LevelSelectedV1] = []
    rank = 0

    def _emit(c: LevelCandidateV1, reasons: list[str]) -> None:
        nonlocal rank
        rank += 1
        dist = (
            _level_distance_abs(c, ref_price=float(ref_price))
            if ref_price is not None
            else None
        )

        cid = str(c.get("id") or "")
        bucket = buckets_by_id.get(cid) if cid else None

        extra: list[str] = []
        if bucket == "SOFT":
            extra.append("DISTANCE_SOFT_OK")
        elif bucket == "IN_PINNED":
            # PDH/PDL для 1h/4h: показуємо як маяк навіть якщо далеко.
            # Це трейдерський контекст, але позначаємо явно.
            if ref_price is not None and gate_abs is not None:
                d_abs = _level_distance_abs(c, ref_price=float(ref_price))
                if d_abs is not None and float(d_abs) > float(gate_abs):
                    extra.append("DISTANCE_PINNED_OVERRIDE")

        s: LevelSelectedV1 = {
            "id": str(c.get("id") or "")
            or make_level_id_line_v1(
                tf=tf_norm,  # type: ignore[arg-type]
                label=str(c.get("label") or ""),  # type: ignore[arg-type]
                price=float(c.get("price") or 0.0),
                symbol=symbol,
            ),
            "owner_tf": tf_norm,  # type: ignore[typeddict-item]
            "kind": c.get("kind"),
            "label": c.get("label"),
            "source": c.get("source"),
            "price": c.get("price"),
            "top": c.get("top"),
            "bot": c.get("bot"),
            "rank": int(rank),
            "reason": [str(x) for x in (list(reasons or []) + extra) if str(x).strip()],
            "distance_at_select": float(dist) if dist is not None else None,
        }
        if close_ts is not None:
            s["selected_at_close_ts"] = float(close_ts)
        selected.append(s)

    for c, reasons in dedup_lines:
        _emit(c, reasons)
    for c in picked_bands:
        _emit(c, ["BAND_NEAR_PRICE"])

    return selected


def build_session_high_low_candidates_v1(
    *,
    symbol: str | None,
    asof_ts: float,
    session_start_hour_utc: int,
    session_end_hour_utc: int,
    label_high: LevelLabelLineV1,
    label_low: LevelLabelLineV1,
    frames_by_tf: dict[str, list[dict[str, Any]]],
) -> list[LevelCandidateV1]:
    """Будує SESSION high/low кандидати для конкретного UTC-вікна.

    Правила:
    - вікно сесії визначається через `get_session_window_utc(asof_ts, ...)`;
    - анти-lookahead: враховуємо бари тільки до `asof_ts` включно;
    - readiness: не повертаємо кандидати на дуже малому наборі барів.
    """

    # Policy джерела барів: 1h (менше шуму) або fallback 5m.
    bars_1h = [b for b in frames_by_tf.get("1h", []) if isinstance(b, dict)]
    bars_5m = [b for b in frames_by_tf.get("5m", []) if isinstance(b, dict)]

    # Тривалість сесії у годинах (для адаптивного readiness).
    s_h = int(session_start_hour_utc) % 24
    e_h = int(session_end_hour_utc) % 24
    duration_h = (e_h - s_h) if e_h >= s_h else (24 - s_h) + e_h
    if duration_h <= 0:
        duration_h = 24

    min_1h = 2 if duration_h <= 6 else 3
    min_5m = 12 if duration_h <= 6 else 20

    if len(bars_1h) >= min_1h:
        source_tf = "1h"
        bars = bars_1h
        min_need = min_1h
    elif len(bars_5m) >= min_5m:
        source_tf = "5m"
        bars = bars_5m
        min_need = min_5m
    else:
        return []

    w_start, w_end = get_session_window_utc(
        float(asof_ts),
        session_start_hour_utc=int(session_start_hour_utc),
        session_end_hour_utc=int(session_end_hour_utc),
    )

    in_window: list[dict[str, Any]] = []
    for b in bars:
        if b.get("complete") is False:
            continue
        t = _bar_time_s(b)
        if t is None:
            continue
        # Анти-lookahead: бари лише до поточного asof_ts.
        if float(w_start) <= float(t) < float(w_end) and float(t) <= float(asof_ts):
            in_window.append(b)

    if len(in_window) < int(min_need):
        return []

    highs: list[float] = []
    lows: list[float] = []
    for b in in_window:
        h = safe_float(b.get("high"), finite=True)
        low = safe_float(b.get("low"), finite=True)
        if h is None or low is None:
            continue
        highs.append(float(h))
        lows.append(float(low))

    if not highs or not lows:
        return []

    hh = max(highs)
    ll = min(lows)

    out: list[LevelCandidateV1] = []
    for owner_tf in ("5m", "1h", "4h"):
        out.append(
            {
                "id": make_level_id_line_v1(
                    tf=owner_tf,  # type: ignore[arg-type]
                    label=label_high,
                    price=float(hh),
                    symbol=symbol,
                ),
                "owner_tf": owner_tf,  # type: ignore[typeddict-item]
                "kind": "line",
                "label": label_high,
                "source": "SESSION",
                "price": float(hh),
                "top": None,
                "bot": None,
                "asof_ts": float(asof_ts),
                "window_ts": (float(w_start), float(w_end)),
            }
        )
        out.append(
            {
                "id": make_level_id_line_v1(
                    tf=owner_tf,  # type: ignore[arg-type]
                    label=label_low,
                    price=float(ll),
                    symbol=symbol,
                ),
                "owner_tf": owner_tf,  # type: ignore[typeddict-item]
                "kind": "line",
                "label": label_low,
                "source": "SESSION",
                "price": float(ll),
                "top": None,
                "bot": None,
                "asof_ts": float(asof_ts),
                "window_ts": (float(w_start), float(w_end)),
            }
        )

    _ = source_tf
    return out


def _resolve_asof_ts(payload_meta: dict[str, Any]) -> float | None:
    """Визначає asof_ts (секунди) для candidate'ів.

    Пріоритет:
    1) replay_cursor_ms (якщо є) — це close_time останнього відомого бару.
    2) meta["ts"] (ISO Z) — fallback (менш точний для бар-логіки).
    """

    if not isinstance(payload_meta, dict):
        return None

    cursor_ms = payload_meta.get("replay_cursor_ms")
    if isinstance(cursor_ms, (int, float)):
        ms = float(cursor_ms)
        if ms > 1e12:
            return ms / 1000.0
        if ms > 1e9:
            return ms

    ts_raw = payload_meta.get("ts")
    if isinstance(ts_raw, str) and ts_raw.strip():
        try:
            dt = iso_z_to_dt(ts_raw)
            return float(dt.timestamp())
        except (TypeError, ValueError):
            return None

    return None


def _bar_time_s(bar: dict[str, Any]) -> float | None:
    """Нормалізує час бару до секунд Unix.

    Підтримка (best-effort):
    - UI /ohlcv: bar["time"] у мілісекундах;
    - альтернативи: close_time/open_time (ms або s).
    """

    if not isinstance(bar, dict):
        return None

    for key in ("time", "close_time", "open_time", "ts", "timestamp"):
        v = bar.get(key)
        if isinstance(v, (int, float)):
            x = float(v)
            if x > 1e12:
                return x / 1000.0
            return x
        if isinstance(v, str) and v.strip():
            try:
                dt = iso_z_to_dt(v)
                return float(dt.timestamp())
            except (TypeError, ValueError):
                continue
    return None


def build_prev_day_pdh_pdl_candidates_v1(
    *,
    symbol: str | None,
    asof_ts: float,
    day_start_hour_utc: int,
    frames_by_tf: dict[str, list[dict[str, Any]]],
) -> list[LevelCandidateV1]:
    """Будує PDH/PDL кандидатів з попереднього day window (Крок 3.2.2b)."""

    prev_start_ts, prev_end_ts = get_prev_day_window_utc(
        float(asof_ts), day_start_hour_utc=int(day_start_hour_utc)
    )

    picked = pick_daily_bars_for_levels_v1(frames_by_tf, purpose="prev_day")
    if picked is None:
        return []
    source_tf, bars = picked

    # Фільтр: лише бари у prev-day вікні.
    in_window: list[dict[str, Any]] = []
    for b in bars:
        if not isinstance(b, dict):
            continue
        if b.get("complete") is False:
            continue
        t = _bar_time_s(b)
        if t is None:
            continue
        if float(prev_start_ts) <= float(t) < float(prev_end_ts):
            in_window.append(b)

    # Готовність: не будуємо “фейк” на малому наборі барів.
    if str(source_tf) == "1h":
        if len(in_window) < 12:
            return []
    elif str(source_tf) == "5m":
        if len(in_window) < 100:
            return []
    else:
        return []

    highs: list[float] = []
    lows: list[float] = []
    for b in in_window:
        h = safe_float(b.get("high"), finite=True)
        low = safe_float(b.get("low"), finite=True)
        if h is None or low is None:
            continue
        highs.append(float(h))
        lows.append(float(low))

    if not highs or not lows:
        return []

    pdh = max(highs)
    pdl = min(lows)

    out: list[LevelCandidateV1] = []
    for owner_tf in ("5m", "1h", "4h"):
        out.append(
            {
                "id": make_level_id_line_v1(
                    tf=owner_tf,  # type: ignore[arg-type]
                    label="PDH",
                    price=float(pdh),
                    symbol=symbol,
                ),
                "owner_tf": owner_tf,  # type: ignore[typeddict-item]
                "kind": "line",
                "label": "PDH",
                "source": "DAILY",
                "price": float(pdh),
                "top": None,
                "bot": None,
                "asof_ts": float(asof_ts),
                "window_ts": (float(prev_start_ts), float(prev_end_ts)),
            }
        )
        out.append(
            {
                "id": make_level_id_line_v1(
                    tf=owner_tf,  # type: ignore[arg-type]
                    label="PDL",
                    price=float(pdl),
                    symbol=symbol,
                ),
                "owner_tf": owner_tf,  # type: ignore[typeddict-item]
                "kind": "line",
                "label": "PDL",
                "source": "DAILY",
                "price": float(pdl),
                "top": None,
                "bot": None,
                "asof_ts": float(asof_ts),
                "window_ts": (float(prev_start_ts), float(prev_end_ts)),
            }
        )

    return out


def build_today_edh_edl_candidates_v1(
    *,
    symbol: str | None,
    asof_ts: float,
    day_start_hour_utc: int,
    frames_by_tf: dict[str, list[dict[str, Any]]],
) -> list[LevelCandidateV1]:
    """Будує EDH/EDL кандидатів у today window (Крок 3.2.2c1).

    Важливо:
    - EDH/EDL можуть змінюватися всередині дня — це нормальна поведінка candidates.
    - Анти-фейк: слабші readiness-пороги, бо today може бути «молодим».
    """

    today_start_ts, today_end_ts = get_day_window_utc(
        float(asof_ts), day_start_hour_utc=int(day_start_hour_utc)
    )

    picked = pick_daily_bars_for_levels_v1(frames_by_tf, purpose="today")
    if picked is None:
        return []
    source_tf, bars = picked

    in_window: list[dict[str, Any]] = []
    for b in bars:
        if not isinstance(b, dict):
            continue
        if b.get("complete") is False:
            continue
        t = _bar_time_s(b)
        if t is None:
            continue
        # today window + до asof (без lookahead)
        if float(today_start_ts) <= float(t) < float(today_end_ts) and float(
            t
        ) <= float(asof_ts):
            in_window.append(b)

    # Готовність: today може бути «молодим», тому пороги нижчі.
    if str(source_tf) == "1h":
        if len(in_window) < 3:
            return []
    elif str(source_tf) == "5m":
        if len(in_window) < 20:
            return []
    else:
        return []

    highs: list[float] = []
    lows: list[float] = []
    for b in in_window:
        h = safe_float(b.get("high"), finite=True)
        low = safe_float(b.get("low"), finite=True)
        if h is None or low is None:
            continue
        highs.append(float(h))
        lows.append(float(low))

    if not highs or not lows:
        return []

    edh = max(highs)
    edl = min(lows)

    out: list[LevelCandidateV1] = []
    for owner_tf in ("5m", "1h", "4h"):
        out.append(
            {
                "id": make_level_id_line_v1(
                    tf=owner_tf,  # type: ignore[arg-type]
                    label="EDH",
                    price=float(edh),
                    symbol=symbol,
                ),
                "owner_tf": owner_tf,  # type: ignore[typeddict-item]
                "kind": "line",
                "label": "EDH",
                "source": "DAILY",
                "price": float(edh),
                "top": None,
                "bot": None,
                "asof_ts": float(asof_ts),
                "window_ts": (float(today_start_ts), float(today_end_ts)),
            }
        )
        out.append(
            {
                "id": make_level_id_line_v1(
                    tf=owner_tf,  # type: ignore[arg-type]
                    label="EDL",
                    price=float(edl),
                    symbol=symbol,
                ),
                "owner_tf": owner_tf,  # type: ignore[typeddict-item]
                "kind": "line",
                "label": "EDL",
                "source": "DAILY",
                "price": float(edl),
                "top": None,
                "bot": None,
                "asof_ts": float(asof_ts),
                "window_ts": (float(today_start_ts), float(today_end_ts)),
            }
        )

    return out


def pick_daily_bars_for_levels_v1(
    frames_by_tf: dict[str, list[dict[str, Any]]],
    *,
    purpose: str,
    prefer_tf: str = "1h",
    fallback_tf: str = "5m",
    min_bars_prev_day_1h: int = 12,
    min_bars_prev_day_5m: int = 100,
    min_bars_today_1h: int = 3,
    min_bars_today_5m: int = 20,
) -> tuple[str, list[dict[str, Any]]] | None:
    """Policy вибору джерела барів для DAILY кандидатів (Крок 3.2.2a).

    Принцип:
    - пріоритет 1h (менше шуму, швидше),
    - fallback 5m якщо 1h не готовий,
    - якщо даних недостатньо — повертаємо None (кандидати не додаються).

    `purpose`:
    - "prev_day" → PDH/PDL
    - "today" → EDH/EDL
    """

    purpose_norm = str(purpose or "").strip().lower()
    if purpose_norm not in {"prev_day", "today"}:
        raise ValueError("purpose має бути 'prev_day' або 'today'")

    def _complete_only(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for b in bars or []:
            if not isinstance(b, dict):
                continue
            # Best-effort: якщо є прапор complete=false — це не повний бар.
            if b.get("complete") is False:
                continue
            out.append(b)
        return out

    bars_1h = _complete_only(frames_by_tf.get(str(prefer_tf), []))
    bars_5m = _complete_only(frames_by_tf.get(str(fallback_tf), []))

    if purpose_norm == "prev_day":
        need_1h = max(1, int(min_bars_prev_day_1h))
        need_5m = max(1, int(min_bars_prev_day_5m))
    else:
        need_1h = max(1, int(min_bars_today_1h))
        need_5m = max(1, int(min_bars_today_5m))

    if len(bars_1h) >= need_1h:
        return str(prefer_tf), bars_1h
    if len(bars_5m) >= need_5m:
        return str(fallback_tf), bars_5m
    return None


def _simplify_events(events: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    if isinstance(events, list):
        for event in events[-MAX_EVENTS:]:
            if not isinstance(event, dict):
                continue
            price_value = (
                event.get("price") or event.get("price_level") or event.get("level")
            )
            time_value = (
                event.get("time")
                or event.get("timestamp")
                or event.get("ts")
                or event.get("created_at")
            )
            if isinstance(time_value, (int, float)):
                normalized_ts = _format_utc_from_ms(time_value)
                if normalized_ts:
                    time_value = normalized_ts
            elif isinstance(time_value, str):
                normalized_text = try_iso_to_human_utc(time_value)
                if normalized_text:
                    time_value = normalized_text
            output.append(
                {
                    "type": event.get("event_type") or event.get("type"),
                    "direction": event.get("direction"),
                    "price": price_value,
                    "time": time_value,
                    "status": event.get("status") or event.get("state"),
                }
            )
    return output


def _simplify_execution_events(events: Any) -> list[dict[str, Any]]:
    """Нормалізує execution_events до lightweight dict[] (без важких полів).

    Важливо: execution_events в Stage5 можуть бути епізодичними (лише на кроці).
    Тому далі ми їх персистимо в ViewerStateCache.
    """

    output: list[dict[str, Any]] = []
    if not isinstance(events, list):
        return output
    for ev in events[-MAX_EXECUTION_EVENTS:]:
        if not isinstance(ev, dict):
            continue
        output.append(
            {
                "event_type": ev.get("event_type") or ev.get("type"),
                "direction": ev.get("direction") or ev.get("dir"),
                "time": ev.get("time") or ev.get("ts") or ev.get("timestamp"),
                "price": safe_float(ev.get("price")),
                "level": safe_float(ev.get("level")),
                "ref": ev.get("ref"),
                "poi_zone_id": ev.get("poi_zone_id"),
                "meta": coerce_dict(ev.get("meta")),
            }
        )
    return output


def _persist_execution_events(
    raw_events: list[dict[str, Any]],
    cache: ViewerStateCache | None,
) -> list[dict[str, Any]]:
    """Персистить execution events, щоб UI міг їх малювати як "історію".

    Політика:
    - додаємо лише валідні події;
    - дедуп по ключу (time, event_type, direction, level);
    - тримаємо останні MAX_EXECUTION_EVENTS.
    """

    if cache is None:
        return raw_events

    if raw_events:
        merged = list(cache.last_execution_events)
        seen = {
            (
                str(e.get("time")),
                str(e.get("event_type")),
                str(e.get("direction")),
                str(e.get("level")),
            )
            for e in merged
            if isinstance(e, dict)
        }

        for e in raw_events:
            key = (
                str(e.get("time")),
                str(e.get("event_type")),
                str(e.get("direction")),
                str(e.get("level")),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(e)

        cache.last_execution_events = merged[-MAX_EXECUTION_EVENTS:]

    return list(cache.last_execution_events)


def _simplify_legs(legs: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    if not isinstance(legs, list):
        return output
    for leg in legs[-MAX_LEGS:]:
        if not isinstance(leg, dict):
            continue
        output.append(
            {
                "label": leg.get("label"),
                "direction": leg.get("direction"),
                "from_index": leg.get("from_index"),
                "to_index": leg.get("to_index"),
                "strength": leg.get("strength"),
            }
        )
    return output


def _simplify_swings(swings: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    if not isinstance(swings, list):
        return output
    for swing in swings[-MAX_SWINGS:]:
        if not isinstance(swing, dict):
            continue
        time_value = swing.get("time")
        if isinstance(time_value, str):
            normalized_text = try_iso_to_human_utc(time_value)
            if normalized_text:
                time_value = normalized_text
        output.append(
            {
                "kind": swing.get("kind"),
                "price": swing.get("price"),
                "time": time_value,
            }
        )
    return output


def _simplify_liquidity_targets(targets: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    if not isinstance(targets, list):
        return output
    for t in targets[:MAX_LIQUIDITY_TARGETS]:
        if not isinstance(t, dict):
            continue
        price = safe_float(t.get("price"))
        output.append(
            {
                "role": t.get("role"),
                "tf": t.get("tf"),
                "side": t.get("side"),
                "price": price,
                "type": t.get("type") or t.get("kind"),
                "strength": safe_float(t.get("strength")),
            }
        )
    return output


def _simplify_ranges(ranges: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    if not isinstance(ranges, list):
        return output
    for rng in ranges[-MAX_RANGES:]:
        if not isinstance(rng, dict):
            continue
        start_value = rng.get("start_time")
        end_value = rng.get("end_time")
        if isinstance(start_value, str):
            normalized_text = try_iso_to_human_utc(start_value)
            if normalized_text:
                start_value = normalized_text
        if isinstance(end_value, str):
            normalized_text = try_iso_to_human_utc(end_value)
            if normalized_text:
                end_value = normalized_text
        output.append(
            {
                "high": rng.get("high"),
                "low": rng.get("low"),
                "state": rng.get("state"),
                "start": start_value,
                "end": end_value,
            }
        )
    return output


def _simplify_otes(otes: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    if not isinstance(otes, list):
        return output
    for zone in otes[-MAX_OTE_ZONES:]:
        if not isinstance(zone, dict):
            continue
        output.append(
            {
                "direction": zone.get("direction"),
                "role": zone.get("role"),
                "ote_min": zone.get("ote_min"),
                "ote_max": zone.get("ote_max"),
            }
        )
    return output


def _simplify_pools(pools: Any) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    if not isinstance(pools, list):
        return output
    for pool in pools[:MAX_POOLS]:
        if not isinstance(pool, dict):
            continue
        output.append(
            {
                "level": pool.get("level"),
                "price": pool.get("price") or pool.get("level"),
                "liq_type": pool.get("liq_type") or pool.get("type"),
                "type": pool.get("type") or pool.get("liq_type"),
                "role": pool.get("role"),
                "strength": pool.get("strength"),
                "meta": pool.get("meta"),
            }
        )
    return output


def _zones_iou_1d(
    a_min: float | None,
    a_max: float | None,
    b_min: float | None,
    b_max: float | None,
) -> float:
    if a_min is None or a_max is None or b_min is None or b_max is None:
        return 0.0
    if a_max <= a_min or b_max <= b_min:
        return 0.0
    inter = max(0.0, min(a_max, b_max) - max(a_min, b_min))
    union = (a_max - a_min) + (b_max - b_min) - inter
    if union <= 0.0:
        return 0.0
    return float(inter / union)


def _zone_merge_group_key(zone: dict[str, Any]) -> tuple[str, str, str, str]:
    zt = str(zone.get("zone_type") or zone.get("kind") or zone.get("type") or "-")
    direction = str(zone.get("direction") or "-")
    role = str(zone.get("role") or "-")
    tf = str(zone.get("timeframe") or zone.get("tf") or "-")
    return zt, direction, role, tf


def _merge_overlapping_zone_list(
    zones_list: list[Any],
    *,
    iou_threshold: float,
) -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = [dict(z) for z in zones_list if isinstance(z, dict)]
    if len(zones) < 2:
        return zones

    thr = float(iou_threshold)
    if not (0.0 < thr <= 1.0):
        return zones

    zones.sort(
        key=lambda z: (
            _zone_merge_group_key(z),
            safe_float(z.get("price_min")) or 0.0,
            safe_float(z.get("price_max")) or 0.0,
            str(z.get("zone_id") or ""),
        )
    )

    reps: list[dict[str, Any]] = []
    rep_stack: list[int] = []
    rep_env_min: list[float | None] = []
    rep_env_max: list[float | None] = []
    rep_ids_sample: list[list[str]] = []

    for z in zones:
        group = _zone_merge_group_key(z)
        zmin = safe_float(z.get("price_min"))
        zmax = safe_float(z.get("price_max"))

        placed_idx: int | None = None
        if zmin is not None and zmax is not None:
            for i, rep in enumerate(reps):
                if _zone_merge_group_key(rep) != group:
                    continue
                rmin = safe_float(rep.get("price_min"))
                rmax = safe_float(rep.get("price_max"))
                if _zones_iou_1d(rmin, rmax, zmin, zmax) >= thr:
                    placed_idx = i
                    break

        if placed_idx is None:
            reps.append(z)
            rep_stack.append(1)
            rep_env_min.append(zmin)
            rep_env_max.append(zmax)
            zid = z.get("zone_id")
            rep_ids_sample.append([str(zid)] if isinstance(zid, str) and zid else [])
            continue

        rep = reps[placed_idx]
        rep_stack[placed_idx] = int(rep_stack[placed_idx]) + 1

        # Технічна порада (QA): canonical bounds не роздуваємо (щоб не робити "супер-зону").
        # Envelope bounds зберігаємо окремо в meta.envelope_* для tooltip/debug.
        cur_min = rep_env_min[placed_idx]
        cur_max = rep_env_max[placed_idx]
        if cur_min is None:
            rep_env_min[placed_idx] = zmin
        elif zmin is not None:
            rep_env_min[placed_idx] = min(float(cur_min), float(zmin))
        if cur_max is None:
            rep_env_max[placed_idx] = zmax
        elif zmax is not None:
            rep_env_max[placed_idx] = max(float(cur_max), float(zmax))

        zid = z.get("zone_id")
        if isinstance(zid, str) and zid:
            sample = rep_ids_sample[placed_idx]
            if zid not in sample and len(sample) < 3:
                sample.append(zid)

    # Додаємо `meta.stack` лише для кластерів (stack>1), щоб не засмічувати payload.
    out: list[dict[str, Any]] = []
    for i, (rep, stack_n) in enumerate(zip(reps, rep_stack, strict=False)):
        if int(stack_n) > 1:
            # Безпечна конвертація meta: якщо rep["meta"] не dict — використовуємо порожній dict,
            # і явно вказуємо тип, щоб уникнути помилок статичної типізації.
            raw_meta = rep.get("meta")
            meta_dict = raw_meta if isinstance(raw_meta, dict) else {}
            meta_out: dict[str, Any] = dict(meta_dict)
            meta_out["stack"] = int(stack_n)
            env_min = rep_env_min[i] if i < len(rep_env_min) else None
            env_max = rep_env_max[i] if i < len(rep_env_max) else None
            if env_min is not None:
                meta_out["envelope_min"] = float(env_min)
            if env_max is not None:
                meta_out["envelope_max"] = float(env_max)
            ids = rep_ids_sample[i] if i < len(rep_ids_sample) else []
            if ids:
                meta_out["merged_from_ids_sample"] = list(ids)
            rep["meta"] = meta_out
        out.append(rep)
    return out


def _merge_overlapping_zones(
    zones: dict[str, Any],
    *,
    iou_threshold: float,
) -> dict[str, Any]:
    if not isinstance(zones, dict):
        return zones

    out: dict[str, Any] = dict(zones)
    for key in (
        "zones",
        "active_zones",
        "poi_zones",
        "breaker_zones",
        "breaker_active_zones",
    ):
        raw = out.get(key)
        if not isinstance(raw, list) or len(raw) < 2:
            continue
        out[key] = _merge_overlapping_zone_list(raw, iou_threshold=iou_threshold)
    return out


def _zone_key(zone: dict[str, Any]) -> str:
    zid = zone.get("zone_id")
    if isinstance(zid, str) and zid:
        return f"zid:{zid}"

    zt = str(zone.get("zone_type") or zone.get("kind") or zone.get("type") or "-")
    direction = str(zone.get("direction") or "-")
    role = str(zone.get("role") or "-")
    tf = str(zone.get("timeframe") or zone.get("tf") or "-")
    pmin = safe_float(zone.get("price_min"))
    pmax = safe_float(zone.get("price_max"))
    pmin_q = "-" if pmin is None else f"{pmin:.2f}"
    pmax_q = "-" if pmax is None else f"{pmax:.2f}"
    return f"z:{zt}:{direction}:{role}:{tf}:{pmin_q}:{pmax_q}"


def _pool_key(pool: dict[str, Any]) -> str:
    liq_type = str(pool.get("liq_type") or pool.get("type") or "-")
    role = str(pool.get("role") or "-")
    meta = pool.get("meta") if isinstance(pool.get("meta"), dict) else {}
    side = str((meta or {}).get("side") or "-")
    lvl = safe_float(pool.get("level") or pool.get("price"))
    lvl_q = "-" if lvl is None else f"{lvl:.2f}"
    liq_upper = liq_type.upper()

    # Крок 4 (QA): canonical key через стабільні meta-id (коли доступні).
    # Це важливо для «merge/update» семантики (менше remove+create у presentation).
    if liq_upper == "WICK_CLUSTER":
        cid = (meta or {}).get("cluster_id")
        if isinstance(cid, str) and cid:
            return f"p:{liq_type}:{role}:{side}:cid:{cid}"
        wc_id = (meta or {}).get("wick_cluster_id")
        if isinstance(wc_id, str) and wc_id:
            return f"p:{liq_type}:{role}:{side}:wid:{wc_id}"

    if liq_upper == "RANGE_EXTREME":
        rid = (meta or {}).get("range_extreme_id")
        if isinstance(rid, str) and rid:
            return f"p:{liq_type}:{role}:{side}:rid:{rid}"

    if liq_upper in ("SFP", "SFP_WICK", "SFP_WICK_CLUSTER"):
        sid = (meta or {}).get("sfp_id")
        if isinstance(sid, str) and sid:
            return f"p:{liq_type}:{role}:{side}:sid:{sid}"

    return f"p:{liq_type}:{role}:{side}:{lvl_q}"


def _filter_newborn_zones(
    zones: dict[str, Any],
    *,
    cache: ViewerStateCache | None,
    is_preview: bool,
    min_close_steps_before_show: int,
) -> dict[str, Any]:
    if not isinstance(zones, dict) or cache is None:
        return zones

    min_steps = max(0, int(min_close_steps_before_show))
    if min_steps <= 0:
        return zones

    close_step = int(cache.close_step or 0)

    def _keep_zone(z: dict[str, Any]) -> bool:
        key = _zone_key(z)
        born = cache.born_step_by_key.get(key)
        if born is None:
            if is_preview:
                return False
            cache.born_step_by_key[key] = close_step
            born = close_step
        age = close_step - int(born)
        return age >= min_steps

    out: dict[str, Any] = dict(zones)
    for key in (
        "zones",
        "active_zones",
        "poi_zones",
        "breaker_zones",
        "breaker_active_zones",
    ):
        raw = out.get(key)
        if not isinstance(raw, list):
            continue
        filtered: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            if _keep_zone(item):
                filtered.append(item)
        out[key] = filtered
    return out


def _filter_newborn_pools(
    pools: Any,
    *,
    cache: ViewerStateCache | None,
    is_preview: bool,
    min_close_steps_before_show: int,
) -> tuple[list[dict[str, Any]], int]:
    if cache is None:
        out = (
            [p for p in pools if isinstance(p, dict)] if isinstance(pools, list) else []
        )
        return out, 0
    if not isinstance(pools, list):
        return [], 0

    min_steps = max(0, int(min_close_steps_before_show))
    if min_steps <= 0:
        out = [p for p in pools if isinstance(p, dict)]
        return out, 0

    close_step = int(cache.close_step or 0)

    out: list[dict[str, Any]] = []
    filtered_newborn = 0
    for p in pools:
        if not isinstance(p, dict):
            continue
        key = _pool_key(p)
        born = cache.born_step_by_key.get(key)
        if born is None:
            if is_preview:
                filtered_newborn += 1
                continue
            cache.born_step_by_key[key] = close_step
            born = close_step
        age = close_step - int(born)
        if age >= min_steps:
            out.append(p)
        else:
            filtered_newborn += 1
    return out, int(filtered_newborn)


def _persist_events(
    events: list[dict[str, Any]],
    cache: ViewerStateCache | None,
) -> list[dict[str, Any]]:
    """Зберігаємо останні події, щоб уникати «спалахів» порожнього списку."""

    if events:
        if cache is not None:
            cache.last_events = [dict(event) for event in events]
        return events
    if cache is not None and cache.last_events:
        return [dict(event) for event in cache.last_events]
    return []


def _persist_zones(
    zones: dict[str, Any],
    cache: ViewerStateCache | None,
) -> dict[str, Any]:
    """Бекфіл зон, якщо в новому пейлоаді вони тимчасово відсутні."""

    if zones:
        if cache is not None:
            cache.last_zones_raw = dict(zones)
        return zones
    if cache is not None and cache.last_zones_raw:
        return dict(cache.last_zones_raw)
    return {}


def _extract_price(asset: dict[str, Any], stats: dict[str, Any]) -> float | None:
    """Вибір ціни для viewer_state з кількох кандидатів.

    Спираємось на те, що publish_smc_state вже робить основну нормалізацію,
    тому тут достатньо простого порядку пріоритетів.
    """

    numeric_candidates = [
        stats.get("current_price"),
        asset.get("price"),
        asset.get("last_price"),
        stats.get("last_price"),
    ]
    for candidate in numeric_candidates:
        price = safe_float(candidate)
        if price is not None:
            return price
    return None


def _resolve_session(asset: dict[str, Any], stats: dict[str, Any]) -> str | None:
    """Оцінка поточної сесії на базі stats/asset."""

    candidates = (
        stats.get("session_tag"),
        stats.get("session"),
        asset.get("session"),
        asset.get("session_tag"),
    )
    for candidate in candidates:
        if candidate:
            return str(candidate)
    return None


def _resolve_fxcm_source(
    payload_meta: dict[str, Any],
    fxcm_block: FxcmMeta | None,
    cache: ViewerStateCache | None,
) -> FxcmMeta | None:
    """Вибираємо джерело FXCM-стану з пріоритетом:

    1) явний fxcm_block (переданий зверху),
    2) payload_meta["fxcm"],
    3) кеш останнього стану.
    """

    if isinstance(fxcm_block, dict):
        return fxcm_block

    fxcm_meta = payload_meta.get("fxcm")
    if isinstance(fxcm_meta, dict):
        return fxcm_meta  # type: ignore[return-value]

    if cache is not None and cache.last_fxcm_meta is not None:
        return cache.last_fxcm_meta

    return None
