"""Публікація SMC-only стану в Redis канал для UI."""

from __future__ import annotations

import logging
import math
import time
from collections import Counter
from typing import Any, Protocol

from redis.asyncio import Redis

from config.config import (
    REDIS_CHANNEL_SMC_STATE,
    REDIS_SNAPSHOT_KEY_SMC,
    UI_SMC_PAYLOAD_SCHEMA_VERSION,
    UI_SMC_SNAPSHOT_TTL_SEC,
)
from core.formatters import fmt_price_stage1, fmt_volume_usd
from core.serialization import json_dumps, utc_now_iso_z
from core.serialization import safe_float

try:  # pragma: no cover - best-effort залежність
    from smc_core.serializers import to_plain_smc_hint as _core_plain_smc_hint
except Exception:  # pragma: no cover
    _core_plain_smc_hint = None

logger = logging.getLogger("ui.publish_smc_state")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler())
    logger.propagate = False

_SEQ = 0
_CORE_SERIALIZER_MISSING_LOGGED = False
_LAST_REDIS_PUBLISH_ERROR_TS: float | None = None


class SmcStateProvider(Protocol):
    def get_all_assets(self) -> list[dict[str, Any]]:  # pragma: no cover - typing only
        ...


def _format_price_for_symbol(value: Any, symbol_lower: str) -> str:
    price = safe_float(value)
    if price is None or price <= 0:
        return "-"
    try:
        fmt_value = fmt_price_stage1(float(price), symbol_lower)
    except Exception:
        return "-"
    if fmt_value == "-":
        return "-"
    return f"{fmt_value} USD"


def _format_tick_age(age_sec: Any) -> str:
    """Повертає компактний рядок віку тіку."""

    age = safe_float(age_sec)
    if age is None or age < 0:
        return "-"
    if age < 1.0:
        return f"{age * 1000:.0f} мс"
    if age < 90.0:
        return f"{age:.1f} с"
    return f"{age / 60.0:.1f} хв"


def _extract_fxcm_meta(cache_handler: object) -> dict[str, Any] | None:
    """Повертає FXCM блок із metrics_snapshot(), якщо доступний."""

    metrics_fn = getattr(cache_handler, "metrics_snapshot", None)
    if not callable(metrics_fn):
        return None
    try:
        snapshot = metrics_fn()
    except Exception:
        logger.debug(
            "[SMC] Не вдалося отримати metrics_snapshot() для FXCM", exc_info=True
        )
        return None
    fxcm_block = snapshot.get("fxcm") if isinstance(snapshot, dict) else None
    if isinstance(fxcm_block, dict):
        return dict(fxcm_block)
    return None


