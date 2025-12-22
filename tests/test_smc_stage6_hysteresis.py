"""Тести Stage6 анти-фліпу (гістерезис/TTL) у SmcStateManager."""

from __future__ import annotations

from datetime import UTC, datetime

from app.smc_state_manager import SmcStateManager


def _hint_signal(*, scenario_id: str, confidence: float) -> dict:
    return {
        "signals": [
            {
                "type": "SCENARIO",
                "direction": (
                    "SHORT"
                    if scenario_id == "4_2"
                    else "LONG" if scenario_id == "4_3" else "NEUTRAL"
                ),
                "confidence": confidence,
                "meta": {
                    "scenario_id": scenario_id,
                    "why": ["x"],
                    "key_levels": {"range_high": 110.0},
                    "telemetry": {"inputs_ok": True, "gates": []},
                },
            }
        ]
    }


def _hint_signal_with_telemetry(
    *,
    scenario_id: str,
    confidence: float,
    telemetry: dict,
) -> dict:
    base = _hint_signal(scenario_id=scenario_id, confidence=confidence)
    base["signals"][0]["meta"]["telemetry"] = dict(telemetry)
    return base


def test_stage6_no_flip_without_confirm_bars() -> None:
    sm = SmcStateManager(["xauusd"])

    # Починаємо зі stable=4_2.
    out1 = sm.apply_stage6_hysteresis(
        "xauusd",
        _hint_signal(scenario_id="4_2", confidence=0.75),
        ttl_sec=0,
        confirm_bars=2,
        switch_delta=0.05,
        now_unix=1000.0,
    )
    assert out1["scenario_id"] == "4_2"

    # Один цикл 4_3 — ще не має фліпнути.
    out2 = sm.apply_stage6_hysteresis(
        "xauusd",
        _hint_signal(scenario_id="4_3", confidence=0.90),
        ttl_sec=0,
        confirm_bars=2,
        switch_delta=0.05,
        now_unix=1001.0,
    )
    assert out2["scenario_id"] == "4_2"
    assert out2["scenario_pending_id"] == "4_3"
    assert out2["scenario_pending_count"] == 1

    # Другий цикл 4_3 — тепер може фліпнути (confirm_bars=2).
    out3 = sm.apply_stage6_hysteresis(
        "xauusd",
        _hint_signal(scenario_id="4_3", confidence=0.90),
        ttl_sec=0,
        confirm_bars=2,
        switch_delta=0.05,
        now_unix=1002.0,
    )
    assert out3["scenario_id"] == "4_3"
    assert isinstance(out3.get("scenario_flip"), dict)


def test_stage6_ttl_blocks_flip_until_expired() -> None:
    sm = SmcStateManager(["xauusd"])

    out1 = sm.apply_stage6_hysteresis(
        "xauusd",
        _hint_signal(scenario_id="4_2", confidence=0.80),
        ttl_sec=10,
        confirm_bars=1,
        switch_delta=0.01,
        now_unix=2000.0,
    )
    assert out1["scenario_id"] == "4_2"

    # Кандидат 4_3 підтверджений, але TTL ще не пройшов.
    out2 = sm.apply_stage6_hysteresis(
        "xauusd",
        _hint_signal(scenario_id="4_3", confidence=0.95),
        ttl_sec=10,
        confirm_bars=1,
        switch_delta=0.01,
        now_unix=2005.0,
    )
    assert out2["scenario_id"] == "4_2"

    # TTL пройшов — тепер switch дозволений.
    out3 = sm.apply_stage6_hysteresis(
        "xauusd",
        _hint_signal(scenario_id="4_3", confidence=0.95),
        ttl_sec=10,
        confirm_bars=1,
        switch_delta=0.01,
        now_unix=2011.0,
    )
    assert out3["scenario_id"] == "4_3"


