"""Побудова агрегованого SmcViewerState з UiSmcStatePayload.

Цей модуль не має залежностей від Rich/консолі й може використовуватися
як у консольному viewer'i, так і в HTTP/WS-серверах.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

from UI_v2.schemas import (
    VIEWER_STATE_SCHEMA_VERSION,
    FxcmMeta,
    SmcViewerState,
    UiSmcAssetPayload,
    UiSmcMeta,
)

# Обмеження розмірів списків у viewer_state, щоб state залишався легковаговим.
MAX_EVENTS: int = 20
MAX_LEGS: int = 6
MAX_SWINGS: int = 6
MAX_RANGES: int = 5
MAX_OTE_ZONES: int = 6
MAX_POOLS: int = 8


@dataclass
class ViewerStateCache:
    """Невеликий кеш для бекфілу подій/зон/FXCM-стану.

    Це дозволяє уникати «мигання» UI, коли в новому пейлоаді немає
    свіжих подій або зон, але їхній попередній стан іще актуальний.
    """

    last_events: list[dict[str, Any]] = field(default_factory=list)
    last_zones_raw: dict[str, Any] = field(default_factory=dict)
    last_fxcm_meta: FxcmMeta | None = None


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
    payload_meta_dict: dict[str, Any] = _as_dict(payload_meta)

    smc_hint = _as_dict(asset_dict.get("smc_hint"))
    smc_structure = _as_dict(
        asset_dict.get("smc_structure") or smc_hint.get("structure")
    )
    smc_liquidity = _as_dict(
        asset_dict.get("smc_liquidity") or smc_hint.get("liquidity")
    )
    smc_zones = _as_dict(asset_dict.get("smc_zones") or smc_hint.get("zones"))

    stats = _as_dict(asset_dict.get("stats"))

    price_value = _extract_price(asset_dict, stats)

    raw_events = _simplify_events(smc_structure.get("events"))
    events = _persist_events(raw_events, cache)
    zones_raw = _persist_zones(smc_zones, cache)

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

    structure_block = {
        "trend": smc_structure.get("trend"),
        "bias": smc_structure.get("bias"),
        "range_state": smc_structure.get("range_state"),
        "legs": _simplify_legs(smc_structure.get("legs")),
        "swings": _simplify_swings(smc_structure.get("swings")),
        "ranges": _simplify_ranges(smc_structure.get("ranges")),
        "events": events,
        "ote_zones": _simplify_otes(smc_structure.get("ote_zones")),
    }

    liquidity_block = {
        "amd_phase": smc_liquidity.get("amd_phase"),
        "pools": _simplify_pools(smc_liquidity.get("pools")),
        # Магніти поки передаємо «як є», без додаткової агрегації.
        "magnets": smc_liquidity.get("magnets") or [],
    }

    symbol_value = asset_dict.get("symbol")
    symbol_norm = str(symbol_value).upper() if symbol_value else None

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
        "zones": {"raw": zones_raw},
    }

    if fxcm_source is not None:
        viewer_state["fxcm"] = fxcm_source

    return viewer_state


# ── Допоміжні функції --------------------------------------------------------


def _as_dict(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    return {}


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_utc_from_ms(value: Any) -> str | None:
    millis = _safe_int(value)
    if millis is None:
        return None
    seconds, remainder = divmod(millis, 1000)
    dt = datetime.fromtimestamp(seconds, tz=UTC)
    dt = dt.replace(microsecond=remainder * 1000)
    return dt.isoformat()


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
        output.append(
            {
                "kind": swing.get("kind"),
                "price": swing.get("price"),
                "time": swing.get("time"),
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
        output.append(
            {
                "high": rng.get("high"),
                "low": rng.get("low"),
                "state": rng.get("state"),
                "start": rng.get("start_time"),
                "end": rng.get("end_time"),
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
        price = _safe_float(candidate)
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