async def publish_smc_state(  # type: ignore
    state_manager: SmcStateProvider,
    cache_handler: object,
    redis_conn: Redis[str],
    *,
    meta_extra: dict[str, Any] | None = None,
) -> None:
    """Публікуємо SMC-стан в окремий Redis канал та снапшот."""

    global _SEQ
    assets = state_manager.get_all_assets()
    dedup: dict[str, dict[str, Any]] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        symbol = str(asset.get("symbol") or "").upper()
        if not symbol:
            continue
        dedup[symbol] = asset
    serialized: list[dict[str, Any]] = []
    amd_phase_counter: Counter[str] = Counter()
    bias_counter: Counter[str] = Counter()
    magnet_counts: list[int] = []
    pool_counts: list[int] = []
    active_zone_counts: list[int] = []
    latency_samples: list[float] = []
    for asset in dedup.values():
        stats = asset.get("stats")
        if not isinstance(stats, dict):
            stats = {}
            asset["stats"] = stats

        symbol_lower = str(asset.get("symbol") or "").lower()
        cp = safe_float(stats.get("current_price"))
        if cp is not None and cp > 0:
            asset["price"] = cp
            price_str = _format_price_for_symbol(cp, symbol_lower)
            if price_str != "-":
                asset["price_str"] = price_str
        else:
            asset.pop("price", None)
            asset.pop("price_str", None)

        for key in ("live_price_mid", "live_price_bid", "live_price_ask"):
            value = safe_float(stats.get(key))
            if value is not None and value > 0:
                asset[key] = value
                value_str = _format_price_for_symbol(value, symbol_lower)
                if value_str != "-":
                    asset[f"{key}_str"] = value_str
            else:
                asset.pop(key, None)
                asset.pop(f"{key}_str", None)

        spread_val = safe_float(stats.get("live_price_spread"))
        if spread_val is not None and spread_val >= 0:
            asset["live_price_spread"] = spread_val
        else:
            asset.pop("live_price_spread", None)

        tick_age_val = safe_float(stats.get("tick_age_sec"))
        if tick_age_val is not None and tick_age_val >= 0:
            asset["tick_age_sec"] = tick_age_val
            asset["tick_age_str"] = _format_tick_age(tick_age_val)
        else:
            asset.pop("tick_age_sec", None)
            asset.pop("tick_age_str", None)

        raw_volume = safe_float(stats.get("volume"))
        if raw_volume is not None and raw_volume > 0 and cp is not None:
            asset["volume"] = float(raw_volume)
            try:
                asset["volume_str"] = format_volume_usd(float(raw_volume))
            except Exception:
                asset.pop("volume_str", None)
        else:
            asset.pop("volume", None)
            asset.pop("volume_str", None)

        latency_val = safe_float(stats.get("smc_latency_ms"))
        if latency_val is not None:
            latency_samples.append(latency_val)

        _prepare_smc_hint(asset)

        hint_block = asset.get("smc_hint")
        if isinstance(hint_block, dict):
            struct_block = hint_block.get("structure")
            if isinstance(struct_block, dict):
                bias_value = struct_block.get("bias")
                if isinstance(bias_value, str) and bias_value:
                    bias_counter[bias_value.upper()] += 1
            liq_block = hint_block.get("liquidity")
            if isinstance(liq_block, dict):
                amd_phase = liq_block.get("amd_phase")
                if isinstance(amd_phase, str) and amd_phase:
                    amd_phase_counter[amd_phase.upper()] += 1
                pools = liq_block.get("pools")
                if isinstance(pools, list):
                    pool_counts.append(len(pools))
                magnets = liq_block.get("magnets")
                if isinstance(magnets, list):
                    magnet_counts.append(len(magnets))
            zones_block = hint_block.get("zones")
            if isinstance(zones_block, dict):
                active = zones_block.get("active_zones")
                if isinstance(active, list):
                    active_zone_counts.append(len(active))
        serialized.append(asset)

    _SEQ = (_SEQ + 1) if _SEQ < 2**31 - 1 else 1
    seq_value = meta_extra.get("cycle_seq") if meta_extra else None
    if isinstance(seq_value, (int, float)):
        seq = int(seq_value)
        _SEQ = seq
    else:
        seq = _SEQ

    fxcm_meta = _extract_fxcm_meta(cache_handler)

    payload = {
        "type": REDIS_CHANNEL_SMC_STATE,
        "meta": {
            "ts": utc_now_iso_z(),
            "seq": seq,
            "schema_version": UI_SMC_PAYLOAD_SCHEMA_VERSION,
        },
        "counters": {"assets": len(serialized)},
        "assets": serialized,
    }
    if meta_extra:
        payload["meta"].update(meta_extra)
        payload["meta"].setdefault("cycle_seq", seq)
    else:
        payload["meta"]["cycle_seq"] = seq
    if fxcm_meta:
        payload["meta"]["fxcm"] = fxcm_meta
        payload["fxcm"] = fxcm_meta

    analytics: dict[str, Any] = {}
    if amd_phase_counter:
        analytics["amd_phase_counts"] = dict(amd_phase_counter)
    if bias_counter:
        analytics["bias_counts"] = dict(bias_counter)
    if pool_counts:
        analytics["avg_pools_per_asset"] = round(sum(pool_counts) / len(pool_counts), 2)
        analytics["max_pools_per_asset"] = max(pool_counts)
    if magnet_counts:
        analytics["avg_magnets_per_asset"] = round(
            sum(magnet_counts) / len(magnet_counts), 2
        )
        analytics["max_magnets_per_asset"] = max(magnet_counts)
    if active_zone_counts:
        analytics["avg_active_zones"] = round(
            sum(active_zone_counts) / len(active_zone_counts), 2
        )
        analytics["max_active_zones"] = max(active_zone_counts)
    if latency_samples:
        analytics["smc_latency_ms_avg"] = round(
            sum(latency_samples) / len(latency_samples), 2
        )
        analytics["smc_latency_ms_max"] = round(max(latency_samples), 2)
    if analytics:
        payload["analytics"] = analytics

    payload_json = json_dumps(payload)

    async def _set_snapshot() -> None:
        try:
            await redis_conn.set(name=REDIS_SNAPSHOT_KEY_SMC, value=payload_json)
            await redis_conn.expire(
                name=REDIS_SNAPSHOT_KEY_SMC, time=UI_SMC_SNAPSHOT_TTL_SEC
            )
        except Exception:
            logger.debug("[SMC] Не вдалося оновити snapshot", exc_info=True)

    await _set_snapshot()
    try:
        await redis_conn.publish(REDIS_CHANNEL_SMC_STATE, payload_json)
    except Exception:
        # Redis може коротко пропадати під час docker update/restart.
        # Важливо не вбивати smc_producer, а тихо пережити та продовжити цикл.
        global _LAST_REDIS_PUBLISH_ERROR_TS
        now = time.time()
        last = _LAST_REDIS_PUBLISH_ERROR_TS
        _LAST_REDIS_PUBLISH_ERROR_TS = now
        if last is None or (now - last) > 30.0:
            logger.warning(
                "[SMC] Redis недоступний — не вдалося опублікувати smc_state (seq=%s)",
                seq,
                exc_info=True,
            )
        else:
            logger.debug(
                "[SMC] Redis недоступний — пропускаю publish smc_state (seq=%s)",
                seq,
                exc_info=True,
            )
        return

    logger.debug("[SMC] Опубліковано %d активів", len(serialized))


