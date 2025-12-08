from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import smc_structure
from smc_core.config import SMC_CORE_CONFIG
from smc_core.engine import SmcCoreEngine
from smc_core.smc_types import SmcInput, SmcZoneType
from smc_structure import ATR_PERIOD_M1, metrics, structure_engine
from tools.run_smc_5m_qa import _load_frame

DATASETS = [
    ("XAUUSD_10_14", Path("datastore/xauusd_bars_5m_10_14_snapshot.jsonl")),
    ("XAUUSD_17_21", Path("datastore/xauusd_bars_5m_17_21_snapshot.jsonl")),
    (
        "XAUUSD_week_2025_11_10",
        Path("datastore/week_2025-11-10/xauusd_bars_5m_snapshot.jsonl"),
    ),
    (
        "XAUUSD_week_2025_11_24",
        Path("datastore/week_2025-11-24/xauusd_bars_5m_snapshot.jsonl"),
    ),
    (
        "XAUUSD_export_5m_7d",
        Path("datastore/exports/XAUUSD_5m_7d.csv"),
    ),
    (
        "XAUUSD_export_5m_14d",
        Path("datastore/exports/XAUUSD_5m_14d.csv"),
    ),
    (
        "XAUUSD_export_5m_30d",
        Path("datastore/exports/XAUUSD_5m_30d.csv"),
    ),
]


def summarize() -> None:
    engine = SmcCoreEngine()
    cfg = SMC_CORE_CONFIG
    for label, path in DATASETS:
        frame = _load_frame(path, 0)
        snapshot = SmcInput(symbol="XAUUSD", tf_primary="5m", ohlc_by_tf={"5m": frame})
        hint = engine.process_snapshot(snapshot)
        zones_state = hint.zones
        structure_state = hint.structure
        zones = zones_state.zones if zones_state else []
        active = zones_state.active_zones if zones_state else []
        meta = zones_state.meta if zones_state else {}
        events = structure_state.events if structure_state else []
        event_history = structure_state.event_history if structure_state else []
        structure_meta = structure_state.meta if structure_state else {}

        ob_zones = [z for z in zones if z.zone_type is SmcZoneType.ORDER_BLOCK]
        breaker_zones = [z for z in zones if z.zone_type is SmcZoneType.BREAKER]
        fvg_zones = [z for z in zones if z.zone_type is SmcZoneType.IMBALANCE]

        active_breaker = [z for z in active if z.zone_type is SmcZoneType.BREAKER]
        active_fvg = [z for z in active if z.zone_type is SmcZoneType.IMBALANCE]

        atr_last = structure_meta.get("atr_last")
        atr_median = structure_meta.get("atr_median")
        near_bos, atr_mean = _detect_near_bos_attempts(structure_state, frame, cfg)

        print(
            f"[{label}] OB={len(ob_zones)} | Breakers={len(breaker_zones)} (active {len(active_breaker)}) "
            f"| FVG={len(fvg_zones)} (active {len(active_fvg)}) | max_distance_atr={meta.get('max_zone_distance_atr')} "
            f"| events={len(events)} hist={len(event_history)} | atr_last={atr_last} atr_med={atr_median} atr_mean={atr_mean}"
        )
        if breaker_zones:
            print("  Breaker roles:", Counter(z.role for z in breaker_zones))
            print("  Breaker directions:", Counter(z.direction for z in breaker_zones))
        if fvg_zones:
            print("  FVG roles:", Counter(z.role for z in fvg_zones))
            print("  FVG directions:", Counter(z.direction for z in fvg_zones))
        if events:
            print("  Recent BOS/CHOCH:")
            for evt in events[-5:]:
                print(
                    "    ",
                    evt.event_type,
                    evt.direction,
                    f"@ {evt.price_level:.5f}",
                    evt.time.isoformat(),
                )
        if near_bos:
            print("  Near BOS attempts (delta/threshold):")
            for attempt in near_bos[:5]:
                print(
                    "    ",
                    attempt["label"],
                    attempt["direction"],
                    f"delta={attempt['delta']:.5f}",
                    f"thr={attempt['threshold']:.5f}",
                    f"ratio={attempt['ratio']:.2f}",
                    f"time={attempt['time'].isoformat()}",  # type: ignore
                )
        print()


def _detect_near_bos_attempts(
    structure_state, frame, cfg, ratio_cutoff: float = 0.8
) -> tuple[list[dict[str, object]], float | None]:
    if not structure_state:
        return [], None
    analysis_frame = smc_structure._prepare_frame(frame, cfg.max_lookback_bars)  # type: ignore[attr-defined]
    if analysis_frame is None or analysis_frame.empty:
        return [], None
    closes = analysis_frame.get("close")
    if closes is None:
        return [], None
    closes = closes.astype(float)
    atr_series = metrics.compute_atr(analysis_frame, ATR_PERIOD_M1)
    atr_mean = None
    if atr_series is not None:
        atr_clean = atr_series.dropna()
        if not atr_clean.empty:
            atr_mean = float(atr_clean.mean())
    near_attempts: list[dict[str, object]] = []
    for leg in structure_state.legs:
        if leg.label == "UNDEFINED" or leg.reference_price is None:
            continue
        close_value = structure_engine._value_at_index(closes, leg.to_swing.index)
        atr_value = structure_engine._value_at_index(atr_series, leg.to_swing.index)  # type: ignore[arg-type]
        if close_value is None:
            continue
        delta = abs(close_value - leg.reference_price)
        atr_component = (
            0.0 if atr_value is None else atr_value * cfg.bos_min_move_atr_m1
        )
        pct_component = abs(close_value) * cfg.bos_min_move_pct_m1
        threshold = max(atr_component, pct_component)
        if delta >= threshold:
            continue
        if threshold <= 0:
            continue
        ratio = delta / threshold
        if ratio < ratio_cutoff:
            continue
        near_attempts.append(
            {
                "label": leg.label,
                "direction": _direction_from_label(leg.label),
                "delta": delta,
                "threshold": threshold,
                "ratio": ratio,
                "time": leg.to_swing.time,
            }
        )
    near_attempts.sort(key=lambda item: item["ratio"], reverse=True)  # type: ignore
    return near_attempts, atr_mean


def _direction_from_label(label: str) -> str:
    if label in {"HH", "HL"}:
        return "LONG"
    if label in {"LL", "LH"}:
        return "SHORT"
    return "UNKNOWN"


if __name__ == "__main__":
    summarize()
