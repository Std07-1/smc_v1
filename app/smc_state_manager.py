"""In-memory менеджер стану для чистого SMC пайплайна.

Цей модуль тримає агрегований стан активів для UI (через Redis publisher) і
спеціальні runtime-стейти, які **не належать SMC-core**.

Поточний фокус:
- Stage6 (4_2 vs 4_3): анти-фліп/гістерезис для «сирого» сценарію з core.

Ключові ідеї Stage6 тут:
- SMC-core повертає raw сценарій детерміновано (може бути `UNCLEAR`).
- Тут ми формуємо stable-стан (TTL + confirm + switch_delta) і віддаємо в `asset.stats`:
    - stable/raw/pending (для прозорості в UI),
    - `scenario_unclear_reason` / `scenario_raw_unclear_reason` (довіра/діагностика).
"""

from __future__ import annotations

import time
from typing import Any

from config.constants import ASSET_STATE, K_STATS, K_SYMBOL
from core.serialization import iso_z_to_dt, safe_float, utc_now_iso_z


class SmcStateManager:
    """Мінімальний менеджер стану без Stage1 спадщини."""

    def __init__(
        self,
        initial_assets: list[str] | None = None,
        *,
        cache_handler: Any | None = None,
    ) -> None:
        self.state: dict[str, dict[str, Any]] = {}
        self.cache = cache_handler
        # Stage6: анти-фліп/гістерезис сценарію 4.2 vs 4.3.
        # Живе тут (поза SMC-core), щоб core залишався чистим/детермінованим.
        self._stage6: dict[str, dict[str, Any]] = {}
        for symbol in initial_assets or []:
            self.init_asset(symbol)

    def set_cache_handler(self, cache_handler: Any | None) -> None:
        """Призначаємо хендлер кешу/сховища (best-effort)."""

        self.cache = cache_handler

    def init_asset(self, symbol: str) -> None:
        """Створюємо дефолтну структуру активу."""

        sym = str(symbol).lower()
        self.state[sym] = {
            K_SYMBOL: sym,
            "state": ASSET_STATE["INIT"],
            "signal": "SMC_NONE",
            "smc_hint": None,
            "hints": ["Очікування SMC даних..."],
            K_STATS: {},
            "last_updated": utc_now_iso_z(),
        }
        self._stage6.setdefault(sym, self._stage6_default_state())

    @staticmethod
    def _stage6_default_state() -> dict[str, Any]:
        return {
            "stable_id": "UNCLEAR",
            "stable_direction": "NEUTRAL",
            "stable_confidence": 0.0,
            "stable_why": [],
            "stable_key_levels": {},
            "last_change_ts": None,
            "last_switch_unix": 0.0,
            "pending_id": None,
            "pending_count": 0,
            "unclear_streak": 0,
            "raw_id": None,
            "raw_confidence": None,
            "raw_direction": None,
            "raw_why": None,
            "raw_key_levels": None,
            "raw_inputs_ok": None,
            "raw_gates": None,
            "raw_unclear_reason": None,
            "raw_telemetry": None,
            # Anti-flip пояснення для UI (TTL/confirm/delta/P1 блоки).
            "anti_flip": {},
            "last_eval": {},
            # Micro-events confirm-only (Stage5 execution)
            "micro_ok": False,
            "micro_within_ttl": False,
            "micro_ref": None,
            "micro_distance_atr": None,
            "micro_age_s": None,
            "micro_events": [],
            "micro_boost": 0.0,
            "raw_confidence_base": None,
        }

    def apply_stage6_hysteresis(
        self,
        symbol: str,
        plain_hint: dict[str, Any] | None,
        *,
        ttl_sec: int = 180,
        confirm_bars: int = 2,
        switch_delta: float = 0.08,
        decay_to_unclear_after: int = 6,
        strong_conf: float = 0.86,
        strong_score_diff: float = 1.4,
        mixed_bias_switch_delta_mult: float = 0.65,
        micro_confirm_enabled: bool = True,
        micro_ttl_sec: int = 90,
        micro_dmax_atr: float = 0.80,
        micro_boost: float = 0.05,
        micro_boost_partial: float = 0.02,
        now_unix: float | None = None,
    ) -> dict[str, Any]:
        """Застосовує Stage6 анти-фліп до сирого `smc_hint.signals[]`.

        Повертає dict для мерджу у `asset.stats`.

                Правила (коротко):
                - stable-сценарій не фліпає без підтвердження (confirm_bars) і запасу (switch_delta).
                - switch обмежено TTL (не частіше ніж раз на ttl_sec).
                - `UNCLEAR` зберігається як raw (і не перезаписує stable одразу), але:
                    - після `decay_to_unclear_after` послідовних raw `UNCLEAR` ми робимо
                        контрольований decay stable → `UNCLEAR` (щоб уникати «липкості»).
                - Для `htf_bias=MIXED` поріг switch_delta може бути зменшений
                    (`mixed_bias_switch_delta_mult`), щоб stable не «залипав».
                - Strong override: за високої впевненості/великої різниці score дозволяємо
                    bypass TTL (швидка реакція на сильні факти).

                                P1 (асиметрія):
                                - 4_2 → 4_3 може пробивати TTL через hard_invalidation (напр. `hold_above_up`).
                                - 4_3 → 4_2 жорсткіше: для switch потрібен явний `failed_hold_up`; інакше
                                    ми або блокуємо pending, або швидко деградуємо у `UNCLEAR` при структурній
                                    інвалідації (BOS_DOWN після sweep).
        """

        sym = str(symbol).lower()
        if sym not in self.state:
            self.init_asset(sym)
        st = self._stage6.get(sym)
        if not isinstance(st, dict):
            st = self._stage6_default_state()
            self._stage6[sym] = st

        now = float(now_unix) if now_unix is not None else time.time()
        raw = _extract_stage6_signal_from_plain_hint(plain_hint)

        raw_id = str((raw or {}).get("scenario_id") or "UNCLEAR")
        raw_direction = str((raw or {}).get("direction") or "NEUTRAL")
        raw_conf = _to_float((raw or {}).get("confidence"), default=None)
        raw_conf_base = raw_conf
        raw_why = (raw or {}).get("why")
        raw_key_levels = (raw or {}).get("key_levels")
        raw_inputs_ok = (raw or {}).get("inputs_ok")
        raw_gates = (raw or {}).get("gates")
        raw_unclear_reason = (raw or {}).get("unclear_reason")
        raw_telemetry = (raw or {}).get("telemetry")

        raw_why_list: list[str] = (
            [str(v) for v in raw_why] if isinstance(raw_why, list) else []
        )

        # Stage5 micro-events: підтвердження лише у in_play + distance<=dmax + TTL.
        micro = {
            "ok": False,
            "within_ttl": False,
            "ref": None,
            "distance_atr": None,
            "age_s": None,
            "events": [],
            "boost": 0.0,
        }
        if micro_confirm_enabled and raw_id in {"4_2", "4_3"}:
            try:
                micro = _stage6_micro_confirm_from_execution(
                    plain_hint=plain_hint,
                    raw_id=raw_id,
                    now_unix=now,
                    ttl_sec=int(micro_ttl_sec),
                    dmax_atr=float(micro_dmax_atr),
                    boost_full=float(micro_boost),
                    boost_partial=float(micro_boost_partial),
                )
            except Exception:
                micro = micro

        if raw_conf is not None and float(micro.get("boost") or 0.0) > 0:
            raw_conf = min(0.95, float(raw_conf) + float(micro.get("boost") or 0.0))
            try:
                ref = micro.get("ref")
                d_atr = micro.get("distance_atr")
                age_s = micro.get("age_s")
                ev = micro.get("events") or []
                raw_why_list.append(
                    "micro: підтвердження execution "
                    f"({'+'.join([str(x) for x in ev][:3])}) "
                    f"ref={ref} d_atr={_fmt_float(d_atr)} age_s={_fmt_float(age_s)}"
                )
            except Exception:
                raw_why_list.append("micro: підтвердження execution")

        st["raw_id"] = raw_id
        st["raw_direction"] = raw_direction
        st["raw_confidence"] = raw_conf
        st["raw_confidence_base"] = raw_conf_base
        st["raw_why"] = raw_why_list
        st["raw_key_levels"] = (
            raw_key_levels if isinstance(raw_key_levels, dict) else {}
        )
        st["raw_inputs_ok"] = bool(raw_inputs_ok) if raw_inputs_ok is not None else None
        st["raw_gates"] = raw_gates if isinstance(raw_gates, list) else []
        st["raw_unclear_reason"] = (
            str(raw_unclear_reason) if isinstance(raw_unclear_reason, str) else None
        )
        st["raw_telemetry"] = raw_telemetry if isinstance(raw_telemetry, dict) else {}

        st["micro_ok"] = bool(micro.get("ok"))
        st["micro_within_ttl"] = bool(micro.get("within_ttl"))
        st["micro_ref"] = (
            micro.get("ref") if isinstance(micro.get("ref"), str) else None
        )
        st["micro_distance_atr"] = _to_float(micro.get("distance_atr"), default=None)
        st["micro_age_s"] = _to_float(micro.get("age_s"), default=None)
        st["micro_events"] = (
            micro.get("events") if isinstance(micro.get("events"), list) else []
        )
        st["micro_boost"] = _to_float(micro.get("boost"), default=0.0) or 0.0

        stable_id = str(st.get("stable_id") or "UNCLEAR")
        stable_conf = _to_float(st.get("stable_confidence"), default=0.0) or 0.0

        # За замовчуванням: чистимо last_eval; заповнимо нижче перед return.
        st["last_eval"] = {}

        rt_any = st.get("raw_telemetry")
        rt: dict[str, Any] = rt_any if isinstance(rt_any, dict) else {}

        # P1: детерміновані прапорці з raw телеметрії (збираються в SMC-core).
        hold_above_up = bool(rt.get("hold_above_up"))
        failed_hold_up = bool(rt.get("failed_hold_up"))

        events_after_any = rt.get("events_after_sweep")
        events_after: dict[str, Any] = (
            events_after_any if isinstance(events_after_any, dict) else {}
        )
        bos_down_after_sweep = bool(events_after.get("bos_down"))

        # Strong micro-confirm (Stage5 execution): тільки як додатковий тригер hard_invalidation.
        micro_events_any = st.get("micro_events")
        micro_events: list[str] = (
            [str(x) for x in micro_events_any]
            if isinstance(micro_events_any, list)
            else []
        )
        micro_strong = (
            bool(st.get("micro_ok"))
            and bool(st.get("micro_within_ttl"))
            and ("MICRO" in micro_events)
            and ("RETEST_OK" in micro_events)
        )

        # P1: hard invalidation може override TTL/confirm/delta.
        hard_invalidation_target: str | None = None
        hard_invalidation_reason: str | None = None
        if stable_id == "4_2" and raw_id == "4_3":
            if hold_above_up:
                hard_invalidation_target = "4_3"
                hard_invalidation_reason = "hold_above_up"
            elif micro_strong:
                hard_invalidation_target = "4_3"
                hard_invalidation_reason = "micro_confirm"

        # 4_3 → 4_2 без failed_hold не вважаємо валідним switch: або блокуємо,
        # або інвалідуємо у UNCLEAR при явному BOS_DOWN після sweep.
        if stable_id == "4_3" and raw_id == "4_2" and (not failed_hold_up):
            if bos_down_after_sweep:
                hard_invalidation_target = "UNCLEAR"
                hard_invalidation_reason = "bos_down_no_failed_hold"

        # Адаптивний поріг для MIXED bias (менший delta, щоб не «липнути»).
        effective_switch_delta = float(switch_delta)
        try:
            htf_bias = (st.get("raw_telemetry") or {}).get("htf_bias")
            if isinstance(htf_bias, str) and htf_bias.upper() == "MIXED":
                effective_switch_delta = float(switch_delta) * float(
                    mixed_bias_switch_delta_mult
                )
        except Exception:
            effective_switch_delta = float(switch_delta)

        # Якщо raw == stable — оновлюємо stable-пояснення (без switch).
        if raw_id == stable_id and raw_id != "UNCLEAR":
            st["stable_direction"] = raw_direction
            if raw_conf is not None:
                st["stable_confidence"] = float(raw_conf)
            st["stable_why"] = st["raw_why"]
            st["stable_key_levels"] = st["raw_key_levels"]
            st["pending_id"] = None
            st["pending_count"] = 0
            st["unclear_streak"] = 0
            st["anti_flip"] = {
                "state": "SYNC",
                "reason": "raw==stable",
                "blocked": [],
            }
            return _stage6_stats_payload(
                st,
                ttl_sec=ttl_sec,
                confirm_bars=confirm_bars,
                switch_delta=effective_switch_delta,
                flip_event=None,
            )

        # P1: hard invalidation (override TTL) — виконуємо одразу.
        if (
            hard_invalidation_target is not None
            and hard_invalidation_reason is not None
        ):
            prev = stable_id
            target = str(hard_invalidation_target)
            st["stable_id"] = target
            if target == "UNCLEAR":
                st["stable_direction"] = "NEUTRAL"
                st["stable_confidence"] = 0.0
            else:
                st["stable_direction"] = raw_direction
                st["stable_confidence"] = (
                    float(raw_conf) if raw_conf is not None else 0.0
                )
            st["stable_why"] = st.get("raw_why") or []
            st["stable_key_levels"] = st.get("raw_key_levels") or {}
            st["last_change_ts"] = utc_now_iso_z()
            st["last_switch_unix"] = now
            st["pending_id"] = None
            st["pending_count"] = 0
            st["unclear_streak"] = 0
            st["anti_flip"] = {
                "state": "SWITCH",
                "reason": f"hard_invalidation:{hard_invalidation_reason}",
                "blocked": [],
            }
            flip_event = {
                "from": prev,
                "to": target,
                "ts": st.get("last_change_ts"),
                "reason": f"hard_invalidation:{hard_invalidation_reason}",
            }
            return _stage6_stats_payload(
                st,
                ttl_sec=ttl_sec,
                confirm_bars=confirm_bars,
                switch_delta=effective_switch_delta,
                flip_event=flip_event,
            )

        # UNCLEAR — не затирає stable одразу, але має decay у "нейтраль" для довіри.
        if raw_id == "UNCLEAR":
            st["pending_id"] = None
            st["pending_count"] = 0
            st["unclear_streak"] = int(st.get("unclear_streak") or 0) + 1

            decay_n = max(1, int(decay_to_unclear_after))
            if stable_id != "UNCLEAR" and int(st.get("unclear_streak") or 0) >= decay_n:
                prev = stable_id
                st["stable_id"] = "UNCLEAR"
                st["stable_direction"] = "NEUTRAL"
                st["stable_confidence"] = 0.0
                # Для UI краще показати пояснення останнього UNCLEAR (top-3).
                raw_why_any = st.get("raw_why")
                raw_why_any_list: list[Any] = (
                    raw_why_any if isinstance(raw_why_any, list) else []
                )
                st["stable_why"] = [str(v) for v in raw_why_any_list][:5]
                st["stable_key_levels"] = st.get("raw_key_levels") or {}
                st["last_change_ts"] = utc_now_iso_z()
                st["last_switch_unix"] = now
                st["unclear_streak"] = 0
                st["anti_flip"] = {
                    "state": "SWITCH",
                    "reason": "decay_unclear",
                    "blocked": [],
                }
                flip_event = {
                    "from": prev,
                    "to": "UNCLEAR",
                    "ts": st.get("last_change_ts"),
                    "reason": "decay_unclear",
                }
                return _stage6_stats_payload(
                    st,
                    ttl_sec=ttl_sec,
                    confirm_bars=confirm_bars,
                    switch_delta=effective_switch_delta,
                    flip_event=flip_event,
                )

            st["anti_flip"] = {
                "state": "HOLD",
                "reason": "raw_unclear",
                "blocked": ["raw_unclear"],
                "unclear_streak": int(st.get("unclear_streak") or 0),
                "decay_to_unclear_after": int(max(1, int(decay_to_unclear_after))),
            }
            return _stage6_stats_payload(
                st,
                ttl_sec=ttl_sec,
                confirm_bars=confirm_bars,
                switch_delta=effective_switch_delta,
                flip_event=None,
            )

        st["unclear_streak"] = 0

        # P1: 4_3 → 4_2 без failed_hold_up не розглядаємо як кандидата на switch.
        # (hard_invalidation до UNCLEAR при BOS_DOWN оброблений вище).
        if stable_id == "4_3" and raw_id == "4_2" and (not failed_hold_up):
            st["pending_id"] = None
            st["pending_count"] = 0
            st["anti_flip"] = {
                "state": "BLOCKED",
                "reason": "p1_no_failed_hold",
                "blocked": ["p1_no_failed_hold"],
                "p1": {
                    "failed_hold_up": bool(failed_hold_up),
                    "bos_down_after_sweep": bool(bos_down_after_sweep),
                },
            }
            return _stage6_stats_payload(
                st,
                ttl_sec=ttl_sec,
                confirm_bars=confirm_bars,
                switch_delta=effective_switch_delta,
                flip_event=None,
            )

        # Кандидат на switch.
        pending_id = st.get("pending_id")
        if pending_id != raw_id:
            st["pending_id"] = raw_id
            st["pending_count"] = 1
        else:
            st["pending_count"] = int(st.get("pending_count") or 0) + 1

        # TTL
        last_switch_unix = _to_float(st.get("last_switch_unix"), default=0.0) or 0.0
        ttl_ok = (now - last_switch_unix) >= float(max(0, int(ttl_sec)))
        ttl_left_sec = max(
            0.0, float(max(0, int(ttl_sec))) - float(now - last_switch_unix)
        )

        # Поріг підтвердження
        required_confirm = 1 if stable_id == "UNCLEAR" else max(1, int(confirm_bars))
        confirm_ok = int(st.get("pending_count") or 0) >= required_confirm

        # Поріг запасу
        if stable_id == "UNCLEAR":
            delta_ok = True
            required_confidence = None
        else:
            if raw_conf is None:
                delta_ok = False
                required_confidence = float(stable_conf + float(effective_switch_delta))
            else:
                delta_ok = float(raw_conf) >= float(
                    stable_conf + float(effective_switch_delta)
                )
                required_confidence = float(stable_conf + float(effective_switch_delta))

        # Strong override: може пробити TTL, або швидко деградувати у UNCLEAR при структурному зламі.
        strong_override_ok = False
        try:
            score_any = rt.get("score")
            score_block: dict[str, Any] = (
                score_any if isinstance(score_any, dict) else {}
            )
            s42 = _to_float(score_block.get("4_2"), default=None)
            s43 = _to_float(score_block.get("4_3"), default=None)
            score_diff = None
            if s42 is not None and s43 is not None:
                score_diff = abs(float(s42) - float(s43))

            if raw_conf is not None and score_diff is not None:
                strong_override_ok = (float(raw_conf) >= float(strong_conf)) and (
                    float(score_diff) >= float(strong_score_diff)
                )

            # Акуратний override «у нейтраль»: якщо stable=4_3, але з'явився BOS_DOWN після sweep,
            # або stable=4_2, але з'явився Break&Hold UP — краще швидко стати UNCLEAR.
            break_hold_any = rt.get("break_hold_up")
            break_hold: dict[str, Any] = (
                break_hold_any if isinstance(break_hold_any, dict) else {}
            )
            if (
                stable_id == "4_3"
                and raw_id == "4_2"
                and bool(events_after.get("bos_down"))
            ):
                strong_override_ok = True
            if stable_id == "4_2" and raw_id == "4_3" and bool(break_hold.get("ok")):
                strong_override_ok = True
        except Exception:
            strong_override_ok = False

        flip_event: dict[str, Any] | None = None
        can_switch = (ttl_ok or strong_override_ok) and confirm_ok and delta_ok
        blocked: list[str] = []
        if not ttl_ok and not strong_override_ok:
            blocked.append("ttl")
        if not confirm_ok:
            blocked.append("confirm")
        if not delta_ok:
            blocked.append("delta")

        st["last_eval"] = {
            "ttl_ok": bool(ttl_ok),
            "ttl_left_sec": float(ttl_left_sec),
            "confirm_ok": bool(confirm_ok),
            "confirm_required": int(required_confirm),
            "pending_id": st.get("pending_id"),
            "pending_count": int(st.get("pending_count") or 0),
            "delta_ok": bool(delta_ok),
            "required_confidence": required_confidence,
            "stable_confidence": float(stable_conf),
            "raw_confidence": raw_conf,
            "strong_override_ok": bool(strong_override_ok),
            "blocked": blocked,
        }

        st["anti_flip"] = {
            "state": "SWITCH" if can_switch else "HOLD",
            "reason": "confirm" if can_switch else "blocked",
            "blocked": blocked,
            "ttl_left_sec": float(ttl_left_sec),
            "confirm_required": int(required_confirm),
            "required_confidence": required_confidence,
            "strong_override_ok": bool(strong_override_ok),
        }

        if can_switch:
            prev = stable_id
            st["stable_id"] = raw_id
            st["stable_direction"] = raw_direction
            st["stable_confidence"] = float(raw_conf) if raw_conf is not None else 0.0
            st["stable_why"] = st["raw_why"]
            st["stable_key_levels"] = st["raw_key_levels"]
            st["last_change_ts"] = utc_now_iso_z()
            st["last_switch_unix"] = now
            st["pending_id"] = None
            st["pending_count"] = 0
            flip_event = {
                "from": prev,
                "to": raw_id,
                "ts": st.get("last_change_ts"),
                "reason": (
                    "strong_override"
                    if (not ttl_ok and strong_override_ok)
                    else "confirm"
                ),
            }

        return _stage6_stats_payload(
            st,
            ttl_sec=ttl_sec,
            confirm_bars=confirm_bars,
            switch_delta=effective_switch_delta,
            flip_event=flip_event,
        )

    def update_asset(self, symbol: str, updates: dict[str, Any]) -> None:
        """Мерджимо оновлення з новими полями (без тригерів)."""

        sym = str(symbol).lower()
        if sym not in self.state:
            self.init_asset(sym)

        current = self.state[sym]
        merged = {**current, **updates}

        stats_current = current.get(K_STATS)
        stats_updates = updates.get(K_STATS)
        if isinstance(stats_current, dict) or isinstance(stats_updates, dict):
            merged[K_STATS] = {
                **(stats_current or {}),
                **(stats_updates or {}),
            }

        if "hints" in merged and not isinstance(merged.get("hints"), list):
            merged["hints"] = [str(merged["hints"])]

        merged[K_SYMBOL] = sym
        merged["last_updated"] = utc_now_iso_z()
        self.state[sym] = merged

    def get_all_assets(self) -> list[dict[str, Any]]:
        """Повертаємо копії станів для UI."""

        return [dict(asset) for asset in self.state.values()]


