"""Експорт контрольних кадрів Levels-V1 (3.3e).

Мета:
- Зафіксувати 1–2 "візуальні" кадри selected (TF=5m) для ручної перевірки вигляду.
- Без зміни UI-логіки: лише читання `/smc-viewer/snapshot`.

Артефакти:
- reports/levels_selected_frames/<ts>_<SYMBOL>/selected_5m.json
- reports/levels_selected_frames/<ts>_<SYMBOL>/selected_summary.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _ensure_repo_on_syspath() -> None:
    """Гарантує імпорти з кореня репо при запуску як скрипта."""

    if __package__:
        return
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))


_ensure_repo_on_syspath()


def _utc_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _http_get_json(url: str, *, timeout_sec: float = 10.0) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def _fetch_viewer_state(base_url: str, symbol: str) -> dict[str, Any]:
    qs = urllib.parse.urlencode({"symbol": symbol.upper()})
    url = f"{base_url.rstrip('/')}/smc-viewer/snapshot?{qs}"
    data = _http_get_json(url)
    if not isinstance(data, dict):
        raise RuntimeError("Очікував dict як SmcViewerState")
    return data


def _f(v: Any) -> float | None:
    try:
        x = float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
    return x if (x is not None and x == x) else None


def _normalize_selected_items(
    viewer_state: dict[str, Any], *, owner_tf: str
) -> list[dict[str, Any]]:
    raw = viewer_state.get("levels_selected_v1")
    if not isinstance(raw, list):
        return []

    out: list[dict[str, Any]] = []
    for s in raw:
        if not isinstance(s, dict):
            continue
        if str(s.get("owner_tf") or "").lower() != str(owner_tf).lower():
            continue

        reason_any = s.get("reason")
        if isinstance(reason_any, list):
            reasons = [str(r) for r in reason_any if r is not None]
        elif reason_any is None:
            reasons = []
        else:
            reasons = [str(reason_any)]

        rank_raw: Any = s.get("rank")
        try:
            rank = int(rank_raw) if rank_raw is not None else 0
        except (TypeError, ValueError):
            rank = 0

        out.append(
            {
                "kind": str(s.get("kind") or "").lower(),
                "label": str(s.get("label") or "").upper(),
                "source": str(s.get("source") or "").upper(),
                "price": _f(s.get("price")),
                "top": _f(s.get("top")),
                "bot": _f(s.get("bot")),
                "rank": rank,
                "reason": sorted(reasons),
                "distance_at_select": _f(s.get("distance_at_select")),
                "selected_at_close_ts": _f(s.get("selected_at_close_ts")),
            }
        )

    out.sort(
        key=lambda x: (
            int(x.get("rank") or 0),
            str(x.get("kind") or ""),
            str(x.get("label") or ""),
            round(float(x.get("price") or 0.0), 6),
            round(float(x.get("bot") or 0.0), 6),
            round(float(x.get("top") or 0.0), 6),
        )
    )
    return out


def _items_hash(items: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        items, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _count_kinds(items: list[dict[str, Any]]) -> dict[str, int]:
    out = {"line": 0, "band": 0, "other": 0}
    for it in items:
        k = str((it or {}).get("kind") or "").lower()
        if k == "line":
            out["line"] += 1
        elif k == "band":
            out["band"] += 1
        else:
            out["other"] += 1
    return out


def _extract_single_close_ts(items: list[dict[str, Any]]) -> float | None:
    values: set[float] = set()
    for it in items:
        v = (it or {}).get("selected_at_close_ts")
        x = _f(v)
        if x is not None:
            values.add(float(x))
    if len(values) == 1:
        return next(iter(values))
    return None


def build_selected_summary_md(
    *, symbol: str, base_url: str, frames: list[dict[str, Any]]
) -> str:
    lines: list[str] = []
    lines.append(f"# Levels selected frames — {symbol} (TF=5m)")
    lines.append("")
    lines.append(f"- Captured at: {_utc_iso()}")
    lines.append(f"- Endpoint: {base_url.rstrip('/')}/smc-viewer/snapshot")
    lines.append("")

    for fr in frames:
        i = fr.get("i")
        fetched_at = fr.get("fetched_at")
        payload_ts = fr.get("payload_ts")
        payload_seq = fr.get("payload_seq")
        close_ts = fr.get("selected_at_close_ts")
        counts = fr.get("counts") or {}
        items = fr.get("items") or []

        lines.append(f"## Frame {i}")
        lines.append("")
        lines.append(f"- fetched_at: {fetched_at}")
        lines.append(f"- payload_ts: {payload_ts}")
        lines.append(f"- payload_seq: {payload_seq}")
        lines.append(f"- selected_at_close_ts: {close_ts}")
        lines.append(
            "- counts: "
            + f"lines={counts.get('line', 0)} bands={counts.get('band', 0)} other={counts.get('other', 0)}"
        )
        lines.append("")

        reason_counts: dict[str, int] = {}
        for it in items:
            for r in it.get("reason") or []:
                reason_counts[str(r)] = int(reason_counts.get(str(r), 0)) + 1

        if reason_counts:
            lines.append("**reason[] summary**")
            for k in sorted(reason_counts.keys()):
                lines.append(f"- {k}: {reason_counts[k]}")
            lines.append("")

        lines.append("**items**")
        for it in items:
            kind = it.get("kind")
            label = it.get("label")
            rank = it.get("rank")
            price = it.get("price")
            bot = it.get("bot")
            top = it.get("top")
            dist = it.get("distance_at_select")
            reasons = it.get("reason") or []

            if kind == "band":
                geom = f"[{bot}, {top}]"
            else:
                geom = f"{price}"

            lines.append(
                f"- #{rank} {label} ({kind}) @ {geom} | dist={dist} | reason={', '.join([str(x) for x in reasons])}"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


@dataclass(frozen=True)
class ExportResult:
    out_dir: Path
    selected_json_path: Path
    summary_md_path: Path


def export_selected_frames(
    *, base_url: str, symbol: str, frames_n: int, interval_sec: float
) -> ExportResult:
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_dir = Path("reports") / "levels_selected_frames" / f"{run_id}_{symbol.upper()}"
    out_dir.mkdir(parents=True, exist_ok=True)

    frames: list[dict[str, Any]] = []
    n = max(1, min(2, int(frames_n)))
    interval = max(0.0, float(interval_sec))

    for i in range(n):
        viewer_state = _fetch_viewer_state(base_url, symbol)
        items = _normalize_selected_items(viewer_state, owner_tf="5m")

        frame = {
            "i": i,
            "fetched_at": _utc_iso(),
            "payload_ts": viewer_state.get("payload_ts"),
            "payload_seq": viewer_state.get("payload_seq"),
            "counts": _count_kinds(items),
            "selected_at_close_ts": _extract_single_close_ts(items),
            "items_hash": _items_hash(items),
            "items": items,
        }
        frames.append(frame)

        if interval > 0 and i < (n - 1):
            time.sleep(interval)

    payload = {
        "schema": "levels_selected_frames_v1",
        "captured_at": _utc_iso(),
        "symbol": symbol.upper(),
        "base_url": base_url,
        "tf": "5m",
        "frames": frames,
    }

    selected_json_path = out_dir / "selected_5m.json"
    selected_json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary_md_path = out_dir / "selected_summary.md"
    summary_md_path.write_text(
        build_selected_summary_md(
            symbol=symbol.upper(), base_url=base_url, frames=frames
        ),
        encoding="utf-8",
    )

    return ExportResult(
        out_dir=out_dir,
        selected_json_path=selected_json_path,
        summary_md_path=summary_md_path,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="3.3e: експорт контрольних кадрів selected_5m"
    )
    parser.add_argument(
        "--base-url", required=True, help="Напр., http://127.0.0.1:8083"
    )
    parser.add_argument("--symbol", required=True, help="Напр., XAUUSD")
    parser.add_argument(
        "--frames",
        type=int,
        default=2,
        help="Кількість кадрів (1–2). За замовчуванням 2.",
    )
    parser.add_argument(
        "--interval-sec",
        type=float,
        default=0.7,
        help="Пауза між кадрами, сек. (за замовчуванням 0.7).",
    )
    args = parser.parse_args()

    res = export_selected_frames(
        base_url=str(args.base_url),
        symbol=str(args.symbol),
        frames_n=int(args.frames),
        interval_sec=float(args.interval_sec),
    )

    print(f"OK: збережено у {res.out_dir}")
    print(f"- {res.selected_json_path}")
    print(f"- {res.summary_md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