def test_stage6_unclear_does_not_override_stable() -> None:
    sm = SmcStateManager(["xauusd"])

    out1 = sm.apply_stage6_hysteresis(
        "xauusd",
        _hint_signal(scenario_id="4_2", confidence=0.75),
        ttl_sec=0,
        confirm_bars=1,
        switch_delta=0.01,
        now_unix=3000.0,
    )
    assert out1["scenario_id"] == "4_2"

    out2 = sm.apply_stage6_hysteresis(
        "xauusd",
        _hint_signal(scenario_id="UNCLEAR", confidence=0.0),
        ttl_sec=0,
        confirm_bars=1,
        switch_delta=0.01,
        now_unix=3001.0,
    )
    assert out2["scenario_id"] == "4_2"
    assert out2["scenario_raw_id"] == "UNCLEAR"


def test_stage6_decay_to_unclear_after_n_unclear() -> None:
    sm = SmcStateManager(["xauusd"])

    out1 = sm.apply_stage6_hysteresis(
        "xauusd",
        _hint_signal(scenario_id="4_3", confidence=0.70),
        ttl_sec=0,
        confirm_bars=1,
        switch_delta=0.05,
        now_unix=4000.0,
    )
    assert out1["scenario_id"] == "4_3"

    # 1) ще не decay
    out2 = sm.apply_stage6_hysteresis(
        "xauusd",
        _hint_signal_with_telemetry(
            scenario_id="UNCLEAR",
            confidence=0.0,
            telemetry={"inputs_ok": True, "gates": [], "unclear_reason": "CONFLICT"},
        ),
        ttl_sec=0,
        confirm_bars=1,
        switch_delta=0.05,
        decay_to_unclear_after=3,
        now_unix=4001.0,
    )
    assert out2["scenario_id"] == "4_3"

    # 2) ще не decay
    out3 = sm.apply_stage6_hysteresis(
        "xauusd",
        _hint_signal_with_telemetry(
            scenario_id="UNCLEAR",
            confidence=0.0,
            telemetry={"inputs_ok": True, "gates": [], "unclear_reason": "LOW_SCORE"},
        ),
        ttl_sec=0,
        confirm_bars=1,
        switch_delta=0.05,
        decay_to_unclear_after=3,
        now_unix=4002.0,
    )
    assert out3["scenario_id"] == "4_3"

    # 3) має decay → UNCLEAR
    out4 = sm.apply_stage6_hysteresis(
        "xauusd",
        _hint_signal_with_telemetry(
            scenario_id="UNCLEAR",
            confidence=0.0,
            telemetry={"inputs_ok": True, "gates": [], "unclear_reason": "LOW_SCORE"},
        ),
        ttl_sec=0,
        confirm_bars=1,
        switch_delta=0.05,
        decay_to_unclear_after=3,
        now_unix=4003.0,
    )
    assert out4["scenario_id"] == "UNCLEAR"
    assert isinstance(out4.get("scenario_flip"), dict)


def test_stage6_strong_override_can_bypass_ttl() -> None:
    sm = SmcStateManager(["xauusd"])

    out1 = sm.apply_stage6_hysteresis(
        "xauusd",
        _hint_signal(scenario_id="4_3", confidence=0.60),
        ttl_sec=100,
        confirm_bars=1,
        switch_delta=0.05,
        now_unix=5000.0,
    )
    assert out1["scenario_id"] == "4_3"

    # TTL ще не пройшов, але raw дуже сильний і має великий score-diff.
    out2 = sm.apply_stage6_hysteresis(
        "xauusd",
        _hint_signal_with_telemetry(
            scenario_id="4_2",
            confidence=0.92,
            telemetry={
                "inputs_ok": True,
                "gates": [],
                "score": {"4_2": 6.0, "4_3": 2.0},
                "failed_hold_up": True,
            },
        ),
        ttl_sec=100,
        confirm_bars=1,
        switch_delta=0.05,
        strong_conf=0.86,
        strong_score_diff=1.4,
        now_unix=5001.0,
    )
    assert out2["scenario_id"] == "4_2"
    assert isinstance(out2.get("scenario_flip"), dict)


