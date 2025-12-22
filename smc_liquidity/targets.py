"""Побудова liquidity targets (internal/external) для Stage3.

Ціль: на кожному TF мати 1–3 найближчі "магніти" ліквідності з роллю internal/external,
щоб Stage3 міг мислити "від пула до пула".

Важливо:
- Нічого не ламаємо в контрактах: targets кладемо в SmcLiquidityState.meta.
- Internal береться з уже знайдених магнітів на primary TF (зазвичай 5m).
- External беремо з HTF (1h/4h) як простий baseline: pivot swing highs/lows (+ fallback на extremes).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd

from smc_core.config import SmcCoreConfig
from smc_core.smc_types import SmcLiquidityMagnet

TargetRole = Literal["internal", "external"]
TargetSide = Literal["above", "below"]


@dataclass(frozen=True, slots=True)
class _Candidate:
    role: TargetRole
    tf: str
    side: TargetSide
    price: float
    kind: str
    strength: float
    why: list[str]
    distance_abs: float


def build_liquidity_targets(
    *,
    snapshot_ohlc_by_tf: dict[str, pd.DataFrame] | Any,
    tf_primary: str,
    magnets: list[SmcLiquidityMagnet],
    context: dict[str, Any] | None = None,
    cfg: SmcCoreConfig,
) -> list[dict[str, Any]]:
    """Будує список liquidity_targets.

    Повертає JSON-friendly список dict, який можна класти в meta.
    """

    ref_price = _extract_ref_price(snapshot_ohlc_by_tf, tf_primary)
    if ref_price is None:
        return []

    candidates: list[_Candidate] = []

    # 1) Internal: магніти primary TF.
    candidates.extend(
        _internal_candidates_from_magnets(
            magnets=magnets,
            tf=tf_primary,
            ref_price=ref_price,
            snapshot_ohlc_by_tf=snapshot_ohlc_by_tf,
        )
    )

    # 2) External: прості HTF pivots (baseline) + fallback на extremes.
    candidates.extend(
        _external_candidates_from_context(
            context=context or {},
            ref_price=ref_price,
        )
    )
    for tf in ("1h", "4h"):
        frame = _get_frame(snapshot_ohlc_by_tf, tf)
        if frame is None or frame.empty:
            continue
        candidates.extend(
            _external_candidates_from_htf_pivots(
                frame=frame,
                tf=tf,
                ref_price=ref_price,
                cfg=cfg,
            )
        )
        candidates.extend(
            _external_candidates_from_day_week_extremes(
                frame=frame,
                tf=tf,
                ref_price=ref_price,
            )
        )

    candidates = _dedup_candidates(candidates)

    # Вибираємо 1–3 найближчі per-role (з гарантією: якщо є вище+нижче — беремо по одному).
    selected: list[_Candidate] = []
    selected.extend(_select_nearest_per_role(candidates, role="internal"))
    selected.extend(_select_nearest_per_role(candidates, role="external"))

    # Стабільний порядок: internal потім external, і ближче → далі.
    selected.sort(key=lambda c: (0 if c.role == "internal" else 1, c.distance_abs))

    return [
        {
            "role": c.role,
            "tf": c.tf,
            "side": c.side,
            "price": round(float(c.price), 6),
            "type": c.kind,
            "strength": round(float(c.strength), 3),
            "reason": c.why,
        }
        for c in selected
    ]


def pick_nearest_target(
    targets: Iterable[dict[str, Any]],
    *,
    role: TargetRole,
    ref_price: float,
) -> dict[str, Any] | None:
    """Повертає найближчу ціль за роллю (internal/external)."""

    best: tuple[float, dict[str, Any]] | None = None
    for t in targets:
        if t.get("role") != role:
            continue
        price = t.get("price")
        if price is None:
            continue
        try:
            dist = abs(float(price) - float(ref_price))
        except (TypeError, ValueError):
            continue
        if best is None or dist < best[0]:
            best = (dist, t)
    return None if best is None else best[1]


def _get_frame(
    snapshot_ohlc_by_tf: dict[str, pd.DataFrame] | Any, tf: str
) -> pd.DataFrame | None:
    try:
        frame = snapshot_ohlc_by_tf.get(tf)
    except AttributeError:
        return None
    if frame is None:
        return None
    if not isinstance(frame, pd.DataFrame):
        return None
    return frame


def _extract_ref_price(
    snapshot_ohlc_by_tf: dict[str, pd.DataFrame] | Any, tf_primary: str
) -> float | None:
    frame = _get_frame(snapshot_ohlc_by_tf, tf_primary)
    if frame is None or frame.empty:
        return None
    if "close" not in frame.columns:
        return None
    try:
        return float(frame["close"].iloc[-1])
    except (TypeError, ValueError):
        return None


def _internal_candidates_from_magnets(
    *,
    magnets: list[SmcLiquidityMagnet],
    tf: str,
    ref_price: float,
    snapshot_ohlc_by_tf: dict[str, pd.DataFrame] | Any,
) -> list[_Candidate]:
    frame = _get_frame(snapshot_ohlc_by_tf, tf)
    atr = _atr_last(frame, period=14) if frame is not None else None
    scale = float(atr) if atr and atr > 0 else max(abs(ref_price) * 0.01, 1e-9)

    out: list[_Candidate] = []
    for m in magnets or []:
        price = float(m.center)
        if not math.isfinite(price):
            continue
        side: TargetSide = "above" if price >= ref_price else "below"
        touches = 0
        strength_sum = 0.0
        last_time: pd.Timestamp | None = None
        for p in m.pools or []:
            touches += int(getattr(p, "n_touches", 0) or 0)
            try:
                strength_sum += float(getattr(p, "strength", 0.0) or 0.0)
            except (TypeError, ValueError):
                pass
            pt = getattr(p, "last_time", None)
            if isinstance(pt, pd.Timestamp):
                try:
                    if pt.tzinfo is None:
                        pt = pt.tz_localize("UTC")
                    else:
                        pt = pt.tz_convert("UTC")
                except Exception:
                    pass
                if last_time is None or pt > last_time:
                    last_time = pt

        dist_abs = abs(price - ref_price)
        proximity = max(0.0, 1.0 - (dist_abs / (3.0 * scale)))
        freshness = 0.0
        if last_time is not None and frame is not None and "timestamp" in frame.columns:
            try:
                ts = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
                if not ts.isna().all():
                    # Скільки барів пройшло з моменту останнього торкання.
                    age = int((ts <= last_time).sum())
                    # Невеликий бонус за "свіжість": останні ~20 барів.
                    freshness = 1.0 if age >= max(0, len(frame) - 20) else 0.5
            except Exception:
                freshness = 0.0

        touches_norm = min(1.0, touches / 6.0) if touches > 0 else 0.0
        strength = 100.0 * (0.55 * proximity + 0.25 * freshness + 0.20 * touches_norm)

        why = [
            "source:magnet",
            f"liq_type:{getattr(m.liq_type, 'name', str(m.liq_type))}",
            f"magnet_role:{m.role}",
            f"pools:{len(m.pools or [])}",
            f"touches:{touches}",
        ]
        out.append(
            _Candidate(
                role="internal",
                tf=tf,
                side=side,
                price=price,
                kind=getattr(m.liq_type, "name", str(m.liq_type)),
                strength=float(strength),
                why=why,
                distance_abs=float(dist_abs),
            )
        )

    return out


def _external_candidates_from_htf_pivots(
    *,
    frame: pd.DataFrame,
    tf: str,
    ref_price: float,
    cfg: SmcCoreConfig,
) -> list[_Candidate]:
    _ = cfg  # майбутня адаптація порогів/вікна

    # Мінімальні інваріанти
    for col in ("high", "low", "close"):
        if col not in frame.columns:
            return []

    lookback = min(200, int(len(frame)))
    if lookback < 10:
        return []

    tail = frame.iloc[-lookback:].copy()
    atr = _atr_last(tail, period=14)
    tol = float(atr) * 0.6 if atr and atr > 0 else max(abs(ref_price) * 0.002, 1e-9)

    piv_h, piv_l = _pivots(tail, left=2, right=2)

    # Кластеризуємо рівні в межах tol.
    clusters_above = _cluster_levels(
        [p.price for p in piv_h if p.price > ref_price], tol
    )
    clusters_below = _cluster_levels(
        [p.price for p in piv_l if p.price < ref_price], tol
    )

    out: list[_Candidate] = []

    # Найближчий вище.
    above_price = (
        min(clusters_above, key=lambda x: x.center).center if clusters_above else None
    )
    if above_price is None:
        try:
            above_price = float(tail["high"].max())
        except Exception:
            above_price = None
    if above_price is not None and math.isfinite(float(above_price)):
        out.append(
            _Candidate(
                role="external",
                tf=tf,
                side="above",
                price=float(above_price),
                kind="HTF_SWING_HIGH",
                strength=_external_strength(
                    ref_price, float(above_price), clusters_above, tol
                ),
                why=[
                    "source:htf_pivots",
                    f"tf:{tf}",
                    "pivot_window:2",
                    f"lookback:{lookback}",
                    f"tol:{round(float(tol), 6)}",
                ],
                distance_abs=float(abs(float(above_price) - ref_price)),
            )
        )

    # Найближчий нижче.
    below_price = (
        max(clusters_below, key=lambda x: x.center).center if clusters_below else None
    )
    if below_price is None:
        try:
            below_price = float(tail["low"].min())
        except Exception:
            below_price = None
    if below_price is not None and math.isfinite(float(below_price)):
        out.append(
            _Candidate(
                role="external",
                tf=tf,
                side="below",
                price=float(below_price),
                kind="HTF_SWING_LOW",
                strength=_external_strength(
                    ref_price, float(below_price), clusters_below, tol
                ),
                why=[
                    "source:htf_pivots",
                    f"tf:{tf}",
                    "pivot_window:2",
                    f"lookback:{lookback}",
                    f"tol:{round(float(tol), 6)}",
                ],
                distance_abs=float(abs(float(below_price) - ref_price)),
            )
        )

    return out


def _external_candidates_from_context(
    *,
    context: dict[str, Any],
    ref_price: float,
) -> list[_Candidate]:
    out: list[_Candidate] = []
    if not context:
        return out

    # 0) Власні сесійні екстремуми (Asia/London/NY): контекст від input_adapter.
    sessions = context.get("smc_sessions")
    if isinstance(sessions, dict):
        for tag, payload in sessions.items():
            if not isinstance(payload, dict):
                continue
            for kind, key in (("SESSION_HIGH", "high"), ("SESSION_LOW", "low")):
                raw = payload.get(key)
                if raw is None:
                    continue
                try:
                    level = float(raw)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(level):
                    continue
                dist_abs = abs(level - ref_price)
                proximity = max(
                    0.0, 1.0 - (dist_abs / max(abs(ref_price) * 0.01, 1e-9))
                )
                strength = float(75.0 + 25.0 * proximity)
                out.append(
                    _Candidate(
                        role="external",
                        tf="1h",
                        side="above" if level >= ref_price else "below",
                        price=level,
                        kind=kind,
                        strength=strength,
                        why=[
                            "source:smc_sessions",
                            f"session_tag:{str(tag).upper()}",
                        ],
                        distance_abs=float(dist_abs),
                    )
                )

    # 1) Інші контекстні рівні (legacy/HTF): week extremes тощо.

    mapping: list[tuple[tuple[str, ...], str, TargetSide, str]] = [
        (("week_high", "pwh"), "4h", "above", "WEEK_HIGH"),
        (("week_low", "pwl"), "4h", "below", "WEEK_LOW"),
    ]

    for keys, tf, side, kind in mapping:
        raw = None
        picked_key: str | None = None
        for k in keys:
            candidate = context.get(k)
            if candidate is None:
                continue
            raw = candidate
            picked_key = k
            break
        if raw is None:
            continue
        if raw is None:
            continue
        try:
            level = float(raw)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(level):
            continue

        dist_abs = abs(level - ref_price)
        proximity = max(0.0, 1.0 - (dist_abs / max(abs(ref_price) * 0.01, 1e-9)))
        strength = float(70.0 + 30.0 * proximity)
        out.append(
            _Candidate(
                role="external",
                tf=tf,
                side=side,
                price=level,
                kind=kind,
                strength=strength,
                why=[
                    "source:context",
                    f"key:{picked_key or 'unknown'}",
                ],
                distance_abs=float(dist_abs),
            )
        )

    return out


def _external_candidates_from_day_week_extremes(
    *,
    frame: pd.DataFrame,
    tf: str,
    ref_price: float,
) -> list[_Candidate]:
    """Fallback на day/week extremes з HTF OHLCV.

    Мінімальна інтерпретація:
    - DAY: попередній календарний день (за timestamp), якщо вдається.
    - WEEK: останні 7 днів (rolling), якщо вдається.
    """

    if (
        "timestamp" not in frame.columns
        or "high" not in frame.columns
        or "low" not in frame.columns
    ):
        return []

    ts = pd.to_datetime(frame["timestamp"], errors="coerce")
    if ts.isna().all():
        return []
    last_ts = ts.dropna().iloc[-1]
    try:
        last_day = pd.Timestamp(last_ts).normalize()
    except Exception:
        return []

    h = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    if h.isna().all() or low.isna().all():
        return []

    out: list[_Candidate] = []

    # Previous day extremes
    prev_day = last_day - pd.Timedelta(days=1)
    mask_prev = (ts >= prev_day) & (ts < last_day)
    if bool(mask_prev.any()):
        try:
            pdh = float(h[mask_prev].max())
            pdl = float(low[mask_prev].min())
        except Exception:
            pdh = None  # type: ignore[assignment]
            pdl = None  # type: ignore[assignment]
        if pdh is not None and math.isfinite(float(pdh)):
            out.append(
                _Candidate(
                    role="external",
                    tf=tf,
                    side="above" if pdh >= ref_price else "below",
                    price=float(pdh),
                    kind="DAY_HIGH",
                    strength=60.0,
                    why=["source:htf_day_extreme", f"tf:{tf}"],
                    distance_abs=float(abs(float(pdh) - ref_price)),
                )
            )
        if pdl is not None and math.isfinite(float(pdl)):
            out.append(
                _Candidate(
                    role="external",
                    tf=tf,
                    side="below" if pdl <= ref_price else "above",
                    price=float(pdl),
                    kind="DAY_LOW",
                    strength=60.0,
                    why=["source:htf_day_extreme", f"tf:{tf}"],
                    distance_abs=float(abs(float(pdl) - ref_price)),
                )
            )

    # Rolling week extremes (7d)
    start_week = last_day - pd.Timedelta(days=7)
    mask_week = ts >= start_week
    if bool(mask_week.any()):
        try:
            wh = float(h[mask_week].max())
            wl = float(low[mask_week].min())
        except Exception:
            wh = None  # type: ignore[assignment]
            wl = None  # type: ignore[assignment]
        if wh is not None and math.isfinite(float(wh)):
            out.append(
                _Candidate(
                    role="external",
                    tf=tf,
                    side="above" if wh >= ref_price else "below",
                    price=float(wh),
                    kind="WEEK_HIGH_ROLLING",
                    strength=55.0,
                    why=["source:htf_week_extreme", f"tf:{tf}"],
                    distance_abs=float(abs(float(wh) - ref_price)),
                )
            )
        if wl is not None and math.isfinite(float(wl)):
            out.append(
                _Candidate(
                    role="external",
                    tf=tf,
                    side="below" if wl <= ref_price else "above",
                    price=float(wl),
                    kind="WEEK_LOW_ROLLING",
                    strength=55.0,
                    why=["source:htf_week_extreme", f"tf:{tf}"],
                    distance_abs=float(abs(float(wl) - ref_price)),
                )
            )

    return out


def _dedup_candidates(candidates: list[_Candidate]) -> list[_Candidate]:
    seen: set[tuple[str, str, str, float, str]] = set()
    out: list[_Candidate] = []
    for c in candidates:
        key = (c.role, c.tf, c.side, round(float(c.price), 5), c.kind)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


@dataclass(frozen=True, slots=True)
class _Pivot:
    idx: int
    price: float


def _pivots(
    df: pd.DataFrame, *, left: int, right: int
) -> tuple[list[_Pivot], list[_Pivot]]:
    highs: list[_Pivot] = []
    lows: list[_Pivot] = []

    h = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    if h.isna().all() or low.isna().all():
        return highs, lows

    n = int(len(df))
    for i in range(left, n - right):
        window_h = h.iloc[i - left : i + right + 1]
        window_l = low.iloc[i - left : i + right + 1]
        hi = float(h.iloc[i])
        lo = float(low.iloc[i])
        if math.isfinite(hi) and hi >= float(window_h.max()):
            highs.append(_Pivot(idx=i, price=hi))
        if math.isfinite(lo) and lo <= float(window_l.min()):
            lows.append(_Pivot(idx=i, price=lo))

    # Беремо лише останні кілька pivot-ів, щоб не "зашуміти".
    return highs[-20:], lows[-20:]


@dataclass(frozen=True, slots=True)
class _Cluster:
    center: float
    n: int


def _cluster_levels(levels: list[float], tol: float) -> list[_Cluster]:
    if not levels:
        return []
    cleaned = [float(x) for x in levels if x is not None and math.isfinite(float(x))]
    if not cleaned:
        return []
    cleaned.sort()

    clusters: list[list[float]] = [[cleaned[0]]]
    for x in cleaned[1:]:
        if abs(x - clusters[-1][-1]) <= tol:
            clusters[-1].append(x)
        else:
            clusters.append([x])

    out: list[_Cluster] = []
    for c in clusters:
        center = float(sum(c) / len(c))
        out.append(_Cluster(center=center, n=len(c)))
    return out


def _external_strength(
    ref_price: float, level: float, clusters: list[_Cluster], tol: float
) -> float:
    dist_abs = abs(level - ref_price)
    proximity = max(0.0, 1.0 - (dist_abs / max(3.0 * tol, 1e-9)))
    touches = 1
    for c in clusters:
        if abs(c.center - level) <= tol:
            touches = max(touches, int(c.n))
            break
    touches_norm = min(1.0, touches / 4.0)
    return float(100.0 * (0.70 * proximity + 0.30 * touches_norm))


def _atr_last(df: pd.DataFrame | None, *, period: int) -> float | None:
    if df is None or df.empty:
        return None
    for col in ("high", "low", "close"):
        if col not in df.columns:
            return None
    h = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    c = pd.to_numeric(df["close"], errors="coerce")
    if h.isna().all() or low.isna().all() or c.isna().all():
        return None

    prev_c = c.shift(1)
    tr1 = (h - low).abs()
    tr2 = (h - prev_c).abs()
    tr3 = (low - prev_c).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period, min_periods=max(2, period // 2)).mean()
    last = atr.iloc[-1]
    try:
        v = float(last)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def _select_nearest_per_role(
    candidates: list[_Candidate], *, role: TargetRole
) -> list[_Candidate]:
    role_cands = [c for c in candidates if c.role == role]
    if not role_cands:
        return []

    role_cands.sort(key=lambda c: (c.distance_abs, -c.strength))
    # 1) Беремо найbliжчу.
    picked: list[_Candidate] = [role_cands[0]]

    # 2) Якщо є інший бік — додамо, щоб завжди можна було сказати "є зовнішня/внутрішня".
    sides = {role_cands[0].side}
    for c in role_cands[1:]:
        if c.side not in sides:
            picked.append(c)
            sides.add(c.side)
            break

    # 3) Добираємо до 3.
    for c in role_cands[1:]:
        if c in picked:
            continue
        picked.append(c)
        if len(picked) >= 3:
            break

    return picked
