"""Utility to summarize SmcZonesState meta for stored SmcHint snapshots."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from pathlib import Path

REPORTS = [
    "smc_xau_5m_2000bars_A.json",
    "smc_xau_5m_2000bars_B.json",
    "smc_xau_5m_2000bars_C.json",
    "smc_xau_5m_2000bars_D.json",
    "smc_xau_5m_2000bars_10_14.json",
    "smc_xau_5m_2000bars_17_21.json",
]

BASE = Path(__file__).resolve().parents[1] / "reports"


def summarize(files: Iterable[str]) -> None:
    for rel in files:
        path = BASE / rel
        if not path.exists():
            print(f"!! {rel}: missing")
            continue
        with path.open("rb") as fh:
            raw = fh.read()
        for encoding in ("utf-8", "utf-8-sig", "utf-16", "utf-16le", "utf-16be"):
            try:
                text = raw.decode(encoding)
                data = json.loads(text)
                break
            except Exception:
                continue
        else:
            print(f"!! {rel}: unable to decode JSON")
            continue
        zones_block = data.get("zones") or {}
        pools = zones_block.get("zones") if isinstance(zones_block, dict) else None
        meta = zones_block.get("meta") if isinstance(zones_block, dict) else {}
        print(f"== {rel} ==")
        if not pools:
            print("zones: 0")
            print(f"meta: {meta}\n")
            continue
        role_counts: dict[str, int] = {}
        dir_counts: dict[str, int] = {}
        for zone in pools:
            role = str(zone.get("role", "UNKNOWN"))
            role_counts[role] = role_counts.get(role, 0) + 1
            direction = str(zone.get("direction", "UNKNOWN"))
            dir_counts[direction] = dir_counts.get(direction, 0) + 1
        print(f"zones: {len(pools)}")
        print(f"roles: {role_counts}")
        print(f"dirs: {dir_counts}")
        print(f"meta: {meta}\n")


if __name__ == "__main__":
    targets = sys.argv[1:] or REPORTS
    summarize(targets)
