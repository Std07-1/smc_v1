"""Stage6: машинний вибір сценарію 4.2 vs 4.3.

Призначення Stage6:
- це не «сигнал на вхід», а технічний розбір, який класифікує поведінку
  після sweep/маніпуляції в HTF-контексті.
- результат повертається в `smc_hint.signals[]` як JSON-friendly dict.

Архітектурно:
- SMC-core формує «сирий» сценарій (без гістерезису/TTL);
- анти-фліп/TTL виконується на рівні `app.SmcStateManager`.

Важливо:
- Логіка детермінована і має право повертати `UNCLEAR`.
- Всі тексти/пояснення (`why`) українською.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd

from smc_core.smc_types import (
    SmcLiquidityPool,
    SmcLiquidityState,
    SmcStructureEvent,
    SmcStructureState,
    SmcZonesState,
)

ScenarioId = Literal["4_2", "4_3", "UNCLEAR"]
ScenarioDirection = Literal["LONG", "SHORT", "NEUTRAL"]

# ── HTF‑Lite дефолти (Variant B, грудень 2025) ─────────────────────────────

HTF_LITE_ATR_PERIOD: int = 14
HTF_LITE_MIN_BARS: int = HTF_LITE_ATR_PERIOD + 2  # 16
HTF_LITE_DR_LOOKBACK_1H: int = 48  # ≈2 доби
HTF_LITE_DR_LOOKBACK_4H: int = 30  # ≈5 діб

# ── Stage6 P0: hold_above / failed_hold (грудень 2025) ───────────────────

HOLD_BARS: int = 3  # 5m
HOLD_EPS_ATR: float = 0.05
HOLD_EPS_ATR_HTF: float = 0.03

W_HOLD: float = 3.2
W_HOLD_PENALTY: float = 2.4
W_FAIL_HOLD: float = 2.8
W_FAIL_HOLD_PENALTY: float = 2.2


@dataclass(frozen=True, slots=True)
class Stage6Decision:
    scenario_id: ScenarioId
    direction: ScenarioDirection
    confidence: float
    why: list[str]
    # Важливо: UI та QA використовують `scenario_raw_key_levels` як payload-словник,
    # тому тут дозволяємо nested dict/list (не лише float).
    key_levels: dict[str, Any]
    telemetry: dict[str, Any]


def decide_42_43(
    *,
    symbol: str,
    tf_primary: str,
    primary_frame: pd.DataFrame,
    ohlc_by_tf: Mapping[str, pd.DataFrame] | None,
    structure: SmcStructureState | None,
    liquidity: SmcLiquidityState | None,
    zones: SmcZonesState | None,
    context: dict[str, Any] | None,
) -> Stage6Decision:
    """Повертає детерміноване рішення `4_2/4_3/UNCLEAR`.

    Гейти:
    - має бути HTF bias (з контексту або fallback з 1h/4h);
    - має бути діапазон (dealing range) або proxy range.

    Параметри та ваги наразі «жорсткі» (мінімальний диф). Якщо треба —
    винесемо в SmcCoreConfig наступною хвилею.
    """

    key_levels: dict[str, Any] = {}
    telemetry: dict[str, Any] = {
        "inputs_ok": True,
        "gates": [],
        "unclear_reason": None,
        "score": {"4_2": 0.0, "4_3": 0.0},
    }

    # SMC-словник для UI: заповнюємо поступово та кладемо в key_levels["smc"].
    smc: dict[str, Any] = {
        "htf": {
            "ready": False,
            "bars_1h": 0,
            "bars_4h": 0,
            "min_bars": int(HTF_LITE_MIN_BARS),
            "dr_high": None,
            "dr_low": None,
            "dr_mid": None,
            "pd": None,
            "atr14": None,
            "magnets": [],
            "bias": None,
            "bias_src": None,
            "bias_raw": None,
            "bias_raw_src": None,
            "dr_tf": None,
            "atr_tf": None,
        },
        "structure_5m": {
            "range_high": None,
            "range_low": None,
            "range_mid": None,
            "bias_5m": None,
            "last_event": None,
            "events_after_sweep": {"truth": "NONE", "ts": None},
        },
        "facts": {
            "sweep": None,
            "hold": {"level_up": None, "k": int(HOLD_BARS), "ok": False},
            "failed_hold": {"level_up": None, "ok": False},
        },
        "poi_active": [],
        "targets_near": [],
    }
    key_levels["smc"] = smc

    last_price = _extract_last_close(primary_frame)
    if last_price is None:
        return Stage6Decision(
            scenario_id="UNCLEAR",
            direction="NEUTRAL",
            confidence=0.0,
            why=["Гейт: немає last_price у primary_frame"],
            key_levels={},
            telemetry={
                "inputs_ok": False,
                "gates": ["no_last_price"],
                "unclear_reason": "NO_LAST_PRICE",
            },
        )

    # ── HTF‑Lite: чесна перевірка наявності 1h+4h і мінімального прогріву ──

    ohlc = ohlc_by_tf or {}
    frame_1h = ohlc.get("1h")
    frame_4h = ohlc.get("4h")

    bars_1h = _count_complete_bars(frame_1h)
    bars_4h = _count_complete_bars(frame_4h)
    telemetry["htf_bars_1h"] = int(bars_1h)
    telemetry["htf_bars_4h"] = int(bars_4h)
    telemetry["htf_min_bars"] = int(HTF_LITE_MIN_BARS)

    smc["htf"]["bars_1h"] = int(bars_1h)
    smc["htf"]["bars_4h"] = int(bars_4h)
    smc["htf"]["min_bars"] = int(HTF_LITE_MIN_BARS)

    if bars_1h < HTF_LITE_MIN_BARS or bars_4h < HTF_LITE_MIN_BARS:
        telemetry["inputs_ok"] = False
        telemetry["gates"].append("no_htf_frames")
        telemetry["unclear_reason"] = "NO_HTF_FRAMES"
        return Stage6Decision(
            scenario_id="UNCLEAR",
            direction="NEUTRAL",
            confidence=0.0,
            why=[
                "Гейт: no_htf_frames (потрібно >=16 complete-барів на 1h і 4h)",
                f"1h={bars_1h}, 4h={bars_4h}",
            ],
            key_levels=key_levels,
            telemetry=telemetry,
        )

    # ── ATR(14): 4h пріоритет, fallback 1h ───────────────────────────────

    atr_4h = _calc_atr(frame_4h, period=HTF_LITE_ATR_PERIOD)
    atr_1h = _calc_atr(frame_1h, period=HTF_LITE_ATR_PERIOD)
    if atr_4h is not None:
        htf_atr = float(atr_4h)
        htf_atr_tf = "4h"
    elif atr_1h is not None:
        htf_atr = float(atr_1h)
        htf_atr_tf = "1h"
    else:
        telemetry["inputs_ok"] = False
        telemetry["gates"].append("atr_unavailable")
        telemetry["unclear_reason"] = "ATR_UNAVAILABLE"
        return Stage6Decision(
            scenario_id="UNCLEAR",
            direction="NEUTRAL",
            confidence=0.0,
            why=[
                "Гейт: atr_unavailable (не вдалось порахувати ATR(14) ні на 4h, ні на 1h)",
            ],
            key_levels={},
            telemetry=telemetry,
        )

    telemetry["htf_atr14"] = float(htf_atr)
    telemetry["htf_atr_tf"] = str(htf_atr_tf)
    key_levels["htf_atr14"] = float(htf_atr)
    smc["htf"]["atr14"] = float(htf_atr)
    smc["htf"]["atr_tf"] = str(htf_atr_tf)

    # ── HTF‑Lite DR: 4h пріоритет (але працюємо з тим, що є) ─────────────

    dr_4h = _calc_dr_levels(frame_4h, lookback=HTF_LITE_DR_LOOKBACK_4H)
    dr_1h = _calc_dr_levels(frame_1h, lookback=HTF_LITE_DR_LOOKBACK_1H)

    if dr_4h is not None:
        htf_dr_high, htf_dr_low, htf_dr_mid, htf_dr_n_used = dr_4h
        htf_dr_tf = "4h"
        htf_dr_n = HTF_LITE_DR_LOOKBACK_4H
    elif dr_1h is not None:
        htf_dr_high, htf_dr_low, htf_dr_mid, htf_dr_n_used = dr_1h
        htf_dr_tf = "1h"
        htf_dr_n = HTF_LITE_DR_LOOKBACK_1H
    else:
        telemetry["inputs_ok"] = False
        telemetry["gates"].append("no_htf_frames")
        telemetry["unclear_reason"] = "NO_HTF_FRAMES"
        return Stage6Decision(
            scenario_id="UNCLEAR",
            direction="NEUTRAL",
            confidence=0.0,
            why=["Гейт: no_htf_frames (не вдалось побудувати HTF‑Lite DR)"],
            key_levels={},
            telemetry=telemetry,
        )

    telemetry["htf_lite_dr_tf"] = str(htf_dr_tf)
    telemetry["htf_lite_dr_n"] = int(htf_dr_n)
    telemetry["htf_lite_dr_n_used"] = int(htf_dr_n_used)
    key_levels.update(
        {
            "htf_dr_high": float(htf_dr_high),
            "htf_dr_low": float(htf_dr_low),
            "htf_dr_mid": float(htf_dr_mid),
        }
    )

    smc["htf"]["ready"] = True
    smc["htf"]["dr_high"] = float(htf_dr_high)
    smc["htf"]["dr_low"] = float(htf_dr_low)
    smc["htf"]["dr_mid"] = float(htf_dr_mid)
    smc["htf"]["dr_tf"] = str(htf_dr_tf)

    htf_pd_zone: str
    htf_lite_bias: Literal["LONG", "SHORT", "NEUTRAL"]
    if last_price > htf_dr_mid:
        htf_pd_zone = "PREMIUM"
        htf_lite_bias = "SHORT"
    elif last_price < htf_dr_mid:
        htf_pd_zone = "DISCOUNT"
        htf_lite_bias = "LONG"
    else:
        htf_pd_zone = "MID"
        htf_lite_bias = "NEUTRAL"

    telemetry["htf_lite_pd_zone"] = htf_pd_zone
    telemetry["htf_lite_bias"] = htf_lite_bias
    smc["htf"]["pd"] = str(htf_pd_zone)

    # ── HTF bias (контрактний) + fallback через HTF‑Lite ─────────────────

    htf_bias_raw, htf_bias_src_raw, _htf_why = _infer_htf_bias(
        context=context or {}, ohlc_by_tf=ohlc, primary_frame=primary_frame
    )
    telemetry["htf_bias_raw"] = htf_bias_raw
    telemetry["htf_bias_raw_src"] = htf_bias_src_raw

    smc["htf"]["bias_raw"] = str(htf_bias_raw)
    smc["htf"]["bias_raw_src"] = str(htf_bias_src_raw)

    htf_bias = htf_bias_raw
    htf_bias_src = htf_bias_src_raw
    if htf_bias_raw in ("UNKNOWN", "NEUTRAL") and htf_lite_bias in ("LONG", "SHORT"):
        htf_bias = htf_lite_bias
        htf_bias_src = "htf_lite(pd)"

    telemetry["htf_bias"] = htf_bias
    telemetry["htf_bias_src"] = htf_bias_src

    smc["htf"]["bias"] = str(htf_bias)
    smc["htf"]["bias_src"] = str(htf_bias_src)
    if htf_bias in ("UNKNOWN", "NEUTRAL"):
        telemetry["inputs_ok"] = False
        telemetry["gates"].append("no_htf_bias")
        telemetry["unclear_reason"] = "NO_HTF"
        return Stage6Decision(
            scenario_id="UNCLEAR",
            direction="NEUTRAL",
            confidence=0.0,
            why=[
                "Гейт: немає валідного HTF bias (контекст/фрейми NEUTRAL/UNKNOWN і HTF‑Lite не дає bias)",
            ],
            key_levels=key_levels,
            telemetry=telemetry,
        )

    # HTF магніти: бережемо малий список, який трейдеру видно на мапі.
    pools_for_magnets = list(liquidity.pools) if liquidity is not None else []
    smc["htf"]["magnets"] = _build_htf_magnets(
        context=context or {},
        pools=pools_for_magnets,
        last_price=float(last_price),
        atr=float(htf_atr),
    )

    range_high, range_low, range_eq, range_src = _extract_dealing_range(
        structure=structure, primary_frame=primary_frame
    )
    telemetry["range_src"] = range_src
    if range_high is None or range_low is None or range_high <= range_low:
        telemetry["inputs_ok"] = False
        telemetry["gates"].append("no_range")
        telemetry["unclear_reason"] = "NO_RANGE"
        return Stage6Decision(
            scenario_id="UNCLEAR",
            direction="NEUTRAL",
            confidence=0.0,
            why=["Гейт: немає dealing range (range_high/low)"],
            key_levels={},
            telemetry=telemetry,
        )

    # Structure gate: Stage6 має спиратися на 5m структуру (BOS/CHOCH/свінги).
    if structure is None or (
        not isinstance(getattr(structure, "events", None), list)
        or (
            len(structure.events) == 0
            and len(getattr(structure, "swings", []) or []) < 2
        )
    ):
        telemetry["inputs_ok"] = False
        telemetry["gates"].append("no_structure")
        telemetry["unclear_reason"] = "NO_STRUCTURE"
        return Stage6Decision(
            scenario_id="UNCLEAR",
            direction="NEUTRAL",
            confidence=0.0,
            why=["Гейт: немає достатньої 5m структури (BOS/CHOCH/свінги)"],
            key_levels=key_levels,
            telemetry=telemetry,
        )

    key_levels.update({"range_high": range_high, "range_low": range_low})
    if range_eq is not None:
        key_levels["range_eq"] = range_eq

    range_mid = (float(range_high) + float(range_low)) / 2.0
    smc["structure_5m"]["range_high"] = float(range_high)
    smc["structure_5m"]["range_low"] = float(range_low)
    smc["structure_5m"]["range_mid"] = float(range_mid)

    bias_5m = None
    if structure is not None:
        b = str(getattr(structure, "bias", "NEUTRAL") or "NEUTRAL").upper()
        bias_5m = "MIXED" if b == "NEUTRAL" else b
    smc["structure_5m"]["bias_5m"] = bias_5m

    is_premium = (range_eq is not None and last_price > range_eq) or (
        range_eq is None and last_price > (range_low + (range_high - range_low) * 0.5)
    )
    is_discount = not is_premium
    telemetry["is_premium"] = bool(is_premium)
    telemetry["is_discount"] = bool(is_discount)

    # ── Події (sweep / break&hold) ───────────────────────────────────────

    pools = list(liquidity.pools) if liquidity is not None else []
    sweep = _detect_sweep(primary_frame=primary_frame, pools=pools)
    if sweep is not None:
        key_levels["swept_level"] = float(sweep.level)
        telemetry["sweep"] = {
            "side": sweep.side,
            "level": float(sweep.level),
            "pool_type": sweep.pool_type,
            "time": (
                sweep.time.isoformat() if isinstance(sweep.time, pd.Timestamp) else None
            ),
        }

        # age: в барах (5m), якщо можемо, інакше None.
        age_bars: int | None = None
        try:
            ts = primary_frame.get("timestamp")
            if ts is not None and len(ts) > 0:
                last_ts = ts.iloc[-1]
                if isinstance(last_ts, pd.Timestamp) and isinstance(
                    sweep.time, pd.Timestamp
                ):
                    dt = last_ts - sweep.time
                    age_bars = int(round(dt.total_seconds() / 300.0))
        except Exception:
            age_bars = None

        smc["facts"]["sweep"] = {
            "side": str(sweep.side),
            "level": float(sweep.level),
            "pool_type": str(sweep.pool_type),
            "ts": (
                sweep.time.isoformat() if isinstance(sweep.time, pd.Timestamp) else None
            ),
            "age_bars": age_bars,
        }

    events = list(structure.events) if structure is not None else []

    smc["structure_5m"]["last_event"] = _extract_last_event(events)

    # Події після sweep: одна істина (останній валідний напрям), або CHOP.
    sweep_time = sweep.time if sweep is not None else None
    events_after = _events_after_sweep_truth(events=events, sweep_time=sweep_time)
    smc["structure_5m"]["events_after_sweep"] = {
        "truth": events_after.get("truth"),
        "ts": events_after.get("ts"),
    }

    # Сумісні прапорці для існуючого стабілізатора.
    truth = str(events_after.get("truth") or "NONE").upper()
    telemetry["events_after_sweep"] = {
        "truth": truth,
        "ts": events_after.get("ts"),
        "bos_down": truth == "BOS_DOWN",
        "bos_up": truth == "BOS_UP",
        "choch_up": truth == "CHOCH_UP",
        "choch_down": truth == "CHOCH_DOWN",
        "chop": truth == "CHOP",
    }

    # ── P0: det. hold levels + hold_above / failed_hold (взаємовиключний перемикач) ──

    atr_5m = _atr_like(structure)
    atr_ref = float(atr_5m) if atr_5m is not None else float(htf_atr)
    eps_atr = float(HOLD_EPS_ATR) if atr_5m is not None else float(HOLD_EPS_ATR_HTF)
    hold_eps = float(atr_ref) * float(eps_atr)
    telemetry["hold"] = {
        "k": int(HOLD_BARS),
        "eps_atr": float(eps_atr),
        "eps": float(hold_eps),
        "atr_ref": float(atr_ref),
        "atr_ref_src": "5m" if atr_5m is not None else "htf",
    }

    # P0 (детермінізм): один канонічний рівень для hold/fail-hold.
    # Щоб не було "сьогодні range_high, завтра htf_dr_high", завжди беремо 5m range_high.
    hold_level_up = float(range_high)
    hold_level_dn = float(range_low)
    key_levels["hold_level_up"] = float(hold_level_up)
    key_levels["hold_level_dn"] = float(hold_level_dn)

    telemetry["hold_level_up"] = {"level": float(hold_level_up), "src": "range_high"}
    telemetry["hold_level_dn"] = {"level": float(hold_level_dn), "src": "range_low"}

    hold_above_up = _hold_above(
        primary_frame=primary_frame,
        level=float(hold_level_up),
        k=int(HOLD_BARS),
        eps=float(hold_eps),
    )

    failed_hold_up = _failed_hold_up(
        primary_frame=primary_frame,
        level=float(hold_level_up),
        swept_level=(
            float(sweep.level) if (sweep is not None and sweep.side == "UP") else None
        ),
        hold_above=bool(hold_above_up),
        eps=float(hold_eps),
    )

    telemetry["hold_above_up"] = bool(hold_above_up)
    telemetry["failed_hold_up"] = bool(failed_hold_up)
    smc["facts"]["hold"] = {
        "level_up": float(hold_level_up),
        "k": int(HOLD_BARS),
        "ok": bool(hold_above_up),
    }
    smc["facts"]["failed_hold"] = {
        "level_up": float(hold_level_up),
        "ok": bool(failed_hold_up),
    }

    # break&hold (старий патерн) лишається як слабкий підтверджувач
    break_hold_up = False
    retest_hold_up = False
    break_hold_up, retest_hold_up = _detect_break_hold_up(
        primary_frame=primary_frame,
        key_level=float(range_high),
        hold_bars=int(HOLD_BARS),
    )
    telemetry["break_hold_up"] = {
        "ok": bool(break_hold_up),
        "retest_hold": bool(retest_hold_up),
        "level": float(range_high),
    }

    # ── Фічі POI/targets (легкі) ─────────────────────────────────────────

    bearish_poi_near = _near_poi(
        zones=zones,
        side="SHORT",
        price=last_price,
        structure=structure,
        in_premium=is_premium,
    )
    bullish_poi_near = _near_poi(
        zones=zones,
        side="LONG",
        price=last_price,
        structure=structure,
        in_premium=is_discount,
    )

    targets_down_near = _targets_near(
        pools=pools,
        side="DOWN",
        price=last_price,
        structure=structure,
    )
    targets_up_near = _targets_near(
        pools=pools,
        side="UP",
        price=last_price,
        structure=structure,
    )

    # POI/Targets (структуровано, для UI; 1–3 елементи).
    smc["poi_active"] = _pick_poi_active(
        zones=zones,
        last_price=float(last_price),
        atr=float(htf_atr),
        limit_per_side=3,
    )
    smc["targets_near"] = _pick_targets_near(
        pools=pools,
        last_price=float(last_price),
        atr=float(htf_atr),
        limit=3,
    )

    # ── Scoring ─────────────────────────────────────────────────────────

    score_42 = 0.0
    score_43 = 0.0
    contrib_42: list[tuple[float, str]] = []
    contrib_43: list[tuple[float, str]] = []

    # HTF bias
    if htf_bias == "SHORT":
        score_42 += 2.2
        contrib_42.append((2.2, "HTF bias SHORT"))
        score_43 -= 0.6
    elif htf_bias == "LONG":
        score_43 += 1.2
        contrib_43.append((1.2, "HTF bias LONG"))
        score_42 -= 0.4
    else:  # MIXED
        score_43 += 0.4
        contrib_43.append((0.4, "HTF bias MIXED"))

    # P0c: HTF‑Lite bias не додаємо як окремий скоринговий факт.
    # HTF‑Lite використовується лише для fallback вибору `htf_bias` вище (коли raw bias UNKNOWN/NEUTRAL).

    # Premium/discount
    if is_premium:
        score_42 += 0.9
        contrib_42.append((0.9, "Ціна у premium"))
    else:
        score_43 += 0.4
        contrib_43.append((0.4, "Ціна у discount"))

    # Sweep
    if sweep is not None:
        if sweep.side == "UP":
            score_42 += 0.9
            contrib_42.append((0.9, "Sweep UP (BSL/EQH)"))
            score_43 += 0.2
        else:
            score_43 += 0.6
            contrib_43.append((0.6, "Sweep DOWN"))

    # Rejection/acceptance proxies via events
    if bool(telemetry.get("events_after_sweep", {}).get("bos_down")):
        score_42 += 1.8
        contrib_42.append((1.8, "BOS_DOWN після sweep"))
    if bool(telemetry.get("events_after_sweep", {}).get("bos_up")):
        score_43 += 1.6
        contrib_43.append((1.6, "BOS_UP після sweep"))
    if bool(telemetry.get("events_after_sweep", {}).get("choch_up")):
        score_43 += 1.2
        contrib_43.append((1.2, "CHOCH_UP після sweep"))

    # Break&hold
    if break_hold_up:
        score_43 += 2.2
        contrib_43.append((2.2, "Break&Hold UP (ключовий рівень)"))
        score_42 -= 0.6
    if retest_hold_up:
        score_43 += 0.6
        contrib_43.append((0.6, "Retest&Hold після пробою"))

    # P0: взаємовиключний перемикач hold vs failed_hold (прибирає CONFLICT логікою)
    if hold_above_up:
        score_43 += float(W_HOLD)
        contrib_43.append((float(W_HOLD), "Hold ABOVE (range_high)"))
        score_42 -= float(W_HOLD_PENALTY)
        contrib_42.append((-float(W_HOLD_PENALTY), "Penalty: hold_above (не 4.2)"))

    if failed_hold_up:
        score_42 += float(W_FAIL_HOLD)
        contrib_42.append(
            (
                float(W_FAIL_HOLD),
                "Failed hold після sweep (range_high)",
            )
        )
        score_43 -= float(W_FAIL_HOLD_PENALTY)
        contrib_43.append(
            (-float(W_FAIL_HOLD_PENALTY), "Penalty: failed_hold (не 4.3)")
        )

    # Targets
    if targets_down_near:
        score_42 += 0.8
        contrib_42.append((0.8, "Є близькі targets вниз"))
    if targets_up_near:
        score_43 += 0.5
        contrib_43.append((0.5, "Є близькі targets вгору"))

    # POI
    if bearish_poi_near:
        score_42 += 0.6
        contrib_42.append((0.6, "Bearish POI поруч"))
    if bullish_poi_near:
        score_43 += 0.4
        contrib_43.append((0.4, "Bullish POI поруч"))

    telemetry["score"]["4_2"] = round(score_42, 4)
    telemetry["score"]["4_3"] = round(score_43, 4)

    min_score = 2.1
    score_delta = 0.65
    telemetry["min_score"] = float(min_score)
    telemetry["score_delta"] = float(score_delta)

    if max(score_42, score_43) < min_score:
        telemetry["unclear_reason"] = "LOW_SCORE"
        return Stage6Decision(
            scenario_id="UNCLEAR",
            direction="NEUTRAL",
            confidence=0.0,
            why=_build_why_canonical(
                scenario_id="UNCLEAR",
                direction="NEUTRAL",
                htf=smc.get("htf") or {},
                structure_5m=smc.get("structure_5m") or {},
                facts=smc.get("facts") or {},
                targets=smc.get("targets_near") or [],
                poi=smc.get("poi_active") or [],
                conclusion_hint="UNCLEAR (скор нижче порогу)",
            ),
            key_levels=key_levels,
            telemetry=telemetry,
        )

    if abs(score_42 - score_43) < score_delta:
        telemetry["unclear_reason"] = "CONFLICT"
        return Stage6Decision(
            scenario_id="UNCLEAR",
            direction="NEUTRAL",
            confidence=0.0,
            why=_build_why_canonical(
                scenario_id="UNCLEAR",
                direction="NEUTRAL",
                htf=smc.get("htf") or {},
                structure_5m=smc.get("structure_5m") or {},
                facts=smc.get("facts") or {},
                targets=smc.get("targets_near") or [],
                poi=smc.get("poi_active") or [],
                conclusion_hint="UNCLEAR (конфлікт скорів)",
            ),
            key_levels=key_levels,
            telemetry=telemetry,
        )

    if score_42 >= score_43:
        scenario_id: ScenarioId = "4_2"
        direction: ScenarioDirection = "SHORT"
        winner = score_42
        loser = score_43
    else:
        scenario_id = "4_3"
        direction = "LONG"
        winner = score_43
        loser = score_42

    confidence = _confidence_from_scores(winner=winner, loser=loser)

    return Stage6Decision(
        scenario_id=scenario_id,
        direction=direction,
        confidence=confidence,
        why=_build_why_canonical(
            scenario_id=scenario_id,
            direction=direction,
            htf=smc.get("htf") or {},
            structure_5m=smc.get("structure_5m") or {},
            facts=smc.get("facts") or {},
            targets=smc.get("targets_near") or [],
            poi=smc.get("poi_active") or [],
            conclusion_hint=None,
        ),
        key_levels=key_levels,
        telemetry=telemetry,
    )


def to_signal_dict(decision: Stage6Decision) -> dict[str, Any]:
    """Конвертує рішення Stage6 у plain dict для `smc_hint.signals[]`."""

    return {
        "type": "SCENARIO",
        "direction": decision.direction,
        "confidence": float(decision.confidence),
        "meta": {
            "scenario_id": decision.scenario_id,
            "why": list(decision.why),
            "key_levels": dict(decision.key_levels),
            "telemetry": dict(decision.telemetry),
        },
    }


# ── Internal helpers ─────────────────────────────────────────────────────


def _extract_last_close(frame: pd.DataFrame) -> float | None:
    if frame is None or frame.empty:
        return None
    if "close" not in frame.columns:
        return None
    try:
        v = float(pd.to_numeric(frame["close"], errors="coerce").iloc[-1])
        if math.isfinite(v) and v > 0:
            return v
    except Exception:
        return None
    return None


def _count_complete_bars(frame: pd.DataFrame | None) -> int:
    """Рахує кількість 'complete' барів у фреймі.

    Якщо стовпця `complete` немає — вважаємо всі бари complete.
    """

    if frame is None or frame.empty:
        return 0
    try:
        if "complete" in frame.columns:
            s = frame["complete"]
            # Бар вважаємо неcomplete лише якщо значення строго False.
            return int((s != False).sum())  # noqa: E712
        return int(len(frame))
    except Exception:
        return 0


def _tail_complete(frame: pd.DataFrame, n: int) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    df = frame
    if "complete" in df.columns:
        try:
            df = df[df["complete"] != False]  # noqa: E712
        except Exception:
            df = frame
    n_int = max(1, int(n))
    return df.tail(n_int)


def _calc_dr_levels(
    frame: pd.DataFrame | None, *, lookback: int
) -> tuple[float, float, float, int] | None:
    """Обчислює HTF‑Lite dealing range (high/low/mid) на tail N барах."""

    if frame is None or frame.empty:
        return None
    if "high" not in frame.columns or "low" not in frame.columns:
        return None

    df = _tail_complete(frame, int(lookback))
    if df.empty:
        return None

    try:
        high = pd.to_numeric(df["high"], errors="coerce").dropna()
        low = pd.to_numeric(df["low"], errors="coerce").dropna()
        if high.empty or low.empty:
            return None
        high_val = float(high.max())
        low_val = float(low.min())
        if (
            not math.isfinite(high_val)
            or not math.isfinite(low_val)
            or high_val <= low_val
        ):
            return None
        mid = (high_val + low_val) / 2.0
        if not math.isfinite(mid):
            return None
        return high_val, low_val, mid, int(len(df))
    except Exception:
        return None


def _calc_atr(frame: pd.DataFrame | None, *, period: int) -> float | None:
    """Обчислює ATR(period) як SMA TrueRange на tail.

    Повертає останнє значення ATR або None, якщо обчислення неможливе.
    """

    if frame is None or frame.empty:
        return None
    if (
        "high" not in frame.columns
        or "low" not in frame.columns
        or "close" not in frame.columns
    ):
        return None

    p = max(1, int(period))
    df = _tail_complete(frame, p + 64)
    if df.empty:
        return None

    try:
        high = pd.to_numeric(df["high"], errors="coerce")
        low = pd.to_numeric(df["low"], errors="coerce")
        close = pd.to_numeric(df["close"], errors="coerce")
        tmp = pd.DataFrame({"high": high, "low": low, "close": close}).dropna()
        if len(tmp) < (p + 1):
            return None
        prev_close = tmp["close"].shift(1)
        tr_components = pd.concat(
            [
                (tmp["high"] - tmp["low"]).abs(),
                (tmp["high"] - prev_close).abs(),
                (tmp["low"] - prev_close).abs(),
            ],
            axis=1,
        )
        tr = tr_components.max(axis=1)
        atr = tr.rolling(window=p, min_periods=p).mean()
        if atr.empty:
            return None
        v = float(atr.iloc[-1])
        if math.isfinite(v) and v > 0:
            return v
        return None
    except Exception:
        return None


def _infer_htf_bias(
    *,
    context: dict[str, Any],
    ohlc_by_tf: Mapping[str, pd.DataFrame],
    primary_frame: pd.DataFrame,
) -> tuple[Literal["LONG", "SHORT", "MIXED", "NEUTRAL", "UNKNOWN"], str, list[str]]:
    """Повертає HTF bias + джерело + пояснення.

    Пріоритет:
    1) `context.trend_context_4h` / `context.trend_context_h1` (якщо присутні).
    2) fallback: грубий нахил close на 4h/1h, якщо фрейми прокинуті в контекст.

    Примітка: у поточному пайплайні контекст може бути порожнім, тому
    цей fallback дозволяє Stage6 працювати без додаткових залежностей.
    """

    why: list[str] = []

    bias_4h = _read_bias_from_context(context.get("trend_context_4h"))
    bias_1h = _read_bias_from_context(context.get("trend_context_h1"))

    if bias_4h or bias_1h:
        b4 = bias_4h or "UNKNOWN"
        b1 = bias_1h or "UNKNOWN"
        if b4 != "UNKNOWN" and b1 != "UNKNOWN" and b4 != b1:
            return "MIXED", "context(4h+1h)", [f"4h={b4}, 1h={b1}"]
        if b4 != "UNKNOWN":
            return b4, "context(4h)", []
        if b1 != "UNKNOWN":
            return b1, "context(1h)", []

    # Fallback: використовуємо вже підвантажені 1h/4h фрейми з SmcInput.ohlc_by_tf.
    b4 = _bias_from_frame(ohlc_by_tf.get("4h"))
    b1 = _bias_from_frame(ohlc_by_tf.get("1h"))
    if b4 != "UNKNOWN" or b1 != "UNKNOWN":
        if b4 != "UNKNOWN" and b1 != "UNKNOWN" and b4 != b1:
            return "MIXED", "frames(4h+1h)", [f"4h={b4}, 1h={b1}"]
        if b4 != "UNKNOWN" and b4 != "NEUTRAL":
            return b4, "frames(4h)", []
        if b1 != "UNKNOWN" and b1 != "NEUTRAL":
            return b1, "frames(1h)", []
        # Якщо обидва NEUTRAL — вважаємо bias невалідним для Stage6.
        return "NEUTRAL", "frames(neutral)", []

    # Якщо ні контексту, ні HTF фреймів — гейт має бути чесним.
    return "UNKNOWN", "none", why


def _bias_from_frame(
    frame: pd.DataFrame | None,
) -> Literal["LONG", "SHORT", "NEUTRAL", "UNKNOWN"]:
    if frame is None or frame.empty:
        return "UNKNOWN"
    if "close" not in frame.columns:
        return "UNKNOWN"

    close = pd.to_numeric(frame["close"], errors="coerce").dropna()
    if len(close) < 6:
        return "UNKNOWN"

    tail = close.tail(40)
    first = float(tail.iloc[0])
    last = float(tail.iloc[-1])
    if not math.isfinite(first) or not math.isfinite(last) or first <= 0:
        return "UNKNOWN"

    delta_pct = (last - first) / abs(first)
    thr = 0.002  # 0.2%: дуже грубий, але стабільний fallback
    if delta_pct >= thr:
        return "LONG"
    if delta_pct <= -thr:
        return "SHORT"
    return "NEUTRAL"


def _read_bias_from_context(
    value: Any,
) -> Literal["LONG", "SHORT", "NEUTRAL", "UNKNOWN"]:
    if not isinstance(value, dict):
        return "UNKNOWN"
    raw = value.get("bias") or value.get("trend") or value.get("direction")
    if not isinstance(raw, str):
        return "UNKNOWN"
    s = raw.strip().upper()
    if s in ("LONG", "UP", "BULL", "BULLISH"):
        return "LONG"
    if s in ("SHORT", "DOWN", "BEAR", "BEARISH"):
        return "SHORT"
    if s in ("NEUTRAL", "RANGE", "MIXED"):
        return "NEUTRAL"
    return "UNKNOWN"


def _extract_dealing_range(
    *, structure: SmcStructureState | None, primary_frame: pd.DataFrame
) -> tuple[float | None, float | None, float | None, str]:
    if structure is not None and structure.active_range is not None:
        r = structure.active_range
        try:
            return (
                float(r.high),
                float(r.low),
                float(r.eq_level),
                "structure.active_range",
            )
        except Exception:
            pass

    # Proxy range: по останнім N барам 5m. Це гірше, але краще ніж завжди UNCLEAR.
    n = 72  # ~6 годин на 5m
    if primary_frame is None or primary_frame.empty:
        return None, None, None, "none"
    if "high" not in primary_frame.columns or "low" not in primary_frame.columns:
        return None, None, None, "none"
    tail = primary_frame.tail(n)
    try:
        hi = float(pd.to_numeric(tail["high"], errors="coerce").max())
        lo = float(pd.to_numeric(tail["low"], errors="coerce").min())
        if math.isfinite(hi) and math.isfinite(lo) and hi > lo:
            eq = (hi + lo) / 2.0
            return hi, lo, eq, f"proxy(last_{n}_bars)"
    except Exception:
        return None, None, None, "none"
    return None, None, None, "none"


@dataclass(frozen=True, slots=True)
class _Sweep:
    side: Literal["UP", "DOWN"]
    level: float
    pool_type: str
    time: pd.Timestamp


def _detect_sweep(
    *, primary_frame: pd.DataFrame, pools: list[SmcLiquidityPool]
) -> _Sweep | None:
    if primary_frame is None or primary_frame.empty:
        return None
    if "high" not in primary_frame.columns or "low" not in primary_frame.columns:
        return None
    if "close" not in primary_frame.columns:
        return None
    if "timestamp" not in primary_frame.columns:
        return None

    # Скануємо коротке вікно: останні 10 барів.
    tail = primary_frame.tail(10).copy()
    hi = pd.to_numeric(tail["high"], errors="coerce")
    lo = pd.to_numeric(tail["low"], errors="coerce")
    close = pd.to_numeric(tail["close"], errors="coerce")
    ts = pd.to_datetime(tail["timestamp"], utc=True, errors="coerce")

    best: _Sweep | None = None

    def _pool_type(p: SmcLiquidityPool) -> str:
        try:
            return str(p.liq_type.name)
        except Exception:
            return "OTHER"

    # Щоб не перебирати сотні рівнів: беремо тільки найсильніші.
    pools_sorted = sorted(
        pools, key=lambda p: float(getattr(p, "strength", 0.0) or 0.0), reverse=True
    )
    pools_sorted = pools_sorted[:12]

    for p in pools_sorted:
        try:
            level = float(p.level)
        except Exception:
            continue
        if not math.isfinite(level) or level <= 0:
            continue

        # Sweep UP: high > L і close < L
        mask_up = (hi > level) & (close < level)
        if mask_up.any():
            idx = int(mask_up[mask_up].index[-1])
            t = ts.loc[idx]
            if isinstance(t, pd.Timestamp) and not pd.isna(t):
                cand = _Sweep(side="UP", level=level, pool_type=_pool_type(p), time=t)
                best = cand

        # Sweep DOWN: low < L і close > L
        mask_dn = (lo < level) & (close > level)
        if mask_dn.any():
            idx = int(mask_dn[mask_dn].index[-1])
            t = ts.loc[idx]
            if isinstance(t, pd.Timestamp) and not pd.isna(t):
                cand = _Sweep(side="DOWN", level=level, pool_type=_pool_type(p), time=t)
                # Якщо знайшли down sweep — пріоритезуємо його лише якщо він новіший.
                if best is None or (
                    isinstance(best.time, pd.Timestamp) and t > best.time
                ):
                    best = cand

    return best


def _is_after(a: pd.Timestamp, b: pd.Timestamp) -> bool:
    """Повертає True якщо `a` не раніше за `b`.

    На дискретних ТФ sweep може займати 1–2 бари, і структурна подія може мати
    той самий timestamp, що й останній sweep-бар. Для Stage6 трактуємо це як
    «після sweep».
    """

    try:
        a_ts = pd.Timestamp(a)
        b_ts = pd.Timestamp(b)

        # Захист від порівняння tz-aware vs tz-naive.
        if getattr(a_ts, "tz", None) is not None and getattr(b_ts, "tz", None) is None:
            b_ts = b_ts.tz_localize(a_ts.tz)
        elif (
            getattr(a_ts, "tz", None) is None and getattr(b_ts, "tz", None) is not None
        ):
            a_ts = a_ts.tz_localize(b_ts.tz)

        return bool(a_ts >= b_ts)
    except Exception:
        return False


def _pick_key_level_up(
    *,
    range_high: float,
    sweep_level: float | None,
    structure: SmcStructureState | None,
) -> float | None:
    # Пріоритет 1: range_high.
    if math.isfinite(range_high) and range_high > 0:
        return float(range_high)
    # Пріоритет 2: swept_level.
    if sweep_level is not None and math.isfinite(sweep_level) and sweep_level > 0:
        return float(sweep_level)
    # Пріоритет 3: last swing high.
    if structure is not None and structure.swings:
        for sw in reversed(structure.swings):
            if getattr(sw, "kind", None) == "HIGH":
                try:
                    v = float(sw.price)
                except Exception:
                    continue
                if math.isfinite(v) and v > 0:
                    return v
    return None


def _detect_break_hold_up(
    *, primary_frame: pd.DataFrame, key_level: float, hold_bars: int
) -> tuple[bool, bool]:
    if primary_frame is None or primary_frame.empty:
        return False, False
    if "close" not in primary_frame.columns or "low" not in primary_frame.columns:
        return False, False

    close = pd.to_numeric(primary_frame["close"], errors="coerce")
    low = pd.to_numeric(primary_frame["low"], errors="coerce")

    eps = max(abs(float(key_level)) * 0.0001, 1e-9)
    above = close > (float(key_level) + eps)

    # Вимагаємо останні hold_bars close вище ключового рівня.
    if len(above) < hold_bars:
        return False, False
    last_ok = bool(above.tail(hold_bars).all())
    if not last_ok:
        return False, False

    # Retest&hold: протягом останніх 8 барів був дотик low<=level, але close лишився вище.
    window = 8
    tail_close = close.tail(window)
    tail_low = low.tail(window)
    retest = ((tail_low <= key_level) & (tail_close > (key_level + eps))).any()
    return True, bool(retest)


def _atr_like(structure: SmcStructureState | None) -> float | None:
    if structure is None:
        return None
    meta = getattr(structure, "meta", None)
    if not isinstance(meta, dict):
        return None
    val = meta.get("atr_last") or meta.get("atr")
    # Guard: явно відкидаємо None та небезпечні типи перед викликом float()
    if val is None or not isinstance(val, (int, float, str)):
        return None
    try:
        v = float(val)
    except Exception:
        return None
    if math.isfinite(v) and v > 0:
        return v
    return None


def _extract_last_event(events: list[SmcStructureEvent]) -> dict[str, Any] | None:
    """Повертає останню структурну подію як dict для UI.

    Формат:
    - kind: BOS_UP/BOS_DOWN/CHOCH_UP/CHOCH_DOWN
    - ts: ISO-рядок або None
    """

    last: SmcStructureEvent | None = None
    for e in events or []:
        t = e.time
        if not isinstance(t, pd.Timestamp):
            continue
        if last is None:
            last = e
            continue
        try:
            if pd.Timestamp(last.time) < pd.Timestamp(t):
                last = e
        except Exception:
            continue

    if last is None:
        return None

    et = str(last.event_type or "").upper()
    d = str(last.direction or "").upper()
    kind = None
    if et == "BOS" and d == "LONG":
        kind = "BOS_UP"
    elif et == "BOS" and d == "SHORT":
        kind = "BOS_DOWN"
    elif et == "CHOCH" and d == "LONG":
        kind = "CHOCH_UP"
    elif et == "CHOCH" and d == "SHORT":
        kind = "CHOCH_DOWN"
    else:
        kind = f"{et}_{d}" if et or d else "UNKNOWN"

    ts = last.time.isoformat() if isinstance(last.time, pd.Timestamp) else None
    return {
        "kind": kind,
        "ts": ts,
        "price_level": _safe_float(last.price_level),
    }


def _events_after_sweep_truth(
    *, events: list[SmcStructureEvent], sweep_time: pd.Timestamp | None
) -> dict[str, Any]:
    """Одна істина після sweep: останній валідний напрям або CHOP.

    Правило CHOP: якщо дві останні події після sweep мають протилежний напрям
    і відбулися в межах одного 5m бару (<=5 хв), трактуємо як шум.
    """

    if sweep_time is None:
        return {"truth": "NONE", "ts": None}

    after: list[SmcStructureEvent] = []
    for e in events or []:
        t = e.time
        if not isinstance(t, pd.Timestamp):
            continue
        if not _is_after(t, sweep_time):
            continue
        et = str(e.event_type or "").upper()
        d = str(e.direction or "").upper()
        if et not in {"BOS", "CHOCH"}:
            continue
        if d not in {"LONG", "SHORT"}:
            continue
        after.append(e)

    if not after:
        return {"truth": "NONE", "ts": None}

    after_sorted = sorted(after, key=lambda x: pd.Timestamp(x.time))
    last = after_sorted[-1]
    prev = after_sorted[-2] if len(after_sorted) >= 2 else None

    last_et = str(last.event_type or "").upper()
    last_d = str(last.direction or "").upper()

    def _kind(et: str, d: str) -> str:
        if et == "BOS" and d == "LONG":
            return "BOS_UP"
        if et == "BOS" and d == "SHORT":
            return "BOS_DOWN"
        if et == "CHOCH" and d == "LONG":
            return "CHOCH_UP"
        if et == "CHOCH" and d == "SHORT":
            return "CHOCH_DOWN"
        return f"{et}_{d}" if et or d else "UNKNOWN"

    if prev is not None:
        try:
            prev_d = str(prev.direction or "").upper()
            dt = pd.Timestamp(last.time) - pd.Timestamp(prev.time)
            if last_d in {"LONG", "SHORT"} and prev_d in {"LONG", "SHORT"}:
                if last_d != prev_d and dt <= pd.Timedelta(minutes=5):
                    return {
                        "truth": "CHOP",
                        "ts": pd.Timestamp(last.time).isoformat(),
                    }
        except Exception:
            pass

    return {
        "truth": _kind(last_et, last_d),
        "ts": pd.Timestamp(last.time).isoformat(),
    }


def _build_htf_magnets(
    *,
    context: dict[str, Any],
    pools: list[SmcLiquidityPool],
    last_price: float,
    atr: float,
    limit: int = 3,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def add_ctx(name: str, key: str) -> None:
        v = _safe_float(context.get(key))
        if v is None:
            return
        out.append(
            {
                "name": name,
                "level": float(v),
                "src": f"context:{key}",
                "strength": None,
                "dist_atr": _dist_atr(float(v), last_price, atr),
            }
        )

    add_ctx("PDH", "pdh")
    add_ctx("PDL", "pdl")
    add_ctx("PWH", "pwh")
    add_ctx("PWL", "pwl")
    add_ctx("SESSION_HIGH", "smc_session_high")
    add_ctx("SESSION_LOW", "smc_session_low")

    # Додаємо сильні HTF-подібні пули (EQH/EQL/session extremes).
    for p in sorted(
        pools or [],
        key=lambda x: float(getattr(x, "strength", 0.0) or 0.0),
        reverse=True,
    ):
        liq_type = getattr(p, "liq_type", None)
        liq_name = str(getattr(liq_type, "name", "")) if liq_type is not None else ""
        if liq_name not in {
            "EQH",
            "EQL",
            "SESSION_HIGH",
            "SESSION_LOW",
            "RANGE_EXTREME",
        }:
            continue
        lvl = _safe_float(getattr(p, "level", None))
        if lvl is None:
            continue
        out.append(
            {
                "name": liq_name,
                "level": float(lvl),
                "src": "liquidity_pool",
                "strength": _safe_float(getattr(p, "strength", None)),
                "dist_atr": _dist_atr(float(lvl), last_price, atr),
            }
        )
        if len(out) >= 12:
            break

    # Дедуп по близьких рівнях і беремо найкорисніші (найближчі за ATR).
    dedup: list[dict[str, Any]] = []
    for m in out:
        lvl = _safe_float(m.get("level"))
        if lvl is None:
            continue
        if any(
            abs(float(lvl) - float(_safe_float(x.get("level")) or 0.0))
            <= max(atr * 0.08, 1e-6)
            for x in dedup
        ):
            continue
        dedup.append(m)

    dedup_sorted = sorted(
        dedup, key=lambda x: float(_safe_float(x.get("dist_atr")) or 999.0)
    )
    return dedup_sorted[: max(0, int(limit))]


def _pick_targets_near(
    *,
    pools: list[SmcLiquidityPool],
    last_price: float,
    atr: float,
    limit: int = 3,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for p in pools or []:
        lvl = _safe_float(getattr(p, "level", None))
        if lvl is None:
            continue
        liq_type = getattr(p, "liq_type", None)
        liq_name = (
            str(getattr(liq_type, "name", "OTHER")) if liq_type is not None else "OTHER"
        )
        items.append(
            {
                "tf": "5m",
                "role": str(getattr(p, "role", "NEUTRAL")),
                "type": liq_name,
                "level": float(lvl),
                "strength": _safe_float(getattr(p, "strength", None)),
                "dist_atr": _dist_atr(float(lvl), last_price, atr),
            }
        )

    items_sorted = sorted(
        items, key=lambda x: float(_safe_float(x.get("dist_atr")) or 999.0)
    )
    return items_sorted[: max(0, int(limit))]


def _pick_poi_active(
    *,
    zones: SmcZonesState | None,
    last_price: float,
    atr: float,
    limit_per_side: int = 3,
) -> list[dict[str, Any]]:
    if zones is None:
        return []

    candidates = list(getattr(zones, "poi_zones", None) or [])
    if not candidates:
        candidates = list(getattr(zones, "active_zones", None) or [])
    if not candidates:
        candidates = list(getattr(zones, "zones", None) or [])
    if not candidates:
        return []

    def z_score(z: Any) -> float:
        c = _safe_float(getattr(z, "confidence", None))
        s = _safe_float(getattr(z, "strength", None))
        if c is None:
            c = 0.0
        if s is None:
            s = 0.0
        return float(c) * 0.7 + float(s) * 0.3

    out: list[dict[str, Any]] = []
    per_side: dict[str, int] = {"LONG": 0, "SHORT": 0}
    for z in sorted(candidates, key=z_score, reverse=True)[:60]:
        side = str(getattr(z, "direction", "BOTH") or "BOTH").upper()
        if side not in {"LONG", "SHORT", "BOTH"}:
            side = "BOTH"

        zmin = _safe_float(getattr(z, "price_min", None))
        zmax = _safe_float(getattr(z, "price_max", None))
        if zmin is None or zmax is None:
            continue
        center = (float(zmin) + float(zmax)) / 2.0
        dist_atr = _dist_atr(center, last_price, atr)

        ztype = getattr(z, "zone_type", None)
        if ztype is None:
            ztype_name = "UNKNOWN"
        else:
            ztype_name = str(getattr(ztype, "name", ztype))

        meta_any = getattr(z, "meta", None)
        meta: dict[str, Any] = meta_any if isinstance(meta_any, dict) else {}
        filled_pct = _safe_float(
            meta.get("filled_pct") or meta.get("fill_pct") or meta.get("fill")
        )

        item = {
            "side": side,
            "type": ztype_name,
            "tf": str(getattr(z, "timeframe", "") or ""),
            "score": round(z_score(z), 4),
            "filled_pct": filled_pct,
            "dist_atr": dist_atr,
            "why": f"dist_atr={dist_atr:.2f} tf={getattr(z, 'timeframe', '')}",
        }

        if side == "BOTH":
            if per_side.get("LONG", 0) < int(limit_per_side):
                out.append(dict(item, side="LONG"))
                per_side["LONG"] = int(per_side.get("LONG", 0)) + 1
            if per_side.get("SHORT", 0) < int(limit_per_side):
                out.append(dict(item, side="SHORT"))
                per_side["SHORT"] = int(per_side.get("SHORT", 0)) + 1
        else:
            if per_side.get(side, 0) < int(limit_per_side):
                out.append(item)
                per_side[side] = int(per_side.get(side, 0)) + 1

        if per_side["LONG"] >= int(limit_per_side) and per_side["SHORT"] >= int(
            limit_per_side
        ):
            break

    # Впорядкуємо для стабільного UI: SHORT, потім LONG, всередині за dist.
    out_sorted = sorted(
        out,
        key=lambda x: (
            0 if str(x.get("side")) == "SHORT" else 1,
            float(_safe_float(x.get("dist_atr")) or 999.0),
            str(x.get("type") or ""),
        ),
    )
    return out_sorted


def _safe_float(v: Any) -> float | None:
    try:
        f = float(v)
    except Exception:
        return None
    if math.isfinite(f):
        return f
    return None


def _dist_atr(level: float, price: float, atr: float) -> float:
    try:
        a = float(atr)
        if not math.isfinite(a) or a <= 0:
            return 999.0
        return float(abs(float(level) - float(price)) / a)
    except Exception:
        return 999.0


def _build_why_canonical(
    *,
    scenario_id: str,
    direction: str,
    htf: dict[str, Any],
    structure_5m: dict[str, Any],
    facts: dict[str, Any],
    targets: list[dict[str, Any]],
    poi: list[dict[str, Any]],
    conclusion_hint: str | None,
) -> list[str]:
    """Канонічний why[] (3–7 рядків) у стабільному порядку."""

    out: list[str] = []

    # 1) HTF рамка
    drh = htf.get("dr_high")
    drl = htf.get("dr_low")
    pd = htf.get("pd")
    atr = htf.get("atr14")
    bias = htf.get("bias")
    bias_src = htf.get("bias_src")
    dr_tf = htf.get("dr_tf")
    atr_tf = htf.get("atr_tf")
    out.append(
        "HTF: "
        f"DR({dr_tf})[{_fmt_num(drl)}..{_fmt_num(drh)}] "
        f"PD={pd} "
        f"ATR14({atr_tf})={_fmt_num(atr)} "
        f"bias={bias}({bias_src})"
    )

    # 2) Ключова подія: sweep
    sweep = facts.get("sweep") if isinstance(facts, dict) else None
    if isinstance(sweep, dict):
        out.append(
            "Ключова подія: "
            f"sweep {sweep.get('side')} {sweep.get('pool_type')}@{_fmt_num(sweep.get('level'))} "
            f"age={sweep.get('age_bars')}"
        )
    else:
        out.append("Ключова подія: sweep — немає")

    # 3) Перемикач: hold або failed_hold
    hold = facts.get("hold") if isinstance(facts, dict) else None
    failed = facts.get("failed_hold") if isinstance(facts, dict) else None
    if isinstance(hold, dict) and bool(hold.get("ok")):
        out.append(
            f"Перемикач: hold_above(level={_fmt_num(hold.get('level_up'))}, k={hold.get('k')})"
        )
    elif isinstance(failed, dict) and bool(failed.get("ok")):
        out.append(
            f"Перемикач: failed_hold_after_sweep(level={_fmt_num(failed.get('level_up'))})"
        )
    else:
        out.append("Перемикач: —")

    # 4) 5m структура
    last_ev = structure_5m.get("last_event") if isinstance(structure_5m, dict) else None
    ev_after = (
        structure_5m.get("events_after_sweep")
        if isinstance(structure_5m, dict)
        else None
    )
    bias_5m = structure_5m.get("bias_5m") if isinstance(structure_5m, dict) else None
    ev_truth = None
    if isinstance(ev_after, dict):
        ev_truth = ev_after.get("truth")
    last_kind = last_ev.get("kind") if isinstance(last_ev, dict) else None
    out.append(f"Структура 5m: bias={bias_5m} last={last_kind} after_sweep={ev_truth}")

    # 5) Targets
    if isinstance(targets, list) and targets:
        parts: list[str] = []
        for t in targets[:3]:
            parts.append(
                f"{t.get('type')}@{_fmt_num(t.get('level'))} d_atr={_fmt_num(t.get('dist_atr'))}"
            )
        out.append("Targets: " + ", ".join(parts))
    else:
        out.append("Targets: —")

    # 6) POI confluence (опційно)
    poi_short = [
        p for p in (poi or []) if isinstance(p, dict) and p.get("side") == "SHORT"
    ]
    poi_long = [
        p for p in (poi or []) if isinstance(p, dict) and p.get("side") == "LONG"
    ]
    top_poi = (poi_short + poi_long)[:1]
    if top_poi:
        p0 = top_poi[0]
        out.append(
            "POI: "
            f"{p0.get('side')} {p0.get('type')} score={_fmt_num(p0.get('score'))} "
            f"filled%={_fmt_num(p0.get('filled_pct'))}"
        )

    # 7) Висновок
    concl = conclusion_hint or f"{scenario_id} {direction}"
    out.append(f"Висновок: {concl}")

    # Рівно 3–7 тез (прибираємо зайве, але зберігаємо порядок).
    # Обов'язково: HTF, ключова подія, перемикач, структура, висновок.
    # POI може бути відсутній.
    if len(out) > 7:
        # якщо POI додався як 6-й і перелімітило — прибираємо POI.
        out = [x for x in out if not x.startswith("POI:")]
    return out[:7]


def _fmt_num(v: Any) -> str:
    f = _safe_float(v)
    if f is None:
        return "-"
    try:
        return f"{float(f):.5g}"
    except Exception:
        return "-"


def _near_poi(
    *,
    zones: SmcZonesState | None,
    side: Literal["LONG", "SHORT"],
    price: float,
    structure: SmcStructureState | None,
    in_premium: bool,
) -> bool:
    if zones is None:
        return False
    candidates = list(zones.poi_zones or [])
    if not candidates:
        candidates = list(zones.active_zones or [])

    atr = _atr_like(structure)
    dist_thr = None
    if atr is not None:
        dist_thr = float(atr) * 2.2
    else:
        dist_thr = abs(float(price)) * 0.003  # 0.3% fallback

    for z in candidates[:12]:
        if getattr(z, "direction", None) not in (side, "BOTH"):
            continue
        try:
            zmin = float(z.price_min)
            zmax = float(z.price_max)
        except Exception:
            continue
        center = (zmin + zmax) / 2.0
        if not math.isfinite(center):
            continue
        # Фільтр «premium/discount» як простий модифікатор.
        if side == "SHORT" and not in_premium:
            continue
        if side == "LONG" and not in_premium:
            continue
        if abs(center - float(price)) <= float(dist_thr):
            return True

    return False


def _targets_near(
    *,
    pools: list[SmcLiquidityPool],
    side: Literal["UP", "DOWN"],
    price: float,
    structure: SmcStructureState | None,
) -> bool:
    if not pools:
        return False

    atr = _atr_like(structure)
    thr = float(atr) * 3.0 if atr is not None else abs(float(price)) * 0.004

    for p in pools[:24]:
        try:
            level = float(p.level)
        except Exception:
            continue
        if not math.isfinite(level) or level <= 0:
            continue
        if side == "DOWN" and level < price and (price - level) <= thr:
            return True
        if side == "UP" and level > price and (level - price) <= thr:
            return True
    return False


def _top_reasons(contrib: list[tuple[float, str]], *, limit: int) -> list[str]:
    if not contrib:
        return []
    items = sorted(contrib, key=lambda x: float(x[0]), reverse=True)
    out: list[str] = []
    for w, label in items[:limit]:
        out.append(f"{label} (+{w:.2g})")
    return out


def _confidence_from_scores(*, winner: float, loser: float) -> float:
    # Простий sigmoid по різниці скорів. 0.5..0.95.
    diff = float(winner) - float(loser)
    x = diff / 2.0
    try:
        sig = 1.0 / (1.0 + math.exp(-x))
    except Exception:
        sig = 0.5
    conf = 0.5 + sig * 0.45
    return float(min(0.95, max(0.5, conf)))


def _near_htf_dr_high(*, price: float, dr_high: float, dr_low: float) -> bool:
    rng = float(dr_high) - float(dr_low)
    if not math.isfinite(rng) or rng <= 0:
        return False
    dr_mid = (float(dr_high) + float(dr_low)) / 2.0
    if float(price) < float(dr_mid):
        return False
    # "Поруч" = в верхній частині діапазону (детерміновано, без підбору постфактум).
    return (float(dr_high) - float(price)) <= float(rng) * 0.35


def _near_htf_dr_low(*, price: float, dr_high: float, dr_low: float) -> bool:
    rng = float(dr_high) - float(dr_low)
    if not math.isfinite(rng) or rng <= 0:
        return False
    dr_mid = (float(dr_high) + float(dr_low)) / 2.0
    if float(price) > float(dr_mid):
        return False
    return (float(price) - float(dr_low)) <= float(rng) * 0.35


def _pick_hold_level_up(
    *,
    last_price: float,
    range_high: float,
    htf_dr_high: float,
    htf_dr_low: float,
    pools: list[SmcLiquidityPool],
) -> tuple[float, str]:
    # P0: інвалідаційний рівень = max(5m range_high, HTF DR high).
    # Примітка: sweep/failed_hold рахуємо окремо (на 5m рівні), див. decide_42_43.
    _ = (last_price, htf_dr_low, pools)
    if math.isfinite(htf_dr_high) and math.isfinite(range_high):
        if float(htf_dr_high) >= float(range_high):
            return float(htf_dr_high), "htf_dr_high"
        return float(range_high), "range_high"
    if math.isfinite(htf_dr_high):
        return float(htf_dr_high), "htf_dr_high"
    return float(range_high), "range_high"


def _pick_hold_level_dn(
    *,
    last_price: float,
    range_low: float,
    htf_dr_high: float,
    htf_dr_low: float,
    pools: list[SmcLiquidityPool],
) -> tuple[float, str]:
    # P0: інвалідаційний рівень вниз = min(5m range_low, HTF DR low).
    _ = (last_price, htf_dr_high, pools)
    if math.isfinite(htf_dr_low) and math.isfinite(range_low):
        if float(htf_dr_low) <= float(range_low):
            return float(htf_dr_low), "htf_dr_low"
        return float(range_low), "range_low"
    if math.isfinite(htf_dr_low):
        return float(htf_dr_low), "htf_dr_low"
    return float(range_low), "range_low"


def _hold_above(
    *, primary_frame: pd.DataFrame, level: float, k: int, eps: float
) -> bool:
    if primary_frame is None or primary_frame.empty:
        return False
    if "close" not in primary_frame.columns:
        return False
    closes = pd.to_numeric(primary_frame["close"], errors="coerce").dropna()
    kk = max(1, int(k))
    if len(closes) < kk:
        return False
    thr = float(level) + max(0.0, float(eps))
    try:
        return bool((closes.tail(kk) > thr).all())
    except Exception:
        return False


def _failed_hold_up(
    *,
    primary_frame: pd.DataFrame,
    level: float,
    swept_level: float | None,
    hold_above: bool,
    eps: float,
) -> bool:
    # Важливо: failed_hold вимагає sweep, щоб не перетворитись на "будь-який відкат".
    if swept_level is None or not math.isfinite(float(swept_level)):
        return False
    if (
        primary_frame is None
        or primary_frame.empty
        or "close" not in primary_frame.columns
    ):
        return False
    closes = pd.to_numeric(primary_frame["close"], errors="coerce").dropna()
    if closes.empty:
        return False

    last_close = float(closes.iloc[-1])
    if not math.isfinite(last_close):
        return False

    thr_dn = float(level) - max(0.0, float(eps))
    close_back_below = last_close < thr_dn
    return bool(close_back_below and (not bool(hold_above)))
