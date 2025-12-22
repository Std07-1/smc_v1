"""Тести Stage6 (4.2 vs 4.3) як технічної класифікації, не сигналу."""

from __future__ import annotations

import pandas as pd

from smc_core.smc_types import (
    SmcLiquidityPool,
    SmcLiquidityState,
    SmcLiquidityType,
    SmcRange,
    SmcRangeState,
    SmcStructureEvent,
    SmcStructureLeg,
    SmcStructureState,
    SmcSwing,
    SmcTrend,
    SmcZonesState,
)
from smc_core.stage6_scenario import decide_42_43


def _df_5m(
    *,
    ts0: str = "2025-12-21T00:00:00Z",
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
) -> pd.DataFrame:
    idx = pd.date_range(ts0, periods=len(closes), freq="5min", tz="UTC")
    if highs is None:
        highs = [c + 0.4 for c in closes]
    if lows is None:
        lows = [c - 0.4 for c in closes]
    return pd.DataFrame(
        {
            "timestamp": idx,
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1.0] * len(closes),
        }
    )


def _df_htf(
    *,
    tf: str,
    bars: int = 20,
    ts0: str = "2025-12-18T00:00:00Z",
    mid: float = 100.0,
    half_range: float = 1.0,
    slope_per_bar: float = 0.0,
    close_nan: bool = False,
) -> pd.DataFrame:
    freq = "1h" if str(tf) == "1h" else "4h"
    idx = pd.date_range(ts0, periods=bars, freq=freq, tz="UTC")

    closes: list[float | None] = []
    highs: list[float] = []
    lows: list[float] = []
    for i in range(int(bars)):
        c = float(mid) + float(slope_per_bar) * float(i)
        closes.append(None if close_nan else c)
        highs.append(c + float(half_range))
        lows.append(c - float(half_range))

    return pd.DataFrame(
        {
            "timestamp": idx,
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "complete": [True] * int(bars),
        }
    )


def _structure_with_range(
    *,
    range_high: float,
    range_low: float,
    bias: str = "NEUTRAL",
    events: list[SmcStructureEvent] | None = None,
) -> SmcStructureState:
    r = SmcRange(
        high=range_high,
        low=range_low,
        eq_level=(range_high + range_low) / 2.0,
        start_time=pd.Timestamp("2025-12-21T00:00:00Z"),
        end_time=None,
        state=SmcRangeState.INSIDE,
    )
    return SmcStructureState(
        primary_tf="5m",
        trend=SmcTrend.UNKNOWN,
        swings=[
            SmcSwing(
                index=0,
                time=pd.Timestamp("2025-12-21T00:00:00Z"),
                price=float(range_low),
                kind="LOW",
                strength=1,
            ),
            SmcSwing(
                index=1,
                time=pd.Timestamp("2025-12-21T00:05:00Z"),
                price=float(range_high),
                kind="HIGH",
                strength=1,
            ),
        ],
        bias=bias,  # type: ignore
        active_range=r,
        ranges=[r],
        events=list(events or []),
    )


def _dummy_leg(ts: str) -> SmcStructureLeg:
    s1 = SmcSwing(index=0, time=pd.Timestamp(ts), price=1.0, kind="LOW", strength=1)
    s2 = SmcSwing(index=1, time=pd.Timestamp(ts), price=2.0, kind="HIGH", strength=1)
    return SmcStructureLeg(from_swing=s1, to_swing=s2, label="UNDEFINED")


def test_stage6_42_continuation_short_bias_sweep_rejection_bos_down() -> None:
    # Range 100..110, EQ=105. Price у premium.
    frame = _df_5m(
        closes=[104.0, 106.0, 107.0, 108.0, 109.0, 108.8],
        highs=[104.4, 106.4, 107.4, 111.2, 110.1, 109.4],  # sweep UP > 110
        lows=[103.6, 105.6, 106.6, 107.6, 108.6, 108.2],
    )

    mid = float(frame["close"].iloc[-1])
    frame_1h = _df_htf(tf="1h", mid=mid, half_range=2.0)
    frame_4h = _df_htf(tf="4h", mid=mid, half_range=2.0)

    # liquidity pool EQH @110
    pool = SmcLiquidityPool(
        level=110.0,
        liq_type=SmcLiquidityType.EQH,
        strength=10.0,
        n_touches=3,
        first_time=None,
        last_time=None,
    )
    liq = SmcLiquidityState(pools=[pool], magnets=[], amd_phase=None)

    sweep_ts = frame["timestamp"].iloc[3]
    bos_ts = pd.Timestamp(sweep_ts) + pd.Timedelta(minutes=5)
    bos_down = SmcStructureEvent(
        event_type="BOS",
        direction="SHORT",
        price_level=107.0,
        time=bos_ts,
        source_leg=_dummy_leg("2025-12-21T00:00:00Z"),
    )

    structure = _structure_with_range(
        range_high=110.0, range_low=100.0, bias="SHORT", events=[bos_down]
    )
    zones = SmcZonesState()

    decision = decide_42_43(
        symbol="xauusd",
        tf_primary="5m",
        primary_frame=frame,
        ohlc_by_tf={"5m": frame, "1h": frame_1h, "4h": frame_4h},
        structure=structure,
        liquidity=liq,
        zones=zones,
        context={"trend_context_4h": {"bias": "SHORT"}},
    )

    assert decision.scenario_id == "4_2"
    assert decision.direction == "SHORT"
    assert 0.5 <= decision.confidence <= 0.95