def _prepare_smc_hint(asset: dict[str, Any]) -> None:
    """Нормалізує smc_hint та повʼязані блоки в активі."""

    hint_obj = asset.get("smc_hint")
    stats_obj = asset.get("stats")
    if hint_obj is None and isinstance(stats_obj, dict):
        hint_obj = stats_obj.get("smc_hint")
        stats_obj.pop("smc_hint", None)

    if hint_obj is None:
        for key in ("smc", "smc_hint", "smc_structure", "smc_liquidity", "smc_zones"):
            asset.pop(key, None)
        return

    plain_hint: Any
    if isinstance(hint_obj, dict):
        plain_hint = hint_obj
    else:
        plain_hint = _plain_smc_hint_via_core(hint_obj)

    if plain_hint is None:
        for key in ("smc", "smc_hint", "smc_structure", "smc_liquidity", "smc_zones"):
            asset.pop(key, None)
        return

    if not isinstance(plain_hint, dict):
        plain_hint = {"value": plain_hint}

    reference_price = None
    if isinstance(stats_obj, dict):
        reference_price = safe_float(stats_obj.get("current_price"))
        if reference_price is None:
            reference_price = safe_float(stats_obj.get("price"))
    if reference_price is None:
        reference_price = _extract_reference_from_hint(plain_hint)

    _normalize_smc_prices(plain_hint, reference_price)

    asset["smc"] = plain_hint
    asset["smc_hint"] = plain_hint

    structure_plain = plain_hint.get("structure")
    if structure_plain:
        asset["smc_structure"] = structure_plain
    else:
        asset.pop("smc_structure", None)

    zones_plain = plain_hint.get("zones")
    if zones_plain:
        asset["smc_zones"] = zones_plain
    else:
        asset.pop("smc_zones", None)

    liq_source = plain_hint.get("liquidity")
    if liq_source is None:
        liq_source = getattr(hint_obj, "liquidity", None)
    liq_plain = _to_plain_smc_liquidity(liq_source)
    if liq_plain is not None:
        asset["smc_liquidity"] = liq_plain
    else:
        asset.pop("smc_liquidity", None)