def _to_float(value: Any, *, default: float | None) -> float | None:
    try:
        v = float(value)
    except Exception:
        return default
    if math_isfinite(v):
        return v
    return default


def math_isfinite(value: float) -> bool:
    try:
        return value == value and value not in (float("inf"), float("-inf"))
    except Exception:
        return False


def _extract_stage6_signal_from_plain_hint(
    plain_hint: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(plain_hint, dict):
        return None
    signals = plain_hint.get("signals")
    if not isinstance(signals, list) or not signals:
        return None
    for s in signals:
        if not isinstance(s, dict):
            continue
        if str(s.get("type") or "").upper() != "SCENARIO":
            continue
        meta_any = s.get("meta")
        meta: dict[str, Any] = meta_any if isinstance(meta_any, dict) else {}
        telemetry_any = meta.get("telemetry")
        telemetry: dict[str, Any] = (
            telemetry_any if isinstance(telemetry_any, dict) else {}
        )
        return {
            "scenario_id": meta.get("scenario_id") or "UNCLEAR",
            "direction": s.get("direction") or "NEUTRAL",
            "confidence": s.get("confidence"),
            "why": meta.get("why") if isinstance(meta.get("why"), list) else [],
            "key_levels": (
                meta.get("key_levels")
                if isinstance(meta.get("key_levels"), dict)
                else {}
            ),
            "inputs_ok": telemetry.get("inputs_ok"),
            "gates": (
                telemetry.get("gates")
                if isinstance(telemetry.get("gates"), list)
                else []
            ),
            "unclear_reason": telemetry.get("unclear_reason"),
            "telemetry": telemetry,
        }
    return None


def _stage6_stats_payload(
    st: dict[str, Any],
    *,
    ttl_sec: int,
    confirm_bars: int,
    switch_delta: float,
    flip_event: dict[str, Any] | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "scenario_id": st.get("stable_id") or "UNCLEAR",
        "scenario_direction": st.get("stable_direction") or "NEUTRAL",
        "scenario_confidence": st.get("stable_confidence") or 0.0,
        "scenario_why": st.get("stable_why") or [],
        "scenario_key_levels": st.get("stable_key_levels") or {},
        "scenario_last_change_ts": st.get("last_change_ts"),
        "scenario_unclear_reason": (
            st.get("raw_unclear_reason")
            if str(st.get("stable_id") or "").upper() == "UNCLEAR"
            else None
        ),
        "scenario_state_ttl_sec": int(ttl_sec),
        "scenario_confirm_bars": int(confirm_bars),
        "scenario_switch_delta": float(switch_delta),
        "scenario_pending_id": st.get("pending_id"),
        "scenario_pending_count": int(st.get("pending_count") or 0),
        "scenario_raw_id": st.get("raw_id"),
        "scenario_raw_direction": st.get("raw_direction"),
        "scenario_raw_confidence": st.get("raw_confidence"),
        "scenario_raw_confidence_base": st.get("raw_confidence_base"),
        "scenario_raw_why": st.get("raw_why") or [],
        "scenario_raw_key_levels": st.get("raw_key_levels") or {},
        "scenario_raw_inputs_ok": st.get("raw_inputs_ok"),
        "scenario_raw_gates": st.get("raw_gates") or [],
        "scenario_raw_unclear_reason": st.get("raw_unclear_reason"),
        # Micro-events (Stage5 execution): confirm-only
        "scenario_micro_ok": bool(st.get("micro_ok")),
        "scenario_micro_within_ttl": bool(st.get("micro_within_ttl")),
        "scenario_micro_ref": st.get("micro_ref"),
        "scenario_micro_distance_atr": st.get("micro_distance_atr"),
        "scenario_micro_age_s": st.get("micro_age_s"),
        "scenario_micro_events": st.get("micro_events") or [],
        "scenario_micro_boost": st.get("micro_boost") or 0.0,
        # Anti-flip прозорість для UI (non-breaking extension).
        "scenario_anti_flip": st.get("anti_flip") or {},
        "scenario_last_eval": st.get("last_eval") or {},
    }
    if flip_event is not None:
        payload["scenario_flip"] = flip_event
    return payload


def _fmt_float(value: Any) -> str:
    v = safe_float(value)
    if v is None:
        return "-"
    try:
        return f"{float(v):.3f}"
    except Exception:
        return "-"


def _event_time_to_unix(value: Any) -> float | None:
    if value is None:
        return None
    # serializers.py для pd.Timestamp робить isoformat() => рядок.
    if isinstance(value, str):
        try:
            return float(iso_z_to_dt(value).timestamp())
        except Exception:
            return None
    try:
        # best-effort: якщо це число (sec/ms)
        v = float(value)
        if v > 1e12:
            return v / 1000.0
        return v
    except Exception:
        return None


def _stage6_micro_confirm_from_execution(
    *,
    plain_hint: dict[str, Any] | None,
    raw_id: str,
    now_unix: float,
    ttl_sec: int,
    dmax_atr: float,
    boost_full: float,
    boost_partial: float,
) -> dict[str, Any]:
    if not isinstance(plain_hint, dict):
        return {
            "ok": False,
            "within_ttl": False,
            "ref": None,
            "distance_atr": None,
            "age_s": None,
            "events": [],
            "boost": 0.0,
        }

    execution_any = plain_hint.get("execution")
    execution: dict[str, Any] = execution_any if isinstance(execution_any, dict) else {}
    meta_any = execution.get("meta")
    meta: dict[str, Any] = meta_any if isinstance(meta_any, dict) else {}

    if meta.get("in_play") is not True:
        return {
            "ok": False,
            "within_ttl": False,
            "ref": None,
            "distance_atr": None,
            "age_s": None,
            "events": [],
            "boost": 0.0,
        }

    atr_ref = safe_float(meta.get("atr_ref"))
    in_play_ref_any = meta.get("in_play_ref")
    in_play_ref: dict[str, Any] = (
        in_play_ref_any if isinstance(in_play_ref_any, dict) else {}
    )
    ref = str(in_play_ref.get("ref") or "UNKNOWN").upper()

    # Візьмемо ціну з останньої події (як сурогат "поточної" для умов distance_to_poi).
    events_any = execution.get("execution_events")
    events: list[dict[str, Any]] = (
        [e for e in events_any if isinstance(e, dict)]
        if isinstance(events_any, list)
        else []
    )
    if not events:
        return {
            "ok": False,
            "within_ttl": False,
            "ref": ref,
            "distance_atr": None,
            "age_s": None,
            "events": [],
            "boost": 0.0,
        }

    last_price = safe_float(events[-1].get("price"))
    distance = None
    if last_price is not None:
        if ref == "POI":
            poi_min = safe_float(in_play_ref.get("poi_min"))
            poi_max = safe_float(in_play_ref.get("poi_max"))
            if poi_min is not None and poi_max is not None:
                distance = min(
                    abs(float(last_price) - float(poi_min)),
                    abs(float(poi_max) - float(last_price)),
                )
        elif ref == "TARGET":
            lvl = safe_float(in_play_ref.get("level"))
            if lvl is not None:
                distance = abs(float(last_price) - float(lvl))

    distance_atr = None
    if distance is not None and atr_ref is not None and atr_ref > 0:
        distance_atr = float(distance) / float(atr_ref)

    if distance_atr is None or float(distance_atr) > float(dmax_atr):
        return {
            "ok": False,
            "within_ttl": False,
            "ref": ref,
            "distance_atr": distance_atr,
            "age_s": None,
            "events": [],
            "boost": 0.0,
        }

    ttl = max(1, int(ttl_sec))
    recent: list[dict[str, Any]] = []
    ages: list[float] = []
    for e in events:
        t_unix = _event_time_to_unix(e.get("time"))
        if t_unix is None:
            continue
        age_s = float(now_unix) - float(t_unix)
        if age_s < 0:
            continue
        if age_s <= float(ttl):
            recent.append(e)
            ages.append(age_s)

    within_ttl = bool(recent)
    if not within_ttl:
        return {
            "ok": False,
            "within_ttl": False,
            "ref": ref,
            "distance_atr": distance_atr,
            "age_s": None,
            "events": [],
            "boost": 0.0,
        }

    kinds = [str(e.get("event_type") or "").upper() for e in recent]
    # direction не обов'язково потрібен: Stage5 вже in_play, але для safety фільтруємо.
    dirs = [str(e.get("direction") or "").upper() for e in recent]

    def _has(kind: str, direction: str) -> bool:
        return any(
            (k == kind and d == direction) for k, d in zip(kinds, dirs, strict=False)
        )

    # Правила підтвердження (мінімальні, без зміни raw_id):
    # - 4_3: MICRO_CHOCH LONG + RETEST_OK LONG (або частково — одна з них)
    # - 4_2: SWEEP SHORT + RETEST_OK SHORT (або частково — одна з них)
    ok = False
    boost = 0.0
    used: list[str] = []

    if raw_id == "4_3":
        a = _has("MICRO_CHOCH", "LONG") or _has("MICRO_BOS", "LONG")
        b = _has("RETEST_OK", "LONG")
        if a:
            used.append("MICRO")
        if b:
            used.append("RETEST_OK")
        if a and b:
            ok = True
            boost = float(boost_full)
        elif a or b:
            ok = True
            boost = float(boost_partial)
    elif raw_id == "4_2":
        a = _has("SWEEP", "SHORT")
        b = _has("RETEST_OK", "SHORT")
        if a:
            used.append("SWEEP")
        if b:
            used.append("RETEST_OK")
        if a and b:
            ok = True
            boost = float(boost_full)
        elif a or b:
            ok = True
            boost = float(boost_partial)

    min_age = min(ages) if ages else None
    return {
        "ok": bool(ok),
        "within_ttl": True,
        "ref": ref,
        "distance_atr": float(distance_atr) if distance_atr is not None else None,
        "age_s": float(min_age) if min_age is not None else None,
        "events": used,
        "boost": float(boost) if ok else 0.0,
    }
