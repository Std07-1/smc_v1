"""Легкий міст для витягання ключових SMC-liquidity ознак у Stage2."""

from __future__ import annotations

from typing import Any

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import SmcHint, SmcLiquidityMagnet


def build_liquidity_hint(
    smc_hint: SmcHint | None, cfg: SmcCoreConfig
) -> dict[str, Any]:
    """Формує Stage2-friendly хінт з агрегованих даних ліквідності."""

    _ = cfg  # зарезервовано для майбутніх адаптивних порогів
    if smc_hint is None or smc_hint.liquidity is None:
        return {}

    liquidity = smc_hint.liquidity
    price = _extract_price(smc_hint)
    primary_magnets = [m for m in liquidity.magnets or [] if m.role == "PRIMARY"]

    has_above = bool(
        price is not None and any(m.center > price for m in primary_magnets)
    )
    has_below = bool(
        price is not None and any(m.center < price for m in primary_magnets)
    )
    distance = _nearest_relative_distance(primary_magnets, price)
    amd_phase_raw = getattr(liquidity, "amd_phase", None)
    if amd_phase_raw is None:
        amd_phase = "NEUTRAL"
    elif hasattr(amd_phase_raw, "name"):
        amd_phase = amd_phase_raw.name
    else:
        amd_phase = str(amd_phase_raw)

    hint: dict[str, Any] = {
        "smc_liq_has_above": has_above,
        "smc_liq_has_below": has_below,
        "smc_liq_dist_to_primary": distance,
        "smc_liq_amd_phase": amd_phase,
    }

    # Acceptance (буквально, без брехні): ключі nearest завжди присутні,
    # але можуть бути None + why[]/confidence.
    hint["smc_liq_nearest_internal"] = None
    hint["smc_liq_nearest_external"] = None
    hint["smc_liq_nearest_internal_why"] = ["not_computed"]
    hint["smc_liq_nearest_external_why"] = ["not_computed"]
    hint["smc_liq_nearest_internal_confidence"] = 0.0
    hint["smc_liq_nearest_external_confidence"] = 0.0

    # Сесійний контекст (власні обчислення з OHLCV), якщо присутній у meta.
    meta = smc_hint.meta or {}
    for k in (
        "smc_session_tag",
        "smc_session_start_ms",
        "smc_session_end_ms",
        "smc_session_high",
        "smc_session_low",
        "smc_session_tf",
        "smc_sessions",
        "session_tag",  # legacy/сумісність
    ):
        if k in meta:
            hint[k] = meta.get(k)
    if price is None:
        hint["smc_liq_nearest_internal_why"] = ["no_ref_price"]
        hint["smc_liq_nearest_external_why"] = ["no_ref_price"]
    else:
        hint["smc_liq_ref_price"] = price

        targets = (liquidity.meta or {}).get("liquidity_targets")
        if isinstance(targets, list) and targets:
            nearest_internal = _pick_nearest_target(
                targets, role="internal", ref_price=price
            )
            nearest_external = _pick_nearest_target(
                targets, role="external", ref_price=price
            )

            if nearest_internal is not None:
                hint["smc_liq_nearest_internal"] = nearest_internal
                hint["smc_liq_nearest_internal_why"] = ["from:liquidity_targets"]
                hint["smc_liq_nearest_internal_confidence"] = 1.0
            else:
                hint["smc_liq_nearest_internal_why"] = ["no_candidates_internal"]

            if nearest_external is not None:
                hint["smc_liq_nearest_external"] = nearest_external
                hint["smc_liq_nearest_external_why"] = ["from:liquidity_targets"]
                hint["smc_liq_nearest_external_confidence"] = 1.0
            else:
                hint["smc_liq_nearest_external_why"] = ["no_candidates_external"]
        else:
            hint["smc_liq_nearest_internal_why"] = ["no_candidates_internal"]
            hint["smc_liq_nearest_external_why"] = ["no_candidates_external"]

        # Опційний fallback: дозволяє UI мати стабільні об'єкти для рендера,
        # але з низькою confidence і явним reason.
        if cfg.liquidity_nearest_fallback_enabled:
            if hint.get("smc_liq_nearest_internal") is None:
                fallback_internal = _fallback_internal_from_primary_magnets(
                    magnets=primary_magnets,
                    ref_price=price,
                    tf=str((liquidity.meta or {}).get("primary_tf") or "") or None,
                )
                if fallback_internal is not None:
                    hint["smc_liq_nearest_internal"] = fallback_internal
                    hint["smc_liq_nearest_internal_why"] = [
                        "fallback:nearest_primary_magnet"
                    ]
                    hint["smc_liq_nearest_internal_confidence"] = 0.1

            if hint.get("smc_liq_nearest_external") is None:
                fallback_external = _fallback_external_from_smc_sessions(
                    smc_sessions=meta.get("smc_sessions"),
                    ref_price=price,
                )
                if fallback_external is not None:
                    hint["smc_liq_nearest_external"] = fallback_external
                    hint["smc_liq_nearest_external_why"] = [
                        "fallback:smc_sessions_extreme"
                    ]
                    hint["smc_liq_nearest_external_confidence"] = 0.1
    if primary_magnets:
        hint["smc_liq_primary_magnets"] = len(primary_magnets)
    if liquidity.meta:
        hint["smc_liq_meta"] = {"amd_reason": liquidity.meta.get("amd_reason")}
    return hint