def test_stage6_43_break_hold_up_after_sweep() -> None:
    # Range 100..110, EQ=105. Після sweep price закріплюється вище 110.
    closes = [104.0, 106.0, 108.0, 109.2, 110.6, 110.7, 110.8]
    highs = [104.4, 106.4, 111.1, 110.3, 110.9, 111.0, 111.2]  # sweep UP
    lows = [103.6, 105.6, 107.6, 108.7, 109.9, 110.0, 110.1]  # ретест low<=110
    frame = _df_5m(closes=closes, highs=highs, lows=lows)

    mid = float(frame["close"].iloc[-1])
    frame_1h = _df_htf(tf="1h", mid=mid, half_range=2.0)
    frame_4h = _df_htf(tf="4h", mid=mid, half_range=2.0)

    pool = SmcLiquidityPool(
        level=110.0,
        liq_type=SmcLiquidityType.EQH,
        strength=10.0,
        n_touches=3,
        first_time=None,
        last_time=None,
    )
    liq = SmcLiquidityState(pools=[pool], magnets=[], amd_phase=None)

    sweep_ts = frame["timestamp"].iloc[2]
    bos_ts = pd.Timestamp(sweep_ts) + pd.Timedelta(minutes=10)
    bos_up = SmcStructureEvent(
        event_type="BOS",
        direction="LONG",
        price_level=110.0,
        time=bos_ts,
        source_leg=_dummy_leg("2025-12-21T00:00:00Z"),
    )

    structure = _structure_with_range(
        range_high=110.0, range_low=100.0, bias="NEUTRAL", events=[bos_up]
    )

    decision = decide_42_43(
        symbol="xauusd",
        tf_primary="5m",
        primary_frame=frame,
        ohlc_by_tf={"5m": frame, "1h": frame_1h, "4h": frame_4h},
        structure=structure,
        liquidity=liq,
        zones=SmcZonesState(),
        context={"trend_context_h1": {"bias": "LONG"}},
    )

    assert decision.scenario_id == "4_3"
    assert decision.direction == "LONG"


def test_stage6_no_htf_lite_bias_scoring_when_context_bias_present() -> None:
    # Контекст дає HTF bias LONG, але HTF‑Lite PD буде PREMIUM -> SHORT.
    # P0c: HTF‑Lite bias не має додаватися як окремий скоринговий факт.
    frame = _df_5m(closes=[100.0, 101.0, 102.0, 103.0, 104.0])
    structure = _structure_with_range(range_high=110.0, range_low=90.0)
    frame_1h = _df_htf(tf="1h", mid=100.0, half_range=10.0)
    frame_4h = _df_htf(tf="4h", mid=100.0, half_range=10.0)

    decision = decide_42_43(
        symbol="xauusd",
        tf_primary="5m",
        primary_frame=frame,
        ohlc_by_tf={"5m": frame, "1h": frame_1h, "4h": frame_4h},
        structure=structure,
        liquidity=SmcLiquidityState(),
        zones=SmcZonesState(),
        context={"trend_context_4h": {"bias": "LONG"}},
    )

    assert all("HTF‑Lite bias" not in str(w) for w in (decision.why or []))