def _plain_smc_hint_via_core(hint_obj: Any) -> Any:
    """Повертає plain-подання hint через smc_core.serializers."""

    if hint_obj is None:
        return None
    if isinstance(hint_obj, dict):
        return hint_obj

    global _CORE_SERIALIZER_MISSING_LOGGED
    if _core_plain_smc_hint is None:
        if not _CORE_SERIALIZER_MISSING_LOGGED:
            logger.warning(
                "smc_core.serializers.to_plain_smc_hint недоступний — smc_hint пропущено"
            )
            _CORE_SERIALIZER_MISSING_LOGGED = True
        return None

    try:
        return _core_plain_smc_hint(hint_obj)
    except Exception:  # pragma: no cover - захисний контур
        logger.exception("Не вдалося серіалізувати smc_hint через smc_core")
        return None


def _extract_reference_from_hint(plain_hint: dict[str, Any]) -> float | None:
    if not isinstance(plain_hint, dict):
        return None
    candidates: tuple[tuple[str, ...], ...] = (
        ("structure", "meta", "snapshot_last_close"),
        ("structure", "meta", "last_price"),
        ("meta", "last_price"),
    )
    for path in candidates:
        cursor: Any = plain_hint
        for key in path:
            if not isinstance(cursor, dict):
                cursor = None
                break
            cursor = cursor.get(key)
        ref = safe_float(cursor)
        if ref is not None:
            return ref
    return None


def _normalize_smc_prices(
    plain_hint: dict[str, Any], reference_price: float | None
) -> None:
    ref = safe_float(reference_price)
    if ref is None or ref == 0:
        return
    structure_block = plain_hint.get("structure")
    if isinstance(structure_block, dict):
        _normalize_structure_prices(structure_block, ref)
    liquidity_block = plain_hint.get("liquidity")
    if isinstance(liquidity_block, dict):
        _normalize_liquidity_prices(liquidity_block, ref)
    zones_block = plain_hint.get("zones")
    if isinstance(zones_block, dict):
        _normalize_zone_prices(zones_block, ref)


def _normalize_structure_prices(structure: dict[str, Any], ref: float) -> None:
    _normalize_list_fields(structure.get("swings"), ("price",), ref)
    _normalize_list_fields(structure.get("ranges"), ("high", "low", "eq_level"), ref)
    active_range = structure.get("active_range")
    if isinstance(active_range, dict):
        _normalize_fields(active_range, ("high", "low", "eq_level"), ref)
    _normalize_list_fields(structure.get("events"), ("price_level",), ref)
    _normalize_list_fields(structure.get("ote_zones"), ("ote_min", "ote_max"), ref)
    _normalize_legs(structure.get("legs"), ref)


def _normalize_liquidity_prices(liq: dict[str, Any], ref: float) -> None:
    _normalize_list_fields(liq.get("pools"), ("level",), ref)
    _normalize_list_fields(
        liq.get("magnets"), ("price_min", "price_max", "center"), ref
    )


def _normalize_zone_prices(zones: dict[str, Any], ref: float) -> None:
    for key in (
        "zones",
        "active_zones",
        "poi_zones",
        "breaker_zones",
        "breaker_active_zones",
    ):
        _normalize_list_fields(
            zones.get(key), ("price_min", "price_max", "entry_hint", "stop_hint"), ref
        )


def _normalize_legs(legs: Any, ref: float) -> None:
    if not isinstance(legs, list):
        return
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        for swing_key in ("from_swing", "to_swing"):
            swing = leg.get(swing_key)
            if isinstance(swing, dict):
                _normalize_fields(swing, ("price",), ref)


def _normalize_list_fields(items: Any, fields: tuple[str, ...], ref: float) -> None:
    if not isinstance(items, list):
        return
    for item in items:
        if isinstance(item, dict):
            _normalize_fields(item, fields, ref)


