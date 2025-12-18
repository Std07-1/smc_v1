"""Детектори SFP та wick-кластерів поверх готової структури."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import (
    SmcInput,
    SmcLiquidityPool,
    SmcLiquidityType,
    SmcStructureState,
)

from .pools import resolve_role_for_bias

SFP_BREAK_FRACTION = 0.25
MIN_BREAK_PCT = 0.002
WICK_RATIO = 2.5


@dataclass(slots=True)
class _LevelInfo:
    level: float
    side: Literal["HIGH", "LOW"]
    source: str
    key: str


def detect_sfp_and_wicks(
    snapshot: SmcInput,
    structure: SmcStructureState,
    cfg: SmcCoreConfig,
) -> tuple[list[SmcLiquidityPool], list[dict[str, Any]], list[dict[str, Any]]]:
    """Повертає додаткові пули та метадані для SFP і wick-кластерів."""

    df = _prepare_price_frame(snapshot, cfg.max_lookback_bars)
    if df is None:
        return [], [], []

    levels = _collect_levels(structure)
    if not levels:
        return [], [], []

    tolerance_pct = max(cfg.eq_tolerance_pct, 0.001)
    break_pct = max(tolerance_pct * SFP_BREAK_FRACTION, MIN_BREAK_PCT)

    sfp_events: list[dict[str, Any]] = []
    extra_pools: list[SmcLiquidityPool] = []
    sfp_recorded: set[str] = set()

    wick_clusters: dict[str, dict[str, Any]] = {}

    timestamps = df["timestamp"].reset_index(drop=True)
    opens = df["open"].astype(float).reset_index(drop=True)
    highs = df["high"].astype(float).reset_index(drop=True)
    lows = df["low"].astype(float).reset_index(drop=True)
    closes = df["close"].astype(float).reset_index(drop=True)

    for idx in range(len(df)):
        ts = pd.Timestamp(timestamps.iloc[idx])
        open_price = float(opens.iloc[idx])
        high_price = float(highs.iloc[idx])
        low_price = float(lows.iloc[idx])
        close_price = float(closes.iloc[idx])
        body = max(abs(close_price - open_price), 1e-6)
        upper_wick = max(high_price - max(open_price, close_price), 0.0)
        lower_wick = max(min(open_price, close_price) - low_price, 0.0)

        for level in levels:
            price_tol = max(level.level * break_pct, MIN_BREAK_PCT)
            if (
                level.side == "HIGH"
                and high_price >= level.level + price_tol
                and close_price < level.level
                and level.key not in sfp_recorded
            ):
                sfp_events.append(
                    {
                        "level": level.level,
                        "side": level.side,
                        "time": ts.isoformat(),
                        "close": close_price,
                        "source": level.source,
                    }
                )
                extra_pools.append(
                    SmcLiquidityPool(
                        level=level.level,
                        liq_type=SmcLiquidityType.SFP,
                        strength=1.0,
                        n_touches=1,
                        first_time=ts,
                        last_time=ts,
                        role=resolve_role_for_bias(
                            structure.bias, SmcLiquidityType.SFP, side=level.side
                        ),
                        meta={
                            "source": "sfp",
                            "side": level.side,
                            "level_source": level.source,
                        },
                    )
                )
                sfp_recorded.add(level.key)
                continue
            if (
                level.side == "LOW"
                and low_price <= level.level - price_tol
                and close_price > level.level
                and level.key not in sfp_recorded
            ):
                sfp_events.append(
                    {
                        "level": level.level,
                        "side": level.side,
                        "time": ts.isoformat(),
                        "close": close_price,
                        "source": level.source,
                    }
                )
                extra_pools.append(
                    SmcLiquidityPool(
                        level=level.level,
                        liq_type=SmcLiquidityType.SFP,
                        strength=1.0,
                        n_touches=1,
                        first_time=ts,
                        last_time=ts,
                        role=resolve_role_for_bias(
                            structure.bias, SmcLiquidityType.SFP, side=level.side
                        ),
                        meta={
                            "source": "sfp",
                            "side": level.side,
                            "level_source": level.source,
                        },
                    )
                )
                sfp_recorded.add(level.key)
                continue

            if level.side == "HIGH":
                if (
                    upper_wick >= body * WICK_RATIO
                    and abs(level.level - high_price) <= price_tol
                ):
                    _collect_wick(wick_clusters, level, ts, upper_wick)
            else:
                if (
                    lower_wick >= body * WICK_RATIO
                    and abs(level.level - low_price) <= price_tol
                ):
                    _collect_wick(wick_clusters, level, ts, lower_wick)

    wick_meta: list[dict[str, Any]] = []
    for cluster in wick_clusters.values():
        wick_meta.append(
            {
                "level": cluster["level"],
                "side": cluster["side"],
                "count": cluster["count"],
                "max_wick": cluster["max_wick"],
                "source": cluster["source"],
                "first_ts": (
                    cluster["first_ts"].isoformat() if cluster["first_ts"] else None
                ),
                "last_ts": (
                    cluster["last_ts"].isoformat() if cluster["last_ts"] else None
                ),
            }
        )
        extra_pools.append(
            SmcLiquidityPool(
                level=cluster["level"],
                liq_type=SmcLiquidityType.WICK_CLUSTER,
                strength=float(cluster["max_wick"]),
                n_touches=cluster["count"],
                first_time=cluster["first_ts"],
                last_time=cluster["last_ts"],
                role=resolve_role_for_bias(
                    structure.bias, SmcLiquidityType.WICK_CLUSTER, side=cluster["side"]
                ),
                meta={
                    "source": "wick_cluster",
                    "side": cluster["side"],
                    "level_source": cluster["source"],
                    "count": cluster["count"],
                },
            )
        )

    return extra_pools, sfp_events, wick_meta


def _collect_levels(structure: SmcStructureState) -> list[_LevelInfo]:
    levels: dict[str, _LevelInfo] = {}
    for swing in structure.swings or []:
        level = float(swing.price)
        key = _level_key(level, swing.kind, "swing")
        if key not in levels:
            levels[key] = _LevelInfo(
                level=level, side=swing.kind, source="swing", key=key
            )
    active_range = structure.active_range
    if active_range is not None:
        high_key = _level_key(float(active_range.high), "HIGH", "range")
        low_key = _level_key(float(active_range.low), "LOW", "range")
        if high_key not in levels:
            levels[high_key] = _LevelInfo(
                level=float(active_range.high),
                side="HIGH",
                source="range",
                key=high_key,
            )
        if low_key not in levels:
            levels[low_key] = _LevelInfo(
                level=float(active_range.low), side="LOW", source="range", key=low_key
            )
    return list(levels.values())


def _collect_wick(
    clusters: dict[str, dict[str, Any]],
    level: _LevelInfo,
    ts: pd.Timestamp,
    wick_size: float,
) -> None:
    cluster = clusters.get(level.key)
    if cluster is None:
        cluster = {
            "level": level.level,
            "side": level.side,
            "source": level.source,
            "count": 0,
            "max_wick": 0.0,
            "first_ts": ts,
            "last_ts": ts,
        }
        clusters[level.key] = cluster
    cluster["count"] += 1
    cluster["max_wick"] = max(cluster["max_wick"], float(wick_size))
    cluster["last_ts"] = ts


def _prepare_price_frame(snapshot: SmcInput, max_bars: int) -> pd.DataFrame | None:
    df = snapshot.ohlc_by_tf.get(snapshot.tf_primary)
    if df is None or df.empty:
        return None
    df = df.copy()
    if max_bars > 0 and len(df) > max_bars:
        df = df.tail(max_bars)
    if "open_time" not in df.columns:
        return None
    open_time = pd.to_numeric(df["open_time"], errors="coerce")
    df["timestamp"] = pd.to_datetime(open_time, unit="ms", errors="coerce", utc=True)
    df = df.dropna(subset=["timestamp"])
    if df.empty:
        return None
    df = df.sort_values("open_time", kind="stable")
    required = {"open", "high", "low", "close"}
    if not required.issubset(df.columns):
        return None
    return df.reset_index(drop=True)


def _level_key(level: float, side: str, source: str) -> str:
    return f"{source}:{side}:{round(level, 4)}"