def test_stage6_events_after_sweep_chop_when_bos_both() -> None:
    frame = _df_5m(
        closes=[104.0, 106.0, 107.0, 108.0, 109.0, 108.8],
        highs=[104.4, 106.4, 107.4, 111.2, 110.1, 109.4],  # sweep UP > 110
        lows=[103.6, 105.6, 106.6, 107.6, 108.6, 108.2],
    )

    mid = float(frame["close"].iloc[-1])
    frame_1h = _df_htf(tf="1h", mid=mid, half_range=2.0)
    frame_4h = _df_htf(tf="4h", mid=mid, half_range=2.0)

    pool = SmcLiquidityPool(
        level=110.0,
        liq_type=SmcLiquidityType.EQH,
        strength=10.0,
        n_touches=3,
        first_time=None,
        last_time=None,
    )
    liq = SmcLiquidityState(pools=[pool], magnets=[], amd_phase=None)

    sweep_ts = frame["timestamp"].iloc[3]
    bos1_ts = pd.Timestamp(sweep_ts) + pd.Timedelta(minutes=5)
    bos2_ts = pd.Timestamp(sweep_ts) + pd.Timedelta(minutes=10)
    bos_down = SmcStructureEvent(
        event_type="BOS",
        direction="SHORT",
        price_level=107.0,
        time=bos1_ts,
        source_leg=_dummy_leg("2025-12-21T00:00:00Z"),
    )
    bos_up = SmcStructureEvent(
        event_type="BOS",
        direction="LONG",
        price_level=110.0,
        time=bos2_ts,
        source_leg=_dummy_leg("2025-12-21T00:00:00Z"),
    )

    structure = _structure_with_range(
        range_high=110.0, range_low=100.0, bias="NEUTRAL", events=[bos_down, bos_up]
    )

    decision = decide_42_43(
        symbol="xauusd",
        tf_primary="5m",
        primary_frame=frame,
        ohlc_by_tf={"5m": frame, "1h": frame_1h, "4h": frame_4h},
        structure=structure,
        liquidity=liq,
        zones=SmcZonesState(),
        context={"trend_context_4h": {"bias": "SHORT"}},
    )

    ev = decision.telemetry.get("events_after_sweep") or {}
    assert bool(ev.get("chop")) is True
    assert bool(ev.get("bos_down")) is False
    assert bool(ev.get("bos_up")) is False


def test_stage6_failed_hold_uses_5m_level_even_if_htf_higher() -> None:
    # Канон (детермінізм): hold_level_up завжди = 5m range_high.
    # У цьому кейсі sweep UP і відкат нижче range_high → failed_hold_up=True.
    frame = _df_5m(
        closes=[104.0, 106.0, 108.0, 109.2, 109.0, 108.7],
        highs=[104.4, 106.4, 111.1, 110.3, 109.4, 109.2],  # sweep UP > 110
        lows=[103.6, 105.6, 107.6, 108.7, 108.4, 108.2],
    )

    frame_1h = _df_htf(tf="1h", mid=100.0, half_range=20.0)
    frame_4h = _df_htf(tf="4h", mid=100.0, half_range=20.0)

    pool = SmcLiquidityPool(
        level=110.0,
        liq_type=SmcLiquidityType.EQH,
        strength=10.0,
        n_touches=3,
        first_time=None,
        last_time=None,
    )
    liq = SmcLiquidityState(pools=[pool], magnets=[], amd_phase=None)

    structure = _structure_with_range(range_high=110.0, range_low=100.0, bias="SHORT")

    decision = decide_42_43(
        symbol="xauusd",
        tf_primary="5m",
        primary_frame=frame,
        ohlc_by_tf={"5m": frame, "1h": frame_1h, "4h": frame_4h},
        structure=structure,
        liquidity=liq,
        zones=SmcZonesState(),
        context={"trend_context_4h": {"bias": "SHORT"}},
    )

    assert decision.telemetry.get("failed_hold_up") is True
    assert decision.key_levels.get("hold_level_up") == 110.0

    # SMC-словник для UI: завжди має бути присутній.
    smc = decision.key_levels.get("smc")
    assert isinstance(smc, dict)
    assert isinstance(smc.get("htf"), dict)
    assert isinstance(smc.get("structure_5m"), dict)
    assert isinstance(smc.get("facts"), dict)


