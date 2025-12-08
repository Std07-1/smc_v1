"""QA-скрипт для оцінки OB_v1 на 5m даних XAU/XAG/EUR."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from smc_core.engine import SmcCoreEngine
from smc_core.smc_types import SmcInput, SmcZone, SmcZoneType

DATASETS = [
    ("XAUUSD", ROOT / "datastore" / "xauusd_bars_5m_snapshot.jsonl"),
    ("XAGUSD", ROOT / "datastore" / "xagusd_bars_5m_snapshot.jsonl"),
    ("EURUSD", ROOT / "datastore" / "EURUSD" / "eurusd_bars_5m_snapshot.jsonl"),
]
OUTPUT_PATH = ROOT / "reports" / "smc_qa_5m_summary.json"
BARS_LIMIT = 500


def _load_frame(path: Path, limit: int) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
    else:
        df = pd.read_json(path, lines=True)
    if "is_closed" in df.columns:
        df = df[df["is_closed"].astype(bool)]
    if "open_time" in df.columns:
        df["timestamp"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    elif "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    elif "time" in df.columns:
        df["timestamp"] = pd.to_datetime(df["time"], utc=True)
    else:
        raise ValueError(f"{path} не містить open_time/timestamp")
    df = df.sort_values("timestamp")
    if limit:
        df = df.tail(limit)
    df = df.reset_index(drop=True)
    required = {"open", "high", "low", "close", "timestamp"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} бракує колонок: {missing}")
    return df


def _build_snapshot(symbol: str, frame: pd.DataFrame) -> SmcInput:
    return SmcInput(symbol=symbol, tf_primary="5m", ohlc_by_tf={"5m": frame})


def _event_timestamp(zone: SmcZone) -> pd.Timestamp | None:
    if not zone.reference_event_id:
        return None
    parts = zone.reference_event_id.rsplit("_", maxsplit=1)
    if len(parts) != 2:
        return None
    try:
        return pd.Timestamp(int(parts[1]), unit="ns", tz="UTC")
    except Exception:
        return None


def main() -> None:
    engine = SmcCoreEngine()
    summaries: list[dict[str, Any]] = []
    for symbol, path in DATASETS:
        frame = _load_frame(path, BARS_LIMIT)
        snapshot = _build_snapshot(symbol, frame)
        hint = engine.process_snapshot(snapshot)
        zones_state = hint.zones
        zones = zones_state.zones if zones_state else []
        active_zones = zones_state.active_zones if zones_state else []
        zones_meta = zones_state.meta if zones_state else {}
        ob_zones = [z for z in zones if z.zone_type is SmcZoneType.ORDER_BLOCK]
        breaker_zones = [z for z in zones if z.zone_type is SmcZoneType.BREAKER]
        fvg_zones = [z for z in zones if z.zone_type is SmcZoneType.IMBALANCE]
        active_ob_zones = [
            z for z in active_zones if z.zone_type is SmcZoneType.ORDER_BLOCK
        ]
        active_breaker_zones = [
            z for z in active_zones if z.zone_type is SmcZoneType.BREAKER
        ]
        active_fvg_zones = [
            z for z in active_zones if z.zone_type is SmcZoneType.IMBALANCE
        ]
        role_counts = Counter(z.role for z in ob_zones)
        dir_counts = Counter(z.direction for z in ob_zones)
        active_role_counts = Counter(z.role for z in active_ob_zones)
        breaker_role_counts = Counter(z.role for z in breaker_zones)
        breaker_dir_counts = Counter(z.direction for z in breaker_zones)
        fvg_role_counts = Counter(z.role for z in fvg_zones)
        fvg_dir_counts = Counter(z.direction for z in fvg_zones)
        snapshot_end = frame["timestamp"].iloc[-1]
        primary_age = []
        for zone in ob_zones:
            if zone.role != "PRIMARY":
                continue
            ref_ts = _event_timestamp(zone)
            if ref_ts is None:
                continue
            age_min = (snapshot_end - ref_ts).total_seconds() / 60.0
            primary_age.append({"zone_id": zone.zone_id, "age_min": round(age_min, 2)})
            if len(primary_age) >= 3:
                break
        summary = {
            "symbol": symbol,
            "bars_used": len(frame),
            "zones_total": len(ob_zones),
            "active_zones_total": len(active_ob_zones),
            "role_counts": dict(role_counts),
            "active_role_counts": dict(active_role_counts),
            "direction_counts": dict(dir_counts),
            "breaker_zones_total": len(breaker_zones),
            "breaker_active_zones_total": len(active_breaker_zones),
            "breaker_role_counts": dict(breaker_role_counts),
            "breaker_direction_counts": dict(breaker_dir_counts),
            "fvg_zones_total": len(fvg_zones),
            "fvg_active_zones_total": len(active_fvg_zones),
            "fvg_role_counts": dict(fvg_role_counts),
            "fvg_direction_counts": dict(fvg_dir_counts),
            "distance_threshold_atr": zones_meta.get(
                "active_zone_distance_threshold_atr"
            ),
            "active_zones_within_threshold": zones_meta.get(
                "active_zones_within_threshold"
            ),
            "zones_filtered_by_distance": zones_meta.get("zones_filtered_by_distance"),
            "max_zone_distance_atr": zones_meta.get("max_zone_distance_atr"),
            "time_start": frame["timestamp"].iloc[0].isoformat(),
            "time_end": snapshot_end.isoformat(),
            "primary_reference_age_min": primary_age,
            "sample_primary_ids": [z.zone_id for z in ob_zones if z.role == "PRIMARY"][
                :3
            ],
        }
        summaries.append(summary)
        print(
            f"[{symbol}] zones={summary['zones_total']} primary={role_counts.get('PRIMARY', 0)} "
            f"counter={role_counts.get('COUNTERTREND', 0)} neutral={role_counts.get('NEUTRAL', 0)} "
            f"breakers={summary['breaker_zones_total']} fvgs={summary['fvg_zones_total']}"
        )
    OUTPUT_PATH.write_text(json.dumps(summaries, indent=2))
    print(f"Звіт збережено в {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
