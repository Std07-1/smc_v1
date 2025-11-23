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
    amd_phase = (
        liquidity.amd_phase.name if getattr(liquidity, "amd_phase", None) else "NEUTRAL"
    )

    hint: dict[str, Any] = {
        "smc_liq_has_above": has_above,
        "smc_liq_has_below": has_below,
        "smc_liq_dist_to_primary": distance,
        "smc_liq_amd_phase": amd_phase,
    }
    if price is not None:
        hint["smc_liq_ref_price"] = price
    if primary_magnets:
        hint["smc_liq_primary_magnets"] = len(primary_magnets)
    if liquidity.meta:
        hint["smc_liq_meta"] = {"amd_reason": liquidity.meta.get("amd_reason")}
    return hint


def _extract_price(smc_hint: SmcHint) -> float | None:
    meta = smc_hint.meta or {}
    candidate = meta.get("last_price") or meta.get("price")
    if candidate is None and smc_hint.structure and smc_hint.structure.meta:
        candidate = smc_hint.structure.meta.get("last_price")
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