def test_stage6_unclear_when_no_htf_bias_gate() -> None:
    # Є 1h+4h (готові), але bias NEUTRAL/UNKNOWN і HTF‑Lite теж NEUTRAL (ціна рівно на середині DR).
    frame = _df_5m(closes=[100.0, 100.0, 100.0, 100.0, 100.0])
    structure = _structure_with_range(range_high=101.0, range_low=99.0)
    frame_1h = _df_htf(tf="1h", mid=100.0, half_range=1.0, slope_per_bar=0.0)
    frame_4h = _df_htf(tf="4h", mid=100.0, half_range=1.0, slope_per_bar=0.0)
    decision = decide_42_43(
        symbol="xauusd",
        tf_primary="5m",
        primary_frame=frame,
        ohlc_by_tf={"5m": frame, "1h": frame_1h, "4h": frame_4h},
        structure=structure,
        liquidity=SmcLiquidityState(),
        zones=SmcZonesState(),
        context={},
    )
    assert decision.scenario_id == "UNCLEAR"
    assert decision.direction == "NEUTRAL"
    assert decision.telemetry.get("unclear_reason") == "NO_HTF"


def test_stage6_unclear_when_no_htf_frames_gate() -> None:
    frame = _df_5m(closes=[100.0, 100.1, 100.2, 100.3, 100.4])
    structure = _structure_with_range(range_high=101.0, range_low=99.0)
    frame_1h = _df_htf(tf="1h", mid=100.0, half_range=1.0)
    decision = decide_42_43(
        symbol="xauusd",
        tf_primary="5m",
        primary_frame=frame,
        ohlc_by_tf={"5m": frame, "1h": frame_1h},
        structure=structure,
        liquidity=SmcLiquidityState(),
        zones=SmcZonesState(),
        context={"trend_context_4h": {"bias": "LONG"}},
    )
    assert decision.scenario_id == "UNCLEAR"
    assert decision.telemetry.get("unclear_reason") == "NO_HTF_FRAMES"
    assert "no_htf_frames" in (decision.telemetry.get("gates") or [])


def test_stage6_unclear_when_atr_unavailable_gate() -> None:
    # HTF є, але close на 1h/4h відсутній/NaN → ATR(14) неможливий.
    frame = _df_5m(closes=[100.0, 100.2, 100.1, 100.25, 100.3, 100.28])
    structure = _structure_with_range(range_high=101.0, range_low=99.0)
    frame_1h = _df_htf(tf="1h", mid=100.28, half_range=1.0, close_nan=True)
    frame_4h = _df_htf(tf="4h", mid=100.28, half_range=1.0, close_nan=True)
    decision = decide_42_43(
        symbol="xauusd",
        tf_primary="5m",
        primary_frame=frame,
        ohlc_by_tf={"5m": frame, "1h": frame_1h, "4h": frame_4h},
        structure=structure,
        liquidity=SmcLiquidityState(),
        zones=SmcZonesState(),
        context={"trend_context_4h": {"bias": "LONG"}},
    )
    assert decision.scenario_id == "UNCLEAR"
    assert decision.telemetry.get("unclear_reason") == "ATR_UNAVAILABLE"
    assert "atr_unavailable" in (decision.telemetry.get("gates") or [])


def test_stage6_htf_bias_fallback_from_frames() -> None:
    # Контекст порожній, але 4h фрейм має явний up-slope.
    frame_5m = _df_5m(closes=[100.0, 100.1, 100.2, 100.3, 100.4, 100.5])
    frame_1h = _df_htf(tf="1h", mid=101.0, half_range=1.0, slope_per_bar=0.05)
    frame_4h = _df_htf(tf="4h", mid=101.0, half_range=1.0, slope_per_bar=0.12)
    structure = _structure_with_range(range_high=101.0, range_low=99.0)
    decision = decide_42_43(
        symbol="xauusd",
        tf_primary="5m",
        primary_frame=frame_5m,
        ohlc_by_tf={"5m": frame_5m, "1h": frame_1h, "4h": frame_4h},
        structure=structure,
        liquidity=SmcLiquidityState(),
        zones=SmcZonesState(),
        context={},
    )
    # Може бути UNCLEAR через відсутність sweep/events, але гейт no_htf_bias не має спрацювати.
    assert decision.telemetry.get("unclear_reason") != "NO_HTF_FRAMES"
    assert decision.telemetry.get("gates") != ["no_htf_bias"]


