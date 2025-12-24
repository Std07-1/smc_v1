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


def _coerce_ts(ts: Any) -> pd.Timestamp | None:
    """Нормалізує timestamp для внутрішнього використання.

    У трекері `_track_wick_clusters` ми можемо підхопити `first_ts/last_ts` із
    `prev_wick_clusters` у `snapshot.context`, де timestamp інколи приходить як
    ISO-рядок. Для `SmcLiquidityPool.first_time/last_time` SSOT — `pd.Timestamp`.
    """

    if ts is None:
        return None
    if isinstance(ts, pd.Timestamp):
        return ts
    try:
        out = pd.Timestamp(ts)
        return out
    except Exception:
        return None


def _estimate_life_bars(
    *, first_ts: pd.Timestamp | None, last_ts: pd.Timestamp | None, tf: str
) -> int:
    if first_ts is None or last_ts is None:
        return 0
    try:
        dt = abs((pd.Timestamp(last_ts) - pd.Timestamp(first_ts)).total_seconds())
    except Exception:
        return 0
    tf_s = 0
    try:
        tf_s = int(pd.Timedelta(tf).total_seconds())
    except Exception:
        # fallback: підтримуємо тільки типові TF
        tf_u = str(tf).lower().strip()
        tf_s = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}.get(tf_u, 0)
    if tf_s <= 0:
        return 0
    return int(dt // tf_s) + 1


def _float_or(x: Any, default: float) -> float:
    try:
        v = float(x)
        if v == v:
            return v
    except Exception:
        pass
    return float(default)


def _atr_last(structure: SmcStructureState) -> float | None:
    try:
        v = structure.meta.get("atr_last")
    except Exception:
        return None
    try:
        out = float(v)
        if out > 0 and out == out:
            return out
    except Exception:
        return None
    return None


def _track_wick_clusters(
    *,
    clusters: list[dict[str, Any]],
    prev_clusters: list[dict[str, Any]] | None,
    price_ref: float,
    atr_last: float | None,
    cfg: SmcCoreConfig,
) -> list[dict[str, Any]]:
    """Призначає стабільні cluster_id через matching по близькості рівня.

    Правило: greedy match (найближчий рівень) з порогами:
    - abs(level-cur_prev_level) <= max(price*tol_pct, max_abs_move_atr*ATR)
    - side має збігатися (HIGH/LOW)
    """

    if not clusters:
        return clusters
    if not bool(getattr(cfg, "liquidity_wick_cluster_track_enabled", True)):
        return clusters

    prev = prev_clusters or []
    prev_candidates: list[dict[str, Any]] = []
    for p in prev:
        if not isinstance(p, dict):
            continue
        if "cluster_id" not in p:
            continue
        prev_candidates.append(p)

    tol_pct = _float_or(
        getattr(cfg, "liquidity_wick_cluster_track_tol_pct", 0.0012), 0.0012
    )
    max_abs_move_atr = _float_or(
        getattr(cfg, "liquidity_wick_cluster_track_max_abs_move_atr", 0.60), 0.60
    )
    tol_price = max(abs(float(price_ref)) * tol_pct, 0.0)
    tol_atr = 0.0
    if atr_last is not None:
        tol_atr = max_abs_move_atr * float(atr_last)
    tol = max(tol_price, tol_atr)

    used_prev_ids: set[str] = set()

    def _prev_key(p: dict[str, Any]) -> str:
        return str(p.get("cluster_id") or "")

    # Стабільне присвоєння: сильніші/щільніші кластери матчимо першими.
    def _score(c: dict[str, Any]) -> float:
        return float(c.get("max_wick") or 0.0) * float(c.get("count") or 0.0)

    ordered = sorted(clusters, key=_score, reverse=True)
    for c in ordered:
        if not isinstance(c, dict):
            continue
        side = str(c.get("side") or "").upper()
        level = _float_or(c.get("level"), float("nan"))
        if level != level:
            continue

        best_prev: dict[str, Any] | None = None
        best_dist = None
        for p in prev_candidates:
            if str(p.get("side") or "").upper() != side:
                continue
            pid = _prev_key(p)
            if not pid or pid in used_prev_ids:
                continue
            plevel = _float_or(p.get("level"), float("nan"))
            if plevel != plevel:
                continue
            dist = abs(float(level) - float(plevel))
            if dist > tol:
                continue
            if best_dist is None or dist < best_dist:
                best_prev = p
                best_dist = dist

        if best_prev is not None:
            pid = _prev_key(best_prev)
            c["cluster_id"] = pid
            used_prev_ids.add(pid)
            # Продовжуємо життя кластера з попереднього.
            try:
                c["first_ts"] = best_prev.get("first_ts") or c.get("first_ts")
            except Exception:
                pass
        else:
            # Новий ID: детерміновано від (side, level квантований) — достатньо стабільно,
            # бо трекер далі підхопить.
            lvl_q = round(float(level), 2)
            c["cluster_id"] = f"wc:{side}:{lvl_q:.2f}"

    return clusters


def _to_tz_naive(ts: pd.Timestamp) -> pd.Timestamp:
    """Приводить Timestamp до tz-naive (узгоджено з іншими pool timestamps).

    У структурі/свінгах timestamp зазвичай tz-naive. Для SFP/wick детектора
    ми парсимо open_time у UTC, тому тут вирівнюємо, щоб не ловити
    'Cannot compare tz-naive and tz-aware timestamps' у downstream.
    """

    try:
        if isinstance(ts, pd.Timestamp) and ts.tzinfo is not None:
            return ts.tz_convert(None)
    except Exception:
        pass
    return ts


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
        ts = _to_tz_naive(pd.Timestamp(timestamps.iloc[idx]))
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
                        first_time=_to_tz_naive(ts),
                        last_time=_to_tz_naive(ts),
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
                        first_time=_to_tz_naive(ts),
                        last_time=_to_tz_naive(ts),
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

    # Top-K по стороні, щоб не захаращувати UI/QA.
    clusters_list = list(wick_clusters.values())
    topk_wicks = int(getattr(cfg, "liquidity_wick_cluster_topk_per_side", 0) or 0)
    if topk_wicks > 0 and clusters_list:

        def _wc_side(c: dict[str, Any]) -> str:
            try:
                return str(c.get("side") or "").upper()
            except Exception:
                return ""

        def _wc_score(c: dict[str, Any]) -> float:
            try:
                return float(c.get("max_wick") or 0.0) * float(c.get("count") or 0.0)
            except Exception:
                return 0.0

        out: list[dict[str, Any]] = []
        for side in ("HIGH", "LOW"):
            items = [c for c in clusters_list if _wc_side(c) == side]
            items = sorted(items, key=_wc_score, reverse=True)[:topk_wicks]
            out.extend(items)
        clusters_list = out

    # Після top-K: антишумні фільтри + трекер cluster_id.
    lookback_bars = int(len(df))
    atr_last = _atr_last(structure)
    price_ref = float(closes.iloc[-1]) if len(closes) else 0.0

    # 1) фільтри шуму (амплітуда/щільність/час життя)
    min_life = int(getattr(cfg, "liquidity_wick_cluster_min_life_bars", 0) or 0)
    min_density = _float_or(
        getattr(cfg, "liquidity_wick_cluster_min_density", 0.0), 0.0
    )
    min_amp_atr = _float_or(
        getattr(cfg, "liquidity_wick_cluster_min_amp_atr", 0.0), 0.0
    )

    filtered_clusters: list[dict[str, Any]] = []
    for c in clusters_list:
        try:
            count = int(c.get("count") or 0)
            max_wick = float(c.get("max_wick") or 0.0)
        except Exception:
            continue
        life_bars = _estimate_life_bars(
            first_ts=c.get("first_ts"), last_ts=c.get("last_ts"), tf=snapshot.tf_primary
        )
        density = 0.0 if lookback_bars <= 0 else float(count) / float(lookback_bars)
        amp_atr = None
        if atr_last is not None and atr_last > 0:
            amp_atr = float(max_wick) / float(atr_last)

        if min_life > 0 and life_bars < min_life:
            continue
        if min_density > 0 and density < min_density:
            continue
        if min_amp_atr > 0 and amp_atr is not None and amp_atr < min_amp_atr:
            continue

        filtered_clusters.append(c)

    clusters_list = filtered_clusters

    # 2) трекер: стабільний cluster_id між барами
    prev_clusters_any = (snapshot.context or {}).get("prev_wick_clusters")
    prev_clusters = prev_clusters_any if isinstance(prev_clusters_any, list) else None
    clusters_list = _track_wick_clusters(
        clusters=clusters_list,
        prev_clusters=prev_clusters,
        price_ref=price_ref,
        atr_last=atr_last,
        cfg=cfg,
    )

    # Зберігаємо для наступного бару (тільки локально в snapshot.context, без Redis/ENV).
    try:
        if snapshot.context is not None:
            snapshot.context["prev_wick_clusters"] = [dict(c) for c in clusters_list]
    except Exception:
        pass

    wick_meta: list[dict[str, Any]] = []
    for cluster in clusters_list:
        first_ts = _coerce_ts(cluster.get("first_ts"))
        last_ts = _coerce_ts(cluster.get("last_ts"))
        cluster["first_ts"] = first_ts
        cluster["last_ts"] = last_ts
        wick_meta.append(
            {
                "cluster_id": cluster.get("cluster_id"),
                "level": cluster["level"],
                "side": cluster["side"],
                "count": cluster["count"],
                "max_wick": cluster["max_wick"],
                "source": cluster["source"],
                "first_ts": (first_ts.isoformat() if first_ts is not None else None),
                "last_ts": (last_ts.isoformat() if last_ts is not None else None),
            }
        )
        extra_pools.append(
            SmcLiquidityPool(
                level=cluster["level"],
                liq_type=SmcLiquidityType.WICK_CLUSTER,
                strength=float(cluster["max_wick"]),
                n_touches=cluster["count"],
                first_time=first_ts,
                last_time=last_ts,
                role=resolve_role_for_bias(
                    structure.bias, SmcLiquidityType.WICK_CLUSTER, side=cluster["side"]
                ),
                meta={
                    "source": "wick_cluster",
                    "side": cluster["side"],
                    "level_source": cluster["source"],
                    "count": cluster["count"],
                    "cluster_id": cluster.get("cluster_id"),
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
            "first_ts": _to_tz_naive(ts),
            "last_ts": _to_tz_naive(ts),
        }
        clusters[level.key] = cluster
    cluster["count"] += 1
    cluster["max_wick"] = max(cluster["max_wick"], float(wick_size))
    cluster["last_ts"] = _to_tz_naive(ts)


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