def _normalize_fields(
    target: dict[str, Any], fields: tuple[str, ...], ref: float
) -> None:
    for field in fields:
        if field not in target:
            continue
        normalized = _maybe_rescale_price(target.get(field), ref)
        if normalized is not None:
            target[field] = normalized


def _maybe_rescale_price(value: Any, reference: float) -> float | None:
    price = safe_float(value)
    if price is None:
        return None
    return _rescale_price(price, reference)


def _rescale_price(price: float, reference: float) -> float:
    ref_abs = abs(reference)
    if ref_abs == 0:
        return price
    price_abs = abs(price)
    if price_abs == 0:
        return price
    ratio = ref_abs / price_abs
    if 0.2 <= ratio <= 5:
        return price
    if ratio > 5:
        candidate = _apply_scale(price, ratio, multiply=True, reference=reference)
        if candidate is not None:
            return candidate
    inv_ratio = price_abs / ref_abs
    if inv_ratio > 5:
        candidate = _apply_scale(price, inv_ratio, multiply=False, reference=reference)
        if candidate is not None:
            return candidate
    return price


def _apply_scale(
    price: float, ratio: float, *, multiply: bool, reference: float
) -> float | None:
    power = _round_power_of_ten(ratio)
    if power is None:
        return None
    scale = 10**power
    candidate = price * scale if multiply else price / scale
    if _is_within_magnitude(candidate, reference):
        return candidate
    return None


def _round_power_of_ten(value: float) -> int | None:
    if value <= 0:
        return None
    log_val = math.log10(value)
    power = int(round(log_val))
    if power == 0 or abs(power) > 6:
        return None
    if abs(log_val - power) > 0.2:
        return None
    return power


def _is_within_magnitude(candidate: float, reference: float) -> bool:
    ref_abs = abs(reference)
    if ref_abs == 0:
        return False
    ratio = abs(candidate) / ref_abs
    return 0.2 <= ratio <= 5


def _to_plain_smc_liquidity(liq_state: Any | None) -> dict[str, Any] | None:
    """Конвертує SmcLiquidityState-подібні обʼєкти у plain dict."""

    if liq_state is None:
        return None
    if isinstance(liq_state, dict):
        return liq_state

    pools_plain = [_serialize_pool(pool) for pool in getattr(liq_state, "pools", [])]
    magnets_plain = [
        _serialize_magnet(magnet) for magnet in getattr(liq_state, "magnets", [])
    ]
    amd_phase = _enum_name(getattr(liq_state, "amd_phase", None))
    meta_block = getattr(liq_state, "meta", {})
    meta_plain = dict(meta_block) if isinstance(meta_block, dict) else {}

    return {
        "pools": pools_plain,
        "magnets": magnets_plain,
        "amd_phase": amd_phase,
        "meta": meta_plain,
    }


def _serialize_pool(pool: Any) -> dict[str, Any]:
    meta_block = getattr(pool, "meta", {})
    return {
        "level": safe_float(getattr(pool, "level", None)),
        "liq_type": _enum_name(getattr(pool, "liq_type", None)),
        "strength": safe_float(getattr(pool, "strength", None)),
        "n_touches": getattr(pool, "n_touches", None),
        "role": getattr(pool, "role", None),
        "first_time": _ts_to_iso(getattr(pool, "first_time", None)),
        "last_time": _ts_to_iso(getattr(pool, "last_time", None)),
        "meta": dict(meta_block) if isinstance(meta_block, dict) else {},
    }


def _serialize_magnet(magnet: Any) -> dict[str, Any]:
    meta_block = getattr(magnet, "meta", {})
    return {
        "price_min": safe_float(getattr(magnet, "price_min", None)),
        "price_max": safe_float(getattr(magnet, "price_max", None)),
        "center": safe_float(getattr(magnet, "center", None)),
        "liq_type": _enum_name(getattr(magnet, "liq_type", None)),
        "role": getattr(magnet, "role", None),
        "meta": dict(meta_block) if isinstance(meta_block, dict) else {},
    }


def _enum_name(value: Any) -> str | None:
    if value is None:
        return None
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    if isinstance(value, str):
        return value
    return str(value)


def _ts_to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return str(value)
