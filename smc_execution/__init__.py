"""smc_execution
~~~~~~~~~~~~~~

Stage5: Execution (1m) — micro-події підтвердження лише біля POI/targets.

Принцип:
- 1m не є «мозком» — він лише «тригер», який підсвічує події там, де вже
  є сенс чекати реакцію (POI або target).
- Якщо ціна не in_play, подій не має бути (антишум).

Вихід:
- SmcExecutionState(execution_events[], meta).

Події (мінімальний набір):
- SWEEP: прокол рівня + повернення/закриття назад.
- MICRO_BOS / MICRO_CHOCH: мікро-брейк локального pivot (вікно), лише коли in_play.
- RETEST_OK: підтвердження після break&hold або sweep&reject (простий шаблон на 2 бара).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal, cast

import pandas as pd

from core.serialization import safe_float
from smc_core.config import SmcCoreConfig
from smc_core.smc_types import (
    SmcExecutionEvent,
    SmcExecutionState,
    SmcInput,
    SmcLiquidityState,
    SmcStructureState,
    SmcZone,
    SmcZonesState,
)

_ExecRef = Literal["POI", "TARGET", "UNKNOWN"]


def _context_ref_fields(
    in_play_ref: dict[str, Any] | None,
) -> tuple[_ExecRef, str | None]:
    ref = None
    if isinstance(in_play_ref, dict):
        ref = in_play_ref.get("ref")
    ref_u = str(ref or "UNKNOWN").upper()
    if ref_u not in {"POI", "TARGET"}:
        ref_u = "UNKNOWN"
    ref_lit = cast(_ExecRef, ref_u)
    poi_zone_id = None
    if ref_lit == "POI" and isinstance(in_play_ref, dict):
        zid = in_play_ref.get("poi_zone_id")
        poi_zone_id = str(zid) if zid is not None else None
    return ref_lit, poi_zone_id


def compute_execution_state(
    *,
    snapshot: SmcInput,
    structure: SmcStructureState | None,
    liquidity: SmcLiquidityState | None,
    zones: SmcZonesState | None,
    cfg: SmcCoreConfig,
) -> SmcExecutionState:
    """Обчислює Stage5 execution (1m) події.

    Важливо:
    - soft-fail: якщо 1m відсутній або даних мало — повертаємо пустий стан;
    - події генеруємо лише якщо in_play=true.
    """

    if not cfg.exec_enabled:
        return SmcExecutionState(execution_events=[], meta={"exec_enabled": False})

    frame = snapshot.ohlc_by_tf.get(cfg.exec_tf)
    if frame is None or frame.empty:
        return SmcExecutionState(
            execution_events=[],
            meta={
                "exec_enabled": True,
                "reason": "no_exec_frame",
                "exec_tf": cfg.exec_tf,
            },
        )

    # Потрібно мінімум 3 свічки: шаблон sweep/retest може дивитися на [-2], [-1].
    if len(frame) < 3:
        return SmcExecutionState(
            execution_events=[],
            meta={
                "exec_enabled": True,
                "reason": "insufficient_exec_bars",
                "exec_bars": int(len(frame)),
                "exec_tf": cfg.exec_tf,
            },
        )

    last_close = _safe_last_float(frame, "close")
    if last_close is None:
        return SmcExecutionState(
            execution_events=[],
            meta={
                "exec_enabled": True,
                "reason": "no_last_close",
                "exec_tf": cfg.exec_tf,
            },
        )

    atr = _extract_atr(structure=structure, exec_frame=frame)

    poi_zones = _select_poi_zones(zones)
    targets = _collect_targets(
        structure=structure, liquidity=liquidity, snapshot=snapshot
    )

    radius = None
    if atr is not None and atr > 0:
        radius = float(cfg.exec_in_play_radius_atr) * float(atr)

    in_play_now, in_play_ref = _is_in_play(
        price=float(last_close),
        poi_zones=poi_zones,
        targets=targets,
        radius=radius,
    )

    context_ref, poi_zone_id = _context_ref_fields(in_play_ref)

    in_play = in_play_now
    if cfg.exec_in_play_hold_bars and cfg.exec_in_play_hold_bars > 0:
        hold = int(cfg.exec_in_play_hold_bars)
        in_play = _in_play_holds(
            frame=frame,
            hold_bars=hold,
            poi_zones=poi_zones,
            targets=targets,
            radius=radius,
        )

    meta: dict[str, Any] = {
        "exec_enabled": True,
        "exec_tf": cfg.exec_tf,
        "atr_ref": float(atr) if atr is not None else None,
        "in_play": bool(in_play),
        "in_play_now": bool(in_play_now),
        "in_play_ref": in_play_ref,
        "radius": float(radius) if radius is not None else None,
        "poi_count": int(len(poi_zones)),
        "targets_count": int(len(targets)),
        "hold_bars": int(cfg.exec_in_play_hold_bars),
        "impulse_atr_mul": float(cfg.exec_impulse_atr_mul),
        "micro_pivot_bars": int(cfg.exec_micro_pivot_bars),
    }

    if not in_play:
        return SmcExecutionState(execution_events=[], meta=meta)

    # Антишум: беремо лише найближчий target (якщо він в радіусі), щоб не плодити SWEEP.
    nearest_target = _nearest_level(price=float(last_close), levels=targets)
    sweep_levels: list[float] = []
    if nearest_target is not None and radius is not None:
        if abs(float(nearest_target) - float(last_close)) <= float(radius):
            sweep_levels = [float(nearest_target)]

    events: list[SmcExecutionEvent] = []

    # 1) SWEEP на останньому барі (або його close повернувся).
    if sweep_levels:
        events.extend(
            _detect_sweeps_last_bar(
                frame=frame,
                levels=sweep_levels,
                atr=atr,
                cfg=cfg,
                context_ref=context_ref,
            )
        )

    # 2) micro BOS/CHOCH на останньому барі.
    micro_evt = _detect_micro_break_last_bar(
        frame=frame,
        bias=(structure.bias if structure is not None else "NEUTRAL"),
        atr=atr,
        cfg=cfg,
        ref=context_ref,
        poi_zone_id=poi_zone_id,
    )
    if micro_evt is not None:
        events.append(micro_evt)

    # 3) RETEST_OK: простий 2-bar патерн після sweep або micro-break.
    retest = _detect_retest_ok(
        frame=frame,
        bias=(structure.bias if structure is not None else "NEUTRAL"),
        atr=atr,
        cfg=cfg,
        sweep_levels=sweep_levels,
        ref=context_ref,
        poi_zone_id=poi_zone_id,
    )
    if retest is not None:
        events.append(retest)

    # Жорсткий cap.
    if cfg.exec_max_events and cfg.exec_max_events > 0:
        events = events[-int(cfg.exec_max_events) :]

    return SmcExecutionState(execution_events=events, meta=meta)


# ── In-play helpers ─────────────────────────────────────────────────────────


def _select_poi_zones(zones: SmcZonesState | None) -> list[SmcZone]:
    if zones is None:
        return []
    if zones.poi_zones:
        return list(zones.poi_zones)
    if zones.active_zones:
        return list(zones.active_zones)
    return []


def _collect_targets(
    *,
    structure: SmcStructureState | None,
    liquidity: SmcLiquidityState | None,
    snapshot: SmcInput,
) -> list[float]:
    levels: list[float] = []

    # Важливо (антишум): НЕ використовуємо `liquidity_targets` як гейт для in_play.
    # Це за дизайном «найближчі» рівні, і на реальних даних вони часто роблять
    # in_play майже завжди True, що ламає призначення Stage5 як фільтра.
    _ = liquidity  # зарезервовано: можна повернутись до «значимих» liquidity-levels через окремий флаг.

    _ = structure  # зарезервовано: active_range може бути корисним для візуалізації, але як гейт він занадто "липкий".

    # Context: значимі HTF екстремуми (якщо є).
    ctx = snapshot.context or {}
    for k in ("pdh", "pdl", "pwh", "pwl"):
        v = safe_float(ctx.get(k))
        if v is not None:
            levels.append(float(v))

    # Сесійні highs/lows: беремо лише завершені сесії (не активну),
    # бо active session high/low часто близько до ціни й робить in_play майже завжди True.
    smc_sessions = ctx.get("smc_sessions")
    if isinstance(smc_sessions, dict):
        for payload in smc_sessions.values():
            if not isinstance(payload, dict):
                continue
            if payload.get("is_active") is True:
                continue
            for k in ("high", "low"):
                v = safe_float(payload.get(k))
                if v is not None:
                    levels.append(float(v))

    # Дедуп + стабільний порядок.
    seen: set[float] = set()
    out: list[float] = []
    for v in levels:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _is_in_play(
    *,
    price: float,
    poi_zones: Iterable[SmcZone],
    targets: Iterable[float],
    radius: float | None,
) -> tuple[bool, dict[str, Any]]:
    """Повертає (in_play, ref).

    ref містить коротке пояснення: чи спрацювало POI, чи TARGET, і який саме.
    """

    # 1) В POI.
    #
    # Важливо (антишум): "всередині POI" у сенсі Stage5 означає
    # "в зоні входу" — біля межі POI, а не будь-де в прямокутнику.
    # Інакше широкі/часті POI роблять in_play майже завжди True.
    for z in poi_zones:
        lo = safe_float(getattr(z, "price_min", None))
        hi = safe_float(getattr(z, "price_max", None))
        if lo is None or hi is None:
            continue
        lo2, hi2 = float(lo), float(hi)
        if not (lo2 <= float(price) <= hi2):
            continue

        width = max(1e-9, hi2 - lo2)
        band = 0.20 * width
        if radius is not None and radius > 0:
            band = min(float(band), float(radius))
        # Близько до нижньої/верхньої межі зони.
        if min(float(price) - lo2, hi2 - float(price)) <= float(band):
            return True, {
                "ref": "POI",
                "poi_zone_id": getattr(z, "zone_id", None),
                "poi_min": float(lo),
                "poi_max": float(hi),
            }

    # 2) Біля target.
    if radius is not None and radius > 0:
        nearest = _nearest_level(price=price, levels=targets)
        if nearest is not None and abs(float(nearest) - float(price)) <= float(radius):
            return True, {"ref": "TARGET", "level": float(nearest)}

    return False, {"ref": "NONE"}


def _in_play_holds(
    *,
    frame: pd.DataFrame,
    hold_bars: int,
    poi_zones: list[SmcZone],
    targets: list[float],
    radius: float | None,
) -> bool:
    closes = frame["close"].astype(float)
    if hold_bars <= 0:
        return False
    if len(closes) < hold_bars:
        return False
    tail = closes.iloc[-hold_bars:]
    for v in tail:
        ok, _ref = _is_in_play(
            price=float(v),
            poi_zones=poi_zones,
            targets=targets,
            radius=radius,
        )
        if not ok:
            return False
    return True


def _extract_atr(
    *, structure: SmcStructureState | None, exec_frame: pd.DataFrame
) -> float | None:
    if structure is not None:
        atr = safe_float(
            (structure.meta or {}).get("atr_last")
            or (structure.meta or {}).get("atr_median")
        )
        if atr is not None and atr > 0:
            return float(atr)

    # Fallback: медіана діапазону (high-low) на exec TF (≈ сурогат ATR).
    try:
        rng = (exec_frame["high"].astype(float) - exec_frame["low"].astype(float)).abs()
        v = safe_float(rng.tail(30).median())
        if v is not None and v > 0:
            return float(v)
    except Exception:
        return None
    return None


def _nearest_level(*, price: float, levels: Iterable[float]) -> float | None:
    best: float | None = None
    best_dist: float | None = None
    for lvl in levels:
        try:
            d = abs(float(lvl) - float(price))
        except Exception:
            continue
        if best is None or best_dist is None or d < best_dist:
            best = float(lvl)
            best_dist = float(d)
    return best


def _safe_last_float(frame: pd.DataFrame, col: str) -> float | None:
    try:
        return safe_float(frame[col].iloc[-1])
    except Exception:
        return None


# ── Event detectors (мінімальні, антишумні) ────────────────────────────────


def _detect_sweeps_last_bar(
    *,
    frame: pd.DataFrame,
    levels: list[float],
    atr: float | None,
    cfg: SmcCoreConfig,
    context_ref: _ExecRef,
) -> list[SmcExecutionEvent]:
    """SWEEP: прокол рівня wick'ом + close повернувся за рівень."""

    last = frame.iloc[-1]
    ts = _row_ts(frame, -1)
    if ts is None:
        return []

    o = safe_float(last.get("open"))
    h = safe_float(last.get("high"))
    low = safe_float(last.get("low"))
    c = safe_float(last.get("close"))
    if o is None or h is None or low is None or c is None:
        return []

    # Опційний вола-фільтр (C): імпульс тіла.
    if (
        cfg.exec_impulse_atr_mul
        and cfg.exec_impulse_atr_mul > 0
        and atr is not None
        and atr > 0
    ):
        body = abs(float(c) - float(o))
        if body < float(cfg.exec_impulse_atr_mul) * float(atr):
            return []

    out: list[SmcExecutionEvent] = []
    for lvl in levels:
        # Sweep highs → потенційний SHORT.
        if float(h) > float(lvl) and float(c) < float(lvl):
            out.append(
                SmcExecutionEvent(
                    event_type="SWEEP",
                    direction="SHORT",
                    time=ts,
                    price=float(c),
                    level=float(lvl),
                    ref="TARGET",
                    meta={"sweep_side": "HIGH", "context_ref": context_ref},
                )
            )
        # Sweep lows → потенційний LONG.
        if float(low) < float(lvl) and float(c) > float(lvl):
            out.append(
                SmcExecutionEvent(
                    event_type="SWEEP",
                    direction="LONG",
                    time=ts,
                    price=float(c),
                    level=float(lvl),
                    ref="TARGET",
                    meta={"sweep_side": "LOW", "context_ref": context_ref},
                )
            )

    return out


