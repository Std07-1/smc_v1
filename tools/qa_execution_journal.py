"""QA: журнал Stage5 Execution (1m) на снапшотах.

Ціль: отримати прозорий «журнал», щоб швидко перевірити дійсність гейтінгу `in_play`
та micro-подій (SWEEP / MICRO_BOS / MICRO_CHOCH / RETEST_OK) на реальних jsonl.

Вихід:
- Markdown-репорт у `reports/` з підсумком + таблицями по кроках.

Приклад (PowerShell):
; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" -m tools.qa_execution_journal \
    --path datastore/xauusd_bars_5m_snapshot.jsonl --steps 240 --out reports/execution_journal_xauusd.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd


def _ensure_repo_on_syspath() -> None:
    """Гарантує імпорти з кореня репо при запуску як скрипта."""

    repo_root = Path(__file__).resolve().parents[1]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


def _resolve_snapshot_path(raw: str) -> Path:
    """Дружньо резолвить шлях до *_snapshot.jsonl."""

    p = Path(str(raw).strip())
    repo_root = Path(__file__).resolve().parents[1]

    candidates: list[Path] = [p]
    if not p.is_absolute():
        candidates.append(repo_root / p)
        candidates.append(repo_root / "datastore" / p)
        candidates.append(repo_root / "datastore" / p.name)

    for c in candidates:
        try:
            if c.exists() and c.is_file():
                return c
        except OSError:
            continue
    return p


def _infer_symbol_tf(path: Path) -> tuple[str, str]:
    name = path.name.lower()
    parts = name.split("_bars_")
    if len(parts) == 2:
        symbol = parts[0]
        tf = parts[1].split("_snapshot")[0]
        return symbol.upper(), tf
    return "UNKNOWN", "5m"


def _read_jsonl_tail(path: Path, limit: int) -> pd.DataFrame:
    buf: deque[dict[str, Any]] = deque(maxlen=max(1, int(limit)))
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                buf.append(row)

    df = pd.DataFrame(list(buf))
    if df.empty:
        return df

    if "open_time" not in df.columns:
        return pd.DataFrame()

    open_time = pd.to_numeric(df["open_time"], errors="coerce")
    df["open_time"] = open_time
    df["timestamp"] = pd.to_datetime(open_time, unit="ms", errors="coerce", utc=True)
    df = df.dropna(subset=["open_time", "timestamp"]).copy()
    if df.empty:
        return pd.DataFrame()
    df = df.sort_values("open_time", kind="stable").reset_index(drop=True)
    return df


def _slice_by_end_ms(df: pd.DataFrame, *, end_ms: int, tail: int) -> pd.DataFrame:
    part = df[df["open_time"] <= int(end_ms)]
    if part.empty:
        return part
    if tail > 0 and len(part) > tail:
        part = part.iloc[-tail:]
    return part.copy()


def _md_escape(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


@dataclass(frozen=True, slots=True)
class _Row:
    idx: int
    ts: str
    last_close: float
    in_play: bool
    ref: str
    ref_detail: str
    poi_count: int
    targets_count: int
    radius: float | None
    events_compact: str


def _fmt_ts(ts: Any) -> str:
    if isinstance(ts, pd.Timestamp):
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.isoformat()
    try:
        t2 = pd.to_datetime(ts, utc=True, errors="coerce")
        if isinstance(t2, pd.Timestamp) and not pd.isna(t2):
            return t2.isoformat()
    except Exception:
        pass
    return "-"


def _compact_events(events: Any) -> str:
    if not isinstance(events, list) or not events:
        return "-"
    parts: list[str] = []
    for ev in events:
        try:
            et = str(getattr(ev, "event_type", ""))
            direction = str(getattr(ev, "direction", ""))
            level = getattr(ev, "level", None)
            level_s = f"@{float(level):.2f}" if isinstance(level, (int, float)) else ""
            parts.append(f"{et}:{direction}{level_s}")
        except Exception:
            continue
    return _md_escape(", ".join(parts) if parts else "-")


def _ref_detail(meta: dict[str, Any]) -> tuple[str, str]:
    ref_obj = meta.get("in_play_ref")
    if not isinstance(ref_obj, dict):
        return "NONE", "-"
    ref = str(ref_obj.get("ref") or "NONE")
    if ref == "POI":
        zid = ref_obj.get("poi_zone_id")
        pmin = ref_obj.get("poi_min")
        pmax = ref_obj.get("poi_max")
        zid_s = str(zid) if zid else "-"
        if isinstance(pmin, (int, float)) and isinstance(pmax, (int, float)):
            return ref, _md_escape(f"id={zid_s} [{float(pmin):.2f},{float(pmax):.2f}]")
        return ref, _md_escape(f"id={zid_s}")
    if ref == "TARGET":
        lvl = ref_obj.get("level")
        if isinstance(lvl, (int, float)):
            return ref, _md_escape(f"level={float(lvl):.2f}")
        return ref, "level=?"
    return ref, "-"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="QA журнал Stage5 execution на снапшоті jsonl.",
    )
    p.add_argument(
        "--path",
        required=True,
        help="Шлях до *_snapshot.jsonl (або ім'я з datastore/).",
    )
    p.add_argument(
        "--steps",
        type=int,
        default=240,
        help="Скільки останніх 5m-кроків прогнати (після warmup).",
    )
    p.add_argument(
        "--warmup",
        type=int,
        default=220,
        help="Скільки 5m барів пропустити на старті (щоб було достатньо історії).",
    )
    p.add_argument(
        "--limit-5m",
        type=int,
        default=900,
        help="Скільки 5m барів читати з jsonl (tail).",
    )
    p.add_argument(
        "--limit-1m",
        type=int,
        default=9000,
        help="Скільки 1m барів читати з jsonl (tail).",
    )
    p.add_argument(
        "--limit-1h",
        type=int,
        default=3000,
        help="Скільки 1h барів читати з jsonl (tail).",
    )
    p.add_argument(
        "--limit-4h",
        type=int,
        default=2000,
        help="Скільки 4h барів читати з jsonl (tail).",
    )
    p.add_argument(
        "--out",
        default=None,
        help="Куди писати Markdown (за замовчуванням: reports/execution_journal_<symbol>_<ts>.md).",
    )

    # Конфіг Stage5 (щоб журнал відображав реальні налаштування).
    p.add_argument(
        "--radius-atr", type=float, default=0.9, help="exec_in_play_radius_atr"
    )
    p.add_argument("--hold-bars", type=int, default=0, help="exec_in_play_hold_bars")
    p.add_argument(
        "--impulse-atr-mul", type=float, default=0.0, help="exec_impulse_atr_mul"
    )
    p.add_argument(
        "--micro-pivot-bars", type=int, default=8, help="exec_micro_pivot_bars"
    )
    p.add_argument("--max-events", type=int, default=6, help="exec_max_events")

    p.add_argument(
        "--only-in-play",
        action="store_true",
        help="У головній таблиці показувати лише кроки з in_play=True.",
    )
    p.add_argument(
        "--only-events",
        action="store_true",
        help="У головній таблиці показувати лише кроки, де є execution_events.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _ensure_repo_on_syspath()

    from smc_core import SmcCoreConfig, SmcCoreEngine, SmcInput
    from smc_core.input_adapter import _build_sessions_context

    args = parse_args(argv)

    path5 = _resolve_snapshot_path(args.path)
    if not path5.exists():
        raise SystemExit(f"Не знайдено файл: {path5}")

    symbol, tf_primary = _infer_symbol_tf(path5)
    if tf_primary != "5m":
        tf_primary = "5m"

    # Поруч очікуємо 1m/1h/4h снапшоти за шаблоном.
    repo_root = Path(__file__).resolve().parents[1]
    symbol_l = symbol.lower()
    path1 = repo_root / "datastore" / f"{symbol_l}_bars_1m_snapshot.jsonl"
    path1h = repo_root / "datastore" / f"{symbol_l}_bars_1h_snapshot.jsonl"
    path4h = repo_root / "datastore" / f"{symbol_l}_bars_4h_snapshot.jsonl"

    if not path1.exists():
        raise SystemExit(f"Не знайдено 1m снапшот: {path1}")
    if not path1h.exists():
        raise SystemExit(f"Не знайдено 1h снапшот: {path1h}")
    if not path4h.exists():
        raise SystemExit(f"Не знайдено 4h снапшот: {path4h}")

    df5 = _read_jsonl_tail(path5, int(args.limit_5m))
    df1 = _read_jsonl_tail(path1, int(args.limit_1m))
    df1h = _read_jsonl_tail(path1h, int(args.limit_1h))
    df4h = _read_jsonl_tail(path4h, int(args.limit_4h))

    if df5.empty or df1.empty:
        raise SystemExit("Порожні дані (5m або 1m).")

    cfg = SmcCoreConfig(
        exec_enabled=True,
        exec_tf="1m",
        exec_in_play_radius_atr=float(args.radius_atr),
        exec_in_play_hold_bars=int(args.hold_bars),
        exec_impulse_atr_mul=float(args.impulse_atr_mul),
        exec_micro_pivot_bars=int(args.micro_pivot_bars),
        exec_max_events=int(args.max_events),
    )
    engine = SmcCoreEngine(cfg=cfg)

    steps = int(args.steps)
    warmup = int(args.warmup)
    start = max(warmup, len(df5) - steps)

    checked = 0
    in_play_true = 0
    steps_with_events = 0
    ref_counts: Counter[str] = Counter()
    etype_total: Counter[str] = Counter()

    rows: list[_Row] = []

    for i in range(start, len(df5)):
        row5 = df5.iloc[i]
        end_ms = int(row5["open_time"]) + 5 * 60 * 1000 - 1

        f5 = df5.iloc[max(0, i - 320) : i + 1].copy()
        f1 = _slice_by_end_ms(df1, end_ms=end_ms, tail=1800)
        f1h_i = _slice_by_end_ms(df1h, end_ms=end_ms, tail=800)
        f4h_i = _slice_by_end_ms(df4h, end_ms=end_ms, tail=400)

        if len(f1) < 50:
            continue

        ohlc_by_tf = {"5m": f5, "1m": f1, "1h": f1h_i, "4h": f4h_i}
        ctx = _build_sessions_context(ohlc_by_tf=ohlc_by_tf, tf_primary="5m")

        snap = SmcInput(
            symbol=symbol,
            tf_primary="5m",
            ohlc_by_tf=ohlc_by_tf,
            context=ctx,
        )

        hint = engine.process_snapshot(snap)
        ex = hint.execution
        if ex is None:
            continue

        checked += 1
        meta: dict[str, Any] = dict(ex.meta or {})
        in_play = bool(meta.get("in_play"))
        if in_play:
            in_play_true += 1

        ref, detail = _ref_detail(meta)
        ref_counts[ref] += 1

        events = list(ex.execution_events or [])
        if events:
            steps_with_events += 1
        for ev in events:
            et = str(getattr(ev, "event_type", ""))
            if et:
                etype_total[et] += 1

        last_ts = f5["timestamp"].iloc[-1] if "timestamp" in f5.columns else None
        ts_s = _fmt_ts(last_ts)
        last_close = float(f1["close"].iloc[-1])
        radius = meta.get("radius")
        radius_f = float(radius) if isinstance(radius, (int, float)) else None

        r = _Row(
            idx=checked,
            ts=ts_s,
            last_close=float(last_close),
            in_play=bool(in_play),
            ref=str(ref),
            ref_detail=str(detail),
            poi_count=int(meta.get("poi_count") or 0),
            targets_count=int(meta.get("targets_count") or 0),
            radius=radius_f,
            events_compact=_compact_events(events),
        )

        # фільтри для основної таблиці
        if args.only_in_play and not r.in_play:
            continue
        if args.only_events and r.events_compact == "-":
            continue
        rows.append(r)

    in_play_rate = in_play_true / max(1, checked)
    events_rate = steps_with_events / max(1, checked)

    now = datetime.now(tz=UTC)
    ts_tag = now.strftime("%Y%m%d_%H%M%S")

    out_path: Path
    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = repo_root / out_path
    else:
        out_path = repo_root / "reports" / f"execution_journal_{symbol_l}_{ts_tag}.md"

    out_path.parent.mkdir(parents=True, exist_ok=True)

    def md_table(title: str, items: list[_Row]) -> str:
        lines: list[str] = []
        lines.append(f"## {title}")
        lines.append("")
        lines.append(
            "| # | ts | close(1m) | in_play | ref | ref_detail | poi | targets | radius | events |"
        )
        lines.append("|---:|---|---:|:---:|:---:|---|---:|---:|---:|---|")
        for rr in items:
            radius_s = f"{rr.radius:.4f}" if rr.radius is not None else "-"
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(rr.idx),
                        _md_escape(rr.ts),
                        f"{rr.last_close:.2f}",
                        "✅" if rr.in_play else "—",
                        _md_escape(rr.ref),
                        _md_escape(rr.ref_detail),
                        str(rr.poi_count),
                        str(rr.targets_count),
                        radius_s,
                        rr.events_compact,
                    ]
                )
                + " |"
            )
        lines.append("")
        return "\n".join(lines)

    # Для швидкої валідації: окремі таблиці.
    rows_all: list[_Row] = []
    rows_in_play: list[_Row] = []
    rows_events: list[_Row] = []

    # Якщо main rows відфільтрований — ці секції все одно робимо з "сирих" метрик.
    # Тому прогонимо ще раз компактно для full-списків (без повторного engine),
    # використовуючи вже зібрані rows як базу там, де це можливо.
    # Компроміс: якщо користувач поставив only_* фільтри, "Журнал (усі)" не обіцяємо.
    if not args.only_in_play and not args.only_events:
        rows_all = list(rows)

    # Для in_play та events секцій формуємо з rows (які вже можуть бути відфільтровані);
    # але якщо фільтр не заданий, то це коректно.
    rows_in_play = [r for r in rows if r.in_play]
    rows_events = [r for r in rows if r.events_compact != "-"]

    report: list[str] = []
    report.append(f"# QA журнал Stage5 Execution — {symbol}")
    report.append("")
    report.append(f"Дата (UTC): {now.isoformat()}")
    report.append("")

    report.append("## Параметри")
    report.append("")
    report.append(f"- Снапшот 5m: `{path5}`")
    report.append(f"- Кроків перевірено: **{checked}**")
    report.append(f"- in_play_rate: **{in_play_rate:.3f}** ({in_play_true}/{checked})")
    report.append(
        f"- events_rate: **{events_rate:.3f}** ({steps_with_events}/{checked})"
    )
    report.append("")
    report.append("### Конфіг Stage5")
    report.append("")
    report.append(
        "- "
        + "; ".join(
            [
                f"radius_atr={cfg.exec_in_play_radius_atr}",
                f"hold_bars={cfg.exec_in_play_hold_bars}",
                f"impulse_atr_mul={cfg.exec_impulse_atr_mul}",
                f"micro_pivot_bars={cfg.exec_micro_pivot_bars}",
                f"max_events={cfg.exec_max_events}",
            ]
        )
    )
    report.append("")

    report.append("## Розподіл in_play_ref")
    report.append("")
    report.append("| ref | count |")
    report.append("|---|---:|")
    for k, v in ref_counts.most_common():
        report.append(f"| {_md_escape(k)} | {v} |")
    report.append("")

    report.append("## Розподіл типів подій (total)")
    report.append("")
    report.append("| event_type | count |")
    report.append("|---|---:|")
    if etype_total:
        for k, v in etype_total.most_common():
            report.append(f"| {_md_escape(k)} | {v} |")
    else:
        report.append("| - | 0 |")
    report.append("")

    if rows_all:
        report.append(md_table("Журнал (усі кроки)", rows_all))
    report.append(md_table("Журнал (лише in_play=True)", rows_in_play))
    report.append(md_table("Журнал (лише кроки з подіями)", rows_events))

    out_path.write_text("\n".join(report), encoding="utf-8")

    print(str(out_path))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