def test_stage6_unclear_reason_low_score() -> None:
    # HTF bias є (MIXED), range є, структура «формально» є, але фічі слабкі → LOW_SCORE.
    frame = _df_5m(closes=[100.0, 100.05, 100.1, 100.12, 100.15, 100.18])
    dummy_evt = SmcStructureEvent(
        event_type="BOS",
        direction="LONG",
        price_level=100.2,
        time=pd.Timestamp("2025-12-21T00:10:00Z"),
        source_leg=_dummy_leg("2025-12-21T00:00:00Z"),
    )
    structure = _structure_with_range(
        range_high=101.0, range_low=99.0, events=[dummy_evt]
    )
    mid = float(frame["close"].iloc[-1])
    frame_1h = _df_htf(tf="1h", mid=mid, half_range=1.0, slope_per_bar=0.0)
    frame_4h = _df_htf(tf="4h", mid=mid, half_range=1.0, slope_per_bar=0.0)
    decision = decide_42_43(
        symbol="xauusd",
        tf_primary="5m",
        primary_frame=frame,
        ohlc_by_tf={"5m": frame, "1h": frame_1h, "4h": frame_4h},
        structure=structure,
        liquidity=SmcLiquidityState(),
        zones=SmcZonesState(),
        context={
            "trend_context_4h": {"bias": "LONG"},
            "trend_context_h1": {"bias": "SHORT"},
        },
    )
    assert decision.scenario_id == "UNCLEAR"
    assert decision.telemetry.get("unclear_reason") == "LOW_SCORE"


def test_stage6_unclear_reason_conflict() -> None:
    # P0: після sweep UP із поверненням під hold-рівень очікуємо failed_hold → 4_2.
    frame = _df_5m(
        closes=[104.0, 103.6, 103.4, 103.2, 103.3, 103.25],
        highs=[104.4, 104.6, 110.8, 103.6, 103.7, 103.6],  # sweep UP > range_high
        lows=[103.6, 103.2, 103.0, 103.0, 103.1, 103.1],
    )
    pool = SmcLiquidityPool(
        level=110.0,
        liq_type=SmcLiquidityType.EQH,
        strength=10.0,
        n_touches=3,
        first_time=None,
        last_time=None,
    )
    liq = SmcLiquidityState(pools=[pool], magnets=[], amd_phase=None)

    sweep_ts = frame["timestamp"].iloc[2]
    bos_down = SmcStructureEvent(
        event_type="BOS",
        direction="SHORT",
        price_level=103.0,
        time=pd.Timestamp(sweep_ts) + pd.Timedelta(minutes=5),
        source_leg=_dummy_leg("2025-12-21T00:00:00Z"),
    )
    bos_up = SmcStructureEvent(
        event_type="BOS",
        direction="LONG",
        price_level=104.0,
        time=pd.Timestamp(sweep_ts) + pd.Timedelta(minutes=10),
        source_leg=_dummy_leg("2025-12-21T00:00:00Z"),
    )
    structure = _structure_with_range(
        range_high=110.0, range_low=100.0, bias="NEUTRAL", events=[bos_down, bos_up]
    )

    mid = float(frame["close"].iloc[-1])
    frame_1h = _df_htf(tf="1h", mid=mid, half_range=2.0, slope_per_bar=0.0)
    frame_4h = _df_htf(tf="4h", mid=mid, half_range=2.0, slope_per_bar=0.0)

    decision = decide_42_43(
        symbol="xauusd",
        tf_primary="5m",
        primary_frame=frame,
        ohlc_by_tf={"5m": frame, "1h": frame_1h, "4h": frame_4h},
        structure=structure,
        liquidity=liq,
        zones=SmcZonesState(),
        context={
            "trend_context_4h": {"bias": "LONG"},
            "trend_context_h1": {"bias": "SHORT"},
        },
    )

    assert decision.scenario_id == "4_2"
    smc = decision.key_levels.get("smc")
    assert isinstance(smc, dict)
    facts = smc.get("facts")
    assert isinstance(facts, dict)
    failed = facts.get("failed_hold")
    assert isinstance(failed, dict)
    assert bool(failed.get("ok")) is True
    assert any("failed_hold_after_sweep" in s for s in decision.why)