def _detect_micro_break_last_bar(
    *,
    frame: pd.DataFrame,
    bias: str,
    atr: float | None,
    cfg: SmcCoreConfig,
    ref: _ExecRef,
    poi_zone_id: str | None,
) -> SmcExecutionEvent | None:
    """micro BOS/CHOCH: close пробиває локальний pivot (вікно).

    Для антишуму:
    - використовуємо close, а не wick;
    - за замовчуванням без додаткових порогів, але можна ввімкнути імпульс k*ATR.
    """

    window = int(cfg.exec_micro_pivot_bars)
    if window < 3:
        window = 3
    if len(frame) < window + 1:
        return None

    tail = frame.iloc[-(window + 1) :]
    prev = tail.iloc[:-1]
    last = tail.iloc[-1]

    prev_high = safe_float(prev["high"].astype(float).max())
    prev_low = safe_float(prev["low"].astype(float).min())
    c = safe_float(last.get("close"))
    o = safe_float(last.get("open"))
    if prev_high is None or prev_low is None or c is None:
        return None

    # Волатильність (C) — імпульс тіла.
    if (
        cfg.exec_impulse_atr_mul
        and cfg.exec_impulse_atr_mul > 0
        and atr is not None
        and atr > 0
        and o is not None
    ):
        if abs(float(c) - float(o)) < float(cfg.exec_impulse_atr_mul) * float(atr):
            return None

    ts = _row_ts(frame, -1)
    if ts is None:
        return None

    bias_u = str(bias or "NEUTRAL").upper()

    if float(c) > float(prev_high):
        # Break up.
        ev = (
            "MICRO_BOS"
            if bias_u == "LONG"
            else "MICRO_CHOCH" if bias_u == "SHORT" else "MICRO_BOS"
        )
        return SmcExecutionEvent(
            event_type=ev,
            direction="LONG",
            time=ts,
            price=float(c),
            level=float(prev_high),
            ref=ref,
            poi_zone_id=poi_zone_id,
            meta={"pivot_window": window, "pivot_kind": "HIGH"},
        )

    if float(c) < float(prev_low):
        ev = (
            "MICRO_BOS"
            if bias_u == "SHORT"
            else "MICRO_CHOCH" if bias_u == "LONG" else "MICRO_BOS"
        )
        return SmcExecutionEvent(
            event_type=ev,
            direction="SHORT",
            time=ts,
            price=float(c),
            level=float(prev_low),
            ref=ref,
            poi_zone_id=poi_zone_id,
            meta={"pivot_window": window, "pivot_kind": "LOW"},
        )

    return None


