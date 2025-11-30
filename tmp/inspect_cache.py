from __future__ import annotations

import json
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from pprint import pprint
from typing import Any

TARGETS = {
    "eurusd_1m": (Path("datastore/eurusd_bars_1m_snapshot.jsonl"), 60_000),
    "xauusd_1m": (Path("datastore/xauusd_bars_1m_snapshot.jsonl"), 60_000),
    "xagusd_1m": (Path("datastore/xagusd_bars_1m_snapshot.jsonl"), 60_000),
}


def summarize(label: str, path: Path, interval_ms: int) -> dict[str, object]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return {"label": label, "exists": False}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not rows:
        return {
            "label": label,
            "exists": True,
            "rows": 0,
        }
    rows.sort(key=lambda r: int(r.get("open_time", 0)))
    opens = [int(r.get("open_time", 0)) for r in rows]
    max_gap = 0
    interval = interval_ms
    for a, b in pairwise(opens):
        gap = max(0, (b - a) // interval)
        if gap > max_gap:
            max_gap = gap
    first = opens[0]
    last = opens[-1]
    now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    age_min = (now_ms - last) / 60_000
    return {
        "label": label,
        "exists": True,
        "rows": len(rows),
        "first": first,
        "last": last,
        "first_iso": datetime.fromtimestamp(first / 1000, tz=UTC).isoformat(),
        "last_iso": datetime.fromtimestamp(last / 1000, tz=UTC).isoformat(),
        "age_min": age_min,
        "max_gap": max_gap,
    }


def main() -> None:
    for label, (path, interval_ms) in TARGETS.items():
        info = summarize(label, path, interval_ms)
        pprint(info)


if __name__ == "__main__":
    main()