def _fallback_internal_from_primary_magnets(
    *,
    magnets: list[SmcLiquidityMagnet],
    ref_price: float,
    tf: str | None,
) -> dict[str, Any] | None:
    if not magnets:
        return None
    best: tuple[float, SmcLiquidityMagnet] | None = None
    for m in magnets:
        try:
            dist = abs(float(m.center) - float(ref_price))
        except (TypeError, ValueError):
            continue
        if best is None or dist < best[0]:
            best = (dist, m)
    if best is None:
        return None
    m = best[1]
    side = "above" if float(m.center) >= float(ref_price) else "below"
    return {
        "role": "internal",
        "tf": tf or "",
        "side": side,
        "price": float(m.center),
        "type": "MAGNET_PRIMARY",
        "strength": 0.0,
        "reason": ["fallback"],
    }


def _fallback_external_from_smc_sessions(
    *,
    smc_sessions: Any,
    ref_price: float,
) -> dict[str, Any] | None:
    if not isinstance(smc_sessions, dict):
        return None
    best: tuple[float, dict[str, Any]] | None = None
    for tag, payload in smc_sessions.items():
        if not isinstance(payload, dict):
            continue
        tf = payload.get("tf")
        for kind, key in (("SESSION_HIGH", "high"), ("SESSION_LOW", "low")):
            val = payload.get(key)
            if val is None:
                continue
            try:
                price = float(val)
                dist = abs(price - float(ref_price))
            except (TypeError, ValueError):
                continue
            candidate = {
                "role": "external",
                "tf": str(tf or ""),
                "side": "above" if price >= float(ref_price) else "below",
                "price": price,
                "type": kind,
                "strength": 0.0,
                "reason": [f"fallback:{tag}"],
            }
            if best is None or dist < best[0]:
                best = (dist, candidate)
    return None if best is None else best[1]


def _extract_price(smc_hint: SmcHint) -> float | None:
    meta = smc_hint.meta or {}
    candidate = meta.get("last_price") or meta.get("price")
    if candidate is None and smc_hint.structure and smc_hint.structure.meta:
        candidate = smc_hint.structure.meta.get("last_price")
    if candidate is None:
        return None
    try:
        return float(candidate)
    except (TypeError, ValueError):
        return None


def _nearest_relative_distance(
    magnets: list[SmcLiquidityMagnet], price: float | None
) -> float | None:
    if not magnets or price is None or price == 0:
        return None
    diffs = [abs(m.center - price) for m in magnets]
    if not diffs:
        return None
    nearest = min(diffs)
    return round(nearest / abs(price), 6)


def _pick_nearest_target(
    targets: list[dict[str, Any]],
    *,
    role: str,
    ref_price: float,
) -> dict[str, Any] | None:
    best: tuple[float, dict[str, Any]] | None = None
    for t in targets:
        if not isinstance(t, dict):
            continue
        if t.get("role") != role:
            continue
        candidate = t.get("price")
        if candidate is None:
            continue
        try:
            dist = abs(float(candidate) - float(ref_price))
        except (TypeError, ValueError):
            continue
        if best is None or dist < best[0]:
            best = (dist, t)
    return None if best is None else best[1]