def _detect_retest_ok(
    *,
    frame: pd.DataFrame,
    bias: str,
    atr: float | None,
    cfg: SmcCoreConfig,
    sweep_levels: list[float] | None = None,
    ref: _ExecRef,
    poi_zone_id: str | None,
) -> SmcExecutionEvent | None:
    """RETEST_OK: простий 2-bar патерн.

    - Break&hold: bar[-2] пробив pivot, bar[-1] торкнувся level і закрився в напрямку.
    - Sweep&reject: bar[-2] зробив sweep, bar[-1] підтвердив утримання над/під level.

    Примітка: це мінімальна версія без зберігання стану між циклами.
    """

    if len(frame) < 4:
        return None

    b2 = frame.iloc[-2]
    b1 = frame.iloc[-1]
    ts = _row_ts(frame, -1)
    if ts is None:
        return None

    o2 = safe_float(b2.get("open"))
    h2 = safe_float(b2.get("high"))
    l2 = safe_float(b2.get("low"))
    c2 = safe_float(b2.get("close"))
    o1 = safe_float(b1.get("open"))
    h1 = safe_float(b1.get("high"))
    l1 = safe_float(b1.get("low"))
    c1 = safe_float(b1.get("close"))
    if any(v is None for v in (o2, h2, l2, c2, o1, h1, l1, c1)):
        return None

    assert o2 is not None
    assert h2 is not None
    assert l2 is not None
    assert c2 is not None
    assert o1 is not None
    assert h1 is not None
    assert l1 is not None
    assert c1 is not None

    # Волатильність (C) — застосовуємо тільки до кандидата-брейку (bar[-2]).
    if (
        cfg.exec_impulse_atr_mul
        and cfg.exec_impulse_atr_mul > 0
        and atr is not None
        and atr > 0
    ):
        if abs(float(c2) - float(o2)) < float(cfg.exec_impulse_atr_mul) * float(atr):
            return None

    bias_u = str(bias or "NEUTRAL").upper()

    # Sweep&reject на bar[-2] (sweep) + підтвердження на bar[-1] (retest+hold).
    # Важливо: не вимагає pivot-історії, бо рівень заданий як target.
    if sweep_levels:
        for level in sweep_levels:
            lvl = float(level)
            touched = float(l1) <= lvl <= float(h1)

            # Sweep high: bar[-2] прокол lvl і закрився нижче; bar[-1] retest+hold нижче.
            if float(h2) > lvl and float(c2) < lvl:
                if touched and float(c1) < lvl:
                    return SmcExecutionEvent(
                        event_type="RETEST_OK",
                        direction="SHORT",
                        time=ts,
                        price=float(c1),
                        level=lvl,
                        ref="TARGET",
                        meta={
                            "source": "sweep_reject",
                            "bias": bias_u,
                            "context_ref": ref,
                        },
                    )

            # Sweep low: bar[-2] прокол lvl і закрився вище; bar[-1] retest+hold вище.
            if float(l2) < lvl and float(c2) > lvl:
                if touched and float(c1) > lvl:
                    return SmcExecutionEvent(
                        event_type="RETEST_OK",
                        direction="LONG",
                        time=ts,
                        price=float(c1),
                        level=lvl,
                        ref="TARGET",
                        meta={
                            "source": "sweep_reject",
                            "bias": bias_u,
                            "context_ref": ref,
                        },
                    )

    # Pivot з window (візьмемо prev window перед bar[-2]).
    window = int(cfg.exec_micro_pivot_bars)
    if window < 3:
        window = 3
    if len(frame) < window + 2:
        return None

    prev = frame.iloc[-(window + 2) : -2]
    prev_high = safe_float(prev["high"].astype(float).max())
    prev_low = safe_float(prev["low"].astype(float).min())
    if prev_high is None or prev_low is None:
        return None

    # Break up на bar[-2] і retest level на bar[-1].
    if float(c2) > float(prev_high):
        level = float(prev_high)
        touched = float(l1) <= level <= float(h1)
        held = float(c1) >= level
        if touched and held:
            direction = "LONG"
            return SmcExecutionEvent(
                event_type="RETEST_OK",
                direction=direction,
                time=ts,
                price=float(c1),
                level=level,
                ref=ref,
                poi_zone_id=poi_zone_id,
                meta={"source": "break_hold", "bias": bias_u},
            )

    # Break down на bar[-2] і retest на bar[-1].
    if float(c2) < float(prev_low):
        level = float(prev_low)
        touched = float(l1) <= level <= float(h1)
        held = float(c1) <= level
        if touched and held:
            direction = "SHORT"
            return SmcExecutionEvent(
                event_type="RETEST_OK",
                direction=direction,
                time=ts,
                price=float(c1),
                level=level,
                ref=ref,
                poi_zone_id=poi_zone_id,
                meta={"source": "break_hold", "bias": bias_u},
            )

    return None


def _row_ts(frame: pd.DataFrame, idx: int) -> pd.Timestamp | None:
    """Повертає timestamp рядка, підтримуючи DatetimeIndex або timestamp колонку."""

    try:
        if isinstance(frame.index, pd.DatetimeIndex):
            ts = frame.index[idx]
            if isinstance(ts, pd.Timestamp):
                return ts
    except Exception:
        pass

    try:
        if "timestamp" in frame.columns:
            ts = pd.to_datetime(frame["timestamp"].iloc[idx], utc=True, errors="coerce")
            if isinstance(ts, pd.Timestamp) and not pd.isna(ts):
                return ts
    except Exception:
        return None

    return None
