"""CLI-утиліта для аналізу чутливості BOS/CHOCH порогів на основі snapshot JSON."""

from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from core.serialization import safe_float, safe_int

LOOSE_ATR_THRESHOLD = 0.6
TIGHT_ATR_THRESHOLD = 1.3
LOOSE_PCT_THRESHOLD = 0.002
TIGHT_PCT_THRESHOLD = 0.0035

CSV_COLUMNS = [
    "symbol",
    "tf_input",
    "snapshot_start_ts",
    "snapshot_end_ts",
    "leg_index",
    "from_index",
    "to_index",
    "from_kind",
    "to_kind",
    "label",
    "from_price",
    "to_price",
    "amplitude_abs",
    "amplitude_pct",
    "is_event_leg",
    "event_type",
    "event_direction",
    "passes_loose_atr",
    "passes_tight_atr",
    "passes_loose_pct",
    "passes_tight_pct",
]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Збирає статистику по всіх легах структури та порівнює їх із заданими порогами."
    )
    parser.add_argument(
        "--inputs",
        nargs="+",
        required=True,
        help="JSON-файли, згенеровані tools.smc_snapshot_runner",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="Шлях до підсумкового CSV із легами",
    )
    parser.add_argument(
        "--symbol-filter",
        default=None,
        help="Опціонально: брати дані лише для вказаного символу",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    inputs = [Path(p) for p in args.inputs]
    out_path = Path(args.out)
    rows: list[dict[str, Any]] = []

    for path in inputs:
        rows.extend(_collect_from_snapshot(path, args.symbol_filter))

    _write_csv(out_path, rows)
    return 0


def _collect_from_snapshot(
    path: Path, symbol_filter: str | None
) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    structure = payload.get("structure") or {}
    meta = structure.get("meta") or {}
    symbol = meta.get("symbol")
    if symbol_filter and symbol != symbol_filter:
        return []

    atr_value = _pick_atr(meta)
    legs = structure.get("legs") or []
    events = structure.get("events") or []
    event_flags = _map_events(events)

    stats: list[dict[str, Any]] = []
    for idx, leg in enumerate(legs):
        stats.append(
            _build_leg_stats(
                idx,
                leg,
                meta,
                event_flags.get(_leg_key(leg)),
                atr_value,
            )
        )
    return stats


def _pick_atr(meta: dict[str, Any]) -> float | None:
    for key in ("atr_last", "atr_median"):
        value = safe_float(meta.get(key))
        if value is not None and value > 0:
            return value
    return None


def _leg_key(leg: dict[str, Any]) -> tuple[int | None, int | None]:
    from_swing = leg.get("from_swing") or {}
    to_swing = leg.get("to_swing") or {}
    return (safe_int(from_swing.get("index")), safe_int(to_swing.get("index")))


def _map_events(
    events: Iterable[dict[str, Any]],
) -> dict[tuple[int | None, int | None], dict[str, Any]]:
    mapping: dict[tuple[int | None, int | None], dict[str, Any]] = {}
    for event in events:
        source_leg = event.get("source_leg") or {}
        mapping[_leg_key(source_leg)] = {
            "event_type": event.get("event_type"),
            "event_direction": event.get("direction"),
        }
    return mapping


def _build_leg_stats(
    leg_index: int,
    leg: dict[str, Any],
    meta: dict[str, Any],
    event_info: dict[str, Any] | None,
    atr_value: float | None,
) -> dict[str, Any]:
    from_swing = leg.get("from_swing") or {}
    to_swing = leg.get("to_swing") or {}

    from_price = safe_float(from_swing.get("price"))
    to_price = safe_float(to_swing.get("price"))
    amplitude_abs = None
    amplitude_pct = None
    if from_price is not None and to_price is not None:
        amplitude_abs = abs(to_price - from_price)
        mid_price = (to_price + from_price) / 2 if (to_price + from_price) else 0.0
        if mid_price:
            amplitude_pct = amplitude_abs / mid_price

    ratio = None
    if atr_value and atr_value > 0 and amplitude_abs is not None:
        ratio = amplitude_abs / atr_value

    passes_loose_atr = None if ratio is None else ratio >= LOOSE_ATR_THRESHOLD
    passes_tight_atr = None if ratio is None else ratio >= TIGHT_ATR_THRESHOLD
    passes_loose_pct = (
        None if amplitude_pct is None else amplitude_pct >= LOOSE_PCT_THRESHOLD
    )
    passes_tight_pct = (
        None if amplitude_pct is None else amplitude_pct >= TIGHT_PCT_THRESHOLD
    )

    is_event_leg = event_info is not None
    event_type = event_info.get("event_type") if event_info else None
    event_direction = event_info.get("event_direction") if event_info else None

    return {
        "symbol": meta.get("symbol"),
        "tf_input": meta.get("tf_input"),
        "snapshot_start_ts": meta.get("snapshot_start_ts"),
        "snapshot_end_ts": meta.get("snapshot_end_ts"),
        "leg_index": leg_index,
        "from_index": safe_int(from_swing.get("index")),
        "to_index": safe_int(to_swing.get("index")),
        "from_kind": from_swing.get("kind"),
        "to_kind": to_swing.get("kind"),
        "label": leg.get("label"),
        "from_price": from_price,
        "to_price": to_price,
        "amplitude_abs": amplitude_abs,
        "amplitude_pct": amplitude_pct,
        "is_event_leg": is_event_leg,
        "event_type": event_type,
        "event_direction": event_direction,
        "passes_loose_atr": passes_loose_atr,
        "passes_tight_atr": passes_tight_atr,
        "passes_loose_pct": passes_loose_pct,
        "passes_tight_pct": passes_tight_pct,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if v is None else v) for k, v in row.items()})


if __name__ == "__main__":  # pragma: no cover - ручний виклик CLI
    raise SystemExit(main())