def test_stage6_hard_invalidation_can_bypass_ttl_42_to_43_hold_above() -> None:
    sm = SmcStateManager(["xauusd"])

    out1 = sm.apply_stage6_hysteresis(
        "xauusd",
        _hint_signal(scenario_id="4_2", confidence=0.85),
        ttl_sec=100,
        confirm_bars=2,
        switch_delta=0.20,
        now_unix=6000.0,
    )
    assert out1["scenario_id"] == "4_2"

    # TTL ще не пройшов, confidence менший за stable+delta, але є hard-факт hold_above_up.
    out2 = sm.apply_stage6_hysteresis(
        "xauusd",
        _hint_signal_with_telemetry(
            scenario_id="4_3",
            confidence=0.55,
            telemetry={
                "inputs_ok": True,
                "gates": [],
                "hold_above_up": True,
            },
        ),
        ttl_sec=100,
        confirm_bars=2,
        switch_delta=0.20,
        now_unix=6001.0,
    )
    assert out2["scenario_id"] == "4_3"
    flip = out2.get("scenario_flip")
    assert isinstance(flip, dict)
    assert str(flip.get("reason") or "").startswith("hard_invalidation:")


def test_stage6_hard_invalidation_43_to_unclear_on_bos_down_no_failed_hold() -> None:
    sm = SmcStateManager(["xauusd"])

    out1 = sm.apply_stage6_hysteresis(
        "xauusd",
        _hint_signal(scenario_id="4_3", confidence=0.70),
        ttl_sec=100,
        confirm_bars=2,
        switch_delta=0.10,
        now_unix=7000.0,
    )
    assert out1["scenario_id"] == "4_3"

    # TTL ще не пройшов, але є BOS_DOWN після sweep і немає failed_hold_up → інвалідуємо у UNCLEAR.
    out2 = sm.apply_stage6_hysteresis(
        "xauusd",
        _hint_signal_with_telemetry(
            scenario_id="4_2",
            confidence=0.95,
            telemetry={
                "inputs_ok": True,
                "gates": [],
                "events_after_sweep": {"bos_down": True},
                "failed_hold_up": False,
            },
        ),
        ttl_sec=100,
        confirm_bars=2,
        switch_delta=0.10,
        now_unix=7001.0,
    )
    assert out2["scenario_id"] == "UNCLEAR"
    flip = out2.get("scenario_flip")
    assert isinstance(flip, dict)
    assert str(flip.get("reason") or "") == "hard_invalidation:bos_down_no_failed_hold"


def test_stage6_micro_confirm_boosts_confidence_only() -> None:
    sm = SmcStateManager(["xauusd"])

    now_unix = 10_000.0
    evt_time = datetime.fromtimestamp(now_unix - 10.0, tz=UTC).isoformat()

    hint = _hint_signal(scenario_id="4_3", confidence=0.70)
    hint["execution"] = {
        "execution_events": [
            {
                "event_type": "MICRO_CHOCH",
                "direction": "LONG",
                "time": evt_time,
                "price": 100.9,
                "level": 100.5,
                "ref": "POI",
                "poi_zone_id": "z_poi",
                "meta": {},
            },
            {
                "event_type": "RETEST_OK",
                "direction": "LONG",
                "time": evt_time,
                "price": 101.0,
                "level": 100.5,
                "ref": "POI",
                "poi_zone_id": "z_poi",
                "meta": {},
            },
        ],
        "meta": {
            "in_play": True,
            "atr_ref": 1.0,
            "in_play_ref": {
                "ref": "POI",
                "poi_zone_id": "z_poi",
                "poi_min": 100.0,
                "poi_max": 101.0,
            },
        },
    }

    out = sm.apply_stage6_hysteresis(
        "xauusd",
        hint,
        ttl_sec=0,
        confirm_bars=1,
        switch_delta=0.01,
        micro_confirm_enabled=True,
        micro_ttl_sec=60,
        micro_dmax_atr=0.80,
        micro_boost=0.05,
        micro_boost_partial=0.02,
        now_unix=now_unix,
    )

    # Сценарій не змінюємо (він і так 4_3), змінюємо лише confidence як буст.
    assert out["scenario_id"] == "4_3"
    assert out["scenario_raw_id"] == "4_3"
    assert out["scenario_micro_ok"] is True
    assert out["scenario_raw_confidence_base"] == 0.70
    assert out["scenario_raw_confidence"] == 0.75
