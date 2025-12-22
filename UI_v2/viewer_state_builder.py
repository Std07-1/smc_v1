"""Побудова агрегованого SmcViewerState з UiSmcStatePayload.

Цей модуль не має залежностей від Rich/консолі й може використовуватися
як у консольному viewer'i, так і в HTTP/WS-серверах.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from core.contracts.viewer_state import (
    VIEWER_STATE_SCHEMA_VERSION,
    FxcmMeta,
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

# Випадок C (QA): не показуємо «новонароджене», доки не пройде мінімум N close-кроків.
# N=1 означає: створилось на close N, показуємо починаючи з close N+1.
MIN_CLOSE_STEPS_BEFORE_SHOW: int = 1


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
            scenario_block = {
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
                "switch_delta": safe_float(stats.get("scenario_switch_delta")) or 0.0,
                "anti_flip": coerce_dict(stats.get("scenario_anti_flip")),
                "last_eval": coerce_dict(stats.get("scenario_last_eval")),
            }

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
        min_close_steps_before_show=MIN_CLOSE_STEPS_BEFORE_SHOW,
    )
    zones_raw = _persist_zones(smc_zones_filtered, cache)

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

    liquidity_block: SmcViewerLiquidity = cast(
        SmcViewerLiquidity,
        {
            "amd_phase": smc_liquidity.get("amd_phase"),
            "pools": _simplify_pools(
                _filter_newborn_pools(
                    smc_liquidity.get("pools"),
                    cache=cache,
                    is_preview=is_preview,
                    min_close_steps_before_show=MIN_CLOSE_STEPS_BEFORE_SHOW,
                )
            ),
            # Магніти поки передаємо «як є», без додаткової агрегації.
            "magnets": smc_liquidity.get("magnets") or [],
            "targets": _simplify_liquidity_targets(
                coerce_dict(smc_liquidity.get("meta")).get("liquidity_targets")
            ),
        },
    )

    zones_block: SmcViewerZones = cast(SmcViewerZones, {"raw": zones_raw})

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

    viewer_state: SmcViewerState = {
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
        **({"execution": execution_block} if execution_block else {}),
        "pipeline_local": pipeline_local,
    }

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
    side = str((pool.get("meta") or {}).get("side") or "-")
    lvl = safe_float(pool.get("level") or pool.get("price"))
    lvl_q = "-" if lvl is None else f"{lvl:.2f}"
    if liq_type.upper() == "WICK_CLUSTER":
        cid = (pool.get("meta") or {}).get("cluster_id")
        if isinstance(cid, str) and cid:
            return f"p:{liq_type}:{role}:{side}:cid:{cid}"
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
) -> Any:
    if cache is None:
        return pools
    if not isinstance(pools, list):
        return pools

    min_steps = max(0, int(min_close_steps_before_show))
    if min_steps <= 0:
        return pools

    close_step = int(cache.close_step or 0)

    out: list[dict[str, Any]] = []
    for p in pools:
        if not isinstance(p, dict):
            continue
        key = _pool_key(p)
        born = cache.born_step_by_key.get(key)
        if born is None:
            if is_preview:
                continue
            cache.born_step_by_key[key] = close_step
            born = close_step
        age = close_step - int(born)
        if age >= min_steps:
            out.append(p)
    return out


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