def test_stage6_unclear_reason_conflict_sweep_down() -> None:
    # Конфлікт: після sweep DOWN маємо і BOS_DOWN, і BOS_UP → скор близький.
    # Важливо: P0 failed_hold_up тут НЕ має спрацювати, бо sweep не UP.
    frame = _df_5m(
        closes=[106.0, 105.6, 105.4, 106.5, 107.2, 108.0],
        highs=[106.4, 106.6, 106.2, 106.9, 107.6, 108.4],
        lows=[105.6, 105.2, 104.2, 106.1, 106.8, 107.6],  # sweep DOWN < pool_level
    )
    pool = SmcLiquidityPool(
        level=105.0,
        liq_type=SmcLiquidityType.EQL,
        strength=10.0,
        n_touches=3,
        first_time=None,
        last_time=None,
    )
    liq = SmcLiquidityState(pools=[pool], magnets=[], amd_phase=None)

    sweep_ts = frame["timestamp"].iloc[2]
    bos_down = SmcStructureEvent(
        event_type="BOS",
        direction="SHORT",
        price_level=105.0,
        time=pd.Timestamp(sweep_ts) + pd.Timedelta(minutes=5),
        source_leg=_dummy_leg("2025-12-21T00:00:00Z"),
    )
    bos_up = SmcStructureEvent(
        event_type="BOS",
        direction="LONG",
        price_level=106.0,
        time=pd.Timestamp(sweep_ts) + pd.Timedelta(minutes=10),
        source_leg=_dummy_leg("2025-12-21T00:00:00Z"),
    )
    structure = _structure_with_range(
        range_high=110.0, range_low=100.0, bias="NEUTRAL", events=[bos_down, bos_up]
    )

    mid = float(frame["close"].iloc[-1])
    frame_1h = _df_htf(tf="1h", mid=mid, half_range=2.0, slope_per_bar=0.0)
    frame_4h = _df_htf(tf="4h", mid=mid, half_range=2.0, slope_per_bar=0.0)

    decision = decide_42_43(
        symbol="xauusd",
        tf_primary="5m",
        primary_frame=frame,
        ohlc_by_tf={"5m": frame, "1h": frame_1h, "4h": frame_4h},
        structure=structure,
        liquidity=liq,
        zones=SmcZonesState(),
        context={
            "trend_context_4h": {"bias": "LONG"},
            "trend_context_h1": {"bias": "SHORT"},
        },
    )

    assert decision.scenario_id == "UNCLEAR"
    # P0b: якщо після sweep є і BOS_DOWN, і BOS_UP, Stage6 трактує це як chop і
    # ігнорує обидві події як скорингові "факти" → частіше виходить LOW_SCORE.
    assert decision.telemetry.get("unclear_reason") == "LOW_SCORE"


def test_stage6_raw_is_deterministic_and_key_levels_present() -> None:
    frame = _df_5m(
        closes=[104.0, 105.0, 106.0, 107.0, 108.0, 108.2],
        highs=[104.4, 105.4, 106.4, 107.4, 108.4, 108.6],
        lows=[103.6, 104.6, 105.6, 106.6, 107.6, 107.9],
    )

    # Мінімальна структура: достатньо 1 події, щоб пройти gate no_structure.
    evt = SmcStructureEvent(
        event_type="BOS",
        direction="LONG",
        price_level=108.0,
        time=pd.Timestamp(frame["timestamp"].iloc[-1]) + pd.Timedelta(minutes=5),
        source_leg=_dummy_leg("2025-12-21T00:00:00Z"),
    )
    structure = _structure_with_range(range_high=110.0, range_low=100.0, events=[evt])

    mid = float(frame["close"].iloc[-1])
    frame_1h = _df_htf(tf="1h", mid=mid, half_range=2.0, slope_per_bar=0.0)
    frame_4h = _df_htf(tf="4h", mid=mid, half_range=2.0, slope_per_bar=0.0)

    d1 = decide_42_43(
        symbol="xauusd",
        tf_primary="5m",
        primary_frame=frame,
        ohlc_by_tf={"5m": frame, "1h": frame_1h, "4h": frame_4h},
        structure=structure,
        liquidity=SmcLiquidityState(),
        zones=SmcZonesState(),
        context={"trend_context_4h": {"bias": "LONG"}},
    )
    d2 = decide_42_43(
        symbol="xauusd",
        tf_primary="5m",
        primary_frame=frame,
        ohlc_by_tf={"5m": frame, "1h": frame_1h, "4h": frame_4h},
        structure=structure,
        liquidity=SmcLiquidityState(),
        zones=SmcZonesState(),
        context={"trend_context_4h": {"bias": "LONG"}},
    )

    assert d1.scenario_id == d2.scenario_id
    assert d1.direction == d2.direction
    assert round(float(d1.confidence), 12) == round(float(d2.confidence), 12)
    assert d1.why == d2.why
    assert d1.key_levels == d2.key_levels

    # Для не-UNCLEAR рішення key_levels має містити щонайменше базові рівні.
    if d1.scenario_id != "UNCLEAR":
        for k in ("range_high", "range_low", "hold_level_up", "hold_level_dn"):
            assert k in d1.key_levels
