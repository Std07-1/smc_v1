"""Етап 1: інструмент перевірки покриття OHLCV по TF (без гепів).

Ціль:
- швидко перевірити, що для контрольного вікна по (symbol, tf) немає пропусків
  між `open_time` значеннями (крок = timeframe_ms).

Джерело даних:
- snapshot JSONL файли UnifiedDataStore у `config.datastore.yaml: base_dir`.

Приклад:
    C:/Aione_projects/smc_v1/.venv/Scripts/python.exe -m tools.tf_coverage_report \
        --symbol xauusd --tfs 1m 5m 1h 4h --window-minutes 720

Exit codes:
- 0: гепів немає по всіх TF
- 1: немає файлів/даних для хоча б одного TF
- 2: знайдені гепи хоча б для одного TF
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TF_TO_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


def _timeframe_to_ms(tf: str) -> int | None:
    key = str(tf or "").strip().lower()
    return _TF_TO_MS.get(key)


@dataclass(frozen=True, slots=True)
class TfCoverage:
    tf: str
    has_data: bool
    bars: int
    expected: int
    coverage_pct: float
    gaps: int
    missing_bars: int
    first_open_time_ms: int | None
    last_open_time_ms: int | None
    offgrid: int


def _snapshot_path(base_dir: Path, *, symbol: str, tf: str) -> Path:
    sym = str(symbol or "").strip().lower()
    timeframe = str(tf or "").strip().lower()
    return base_dir / f"{sym}_bars_{timeframe}_snapshot.jsonl"


def _load_open_times_ms(path: Path) -> list[int]:
    out: list[int] = []
    if not path.exists():
        return out
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                value = row.get("open_time")
                if value is None:
                    continue
                try:
                    ms = int(value)
                except Exception:
                    continue
                if ms > 0:
                    out.append(ms)
    except OSError:
        return []
    return out


def compute_tf_coverage(
    open_times_ms: list[int], *, tf_ms: int, window_ms: int
) -> TfCoverage:
    """Рахує покриття у контрольному вікні, прив'язаному до tail.

    Вікно визначається як [end-window_ms, end], де end — останній open_time.
    Це робить перевірку детермінованою, без залежності від wall-clock.
    """

    tf_ms = max(1, int(tf_ms))
    window_ms = max(1, int(window_ms))

    if not open_times_ms:
        return TfCoverage(
            tf="-",
            has_data=False,
            bars=0,
            expected=0,
            coverage_pct=0.0,
            gaps=0,
            missing_bars=0,
            first_open_time_ms=None,
            last_open_time_ms=None,
            offgrid=0,
        )

    uniq = sorted({int(x) for x in open_times_ms})
    if not uniq:
        return TfCoverage(
            tf="-",
            has_data=False,
            bars=0,
            expected=0,
            coverage_pct=0.0,
            gaps=0,
            missing_bars=0,
            first_open_time_ms=None,
            last_open_time_ms=None,
            offgrid=0,
        )

    end_ms = int(uniq[-1])
    start_ms = int(end_ms - window_ms)

    in_window = [t for t in uniq if start_ms <= t <= end_ms]
    if not in_window:
        return TfCoverage(
            tf="-",
            has_data=False,
            bars=0,
            expected=0,
            coverage_pct=0.0,
            gaps=0,
            missing_bars=0,
            first_open_time_ms=None,
            last_open_time_ms=None,
            offgrid=0,
        )

    first_ms = int(in_window[0])
    last_ms = int(in_window[-1])

    expected = int(((last_ms - first_ms) // tf_ms) + 1)
    expected = max(1, expected)

    gaps = 0
    missing = 0
    offgrid = 0

    prev = in_window[0]
    for cur in in_window[1:]:
        delta = int(cur) - int(prev)
        if delta <= 0:
            prev = cur
            continue
        if delta != tf_ms:
            if delta % tf_ms != 0:
                offgrid += 1
            if delta > tf_ms:
                gaps += 1
                missing += max(0, int(delta // tf_ms) - 1)
        prev = cur

    bars = int(len(in_window))
    coverage_pct = (bars / expected) * 100.0 if expected else 0.0

    return TfCoverage(
        tf="-",
        has_data=True,
        bars=bars,
        expected=expected,
        coverage_pct=float(round(coverage_pct, 3)),
        gaps=int(gaps),
        missing_bars=int(missing),
        first_open_time_ms=first_ms,
        last_open_time_ms=last_ms,
        offgrid=int(offgrid),
    )


def _format_row(tf: str, cov: TfCoverage) -> str:
    status = "OK" if cov.has_data and cov.gaps == 0 else "BAD"
    return (
        f"{tf:>3}  {status:>3}  bars={cov.bars:<6} exp={cov.expected:<6} "
        f"cov={cov.coverage_pct:>6.2f}%  gaps={cov.gaps:<3} missing={cov.missing_bars:<6} offgrid={cov.offgrid}"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="tools.tf_coverage_report",
        description="Перевірка покриття OHLCV по TF (без гепів) на snapshot JSONL файлах.",
    )
    p.add_argument("--symbol", required=True, help="Напр. xauusd")
    p.add_argument(
        "--tfs",
        nargs="+",
        default=["1m", "5m", "1h", "4h"],
        help="Список TF, напр. 1m 5m 1h 4h",
    )
    p.add_argument(
        "--window-minutes",
        type=int,
        default=720,
        help="Контрольне вікно у хвилинах (дефолт: 720 = 12h), прив'язане до tail.",
    )
    p.add_argument(
        "--base-dir",
        default="datastore",
        help="Каталог snapshot-ів (дефолт: ./datastore)",
    )
    p.add_argument("--json", action="store_true", help="Вивести JSON замість тексту")

    args = p.parse_args(argv)

    base_dir = Path(str(args.base_dir)).expanduser().resolve()
    symbol = str(args.symbol).strip().lower()
    tfs = [str(tf).strip().lower() for tf in (args.tfs or []) if str(tf).strip()]
    window_ms = int(max(1, int(args.window_minutes))) * 60_000

    results: dict[str, Any] = {}
    missing_files = 0
    has_gaps = 0

    for tf in tfs:
        tf_ms = _timeframe_to_ms(tf)
        if not tf_ms:
            results[tf] = {"error": "unknown_tf"}
            missing_files += 1
            continue

        path = _snapshot_path(base_dir, symbol=symbol, tf=tf)
        open_times = _load_open_times_ms(path)
        if not open_times:
            results[tf] = {
                "path": str(path),
                "has_data": False,
                "bars": 0,
                "gaps": 0,
                "missing_bars": 0,
            }
            missing_files += 1
            continue

        cov = compute_tf_coverage(open_times, tf_ms=int(tf_ms), window_ms=window_ms)
        cov = TfCoverage(
            tf=tf,
            has_data=cov.has_data,
            bars=cov.bars,
            expected=cov.expected,
            coverage_pct=cov.coverage_pct,
            gaps=cov.gaps,
            missing_bars=cov.missing_bars,
            first_open_time_ms=cov.first_open_time_ms,
            last_open_time_ms=cov.last_open_time_ms,
            offgrid=cov.offgrid,
        )

        if cov.gaps > 0 or cov.missing_bars > 0:
            has_gaps += 1

        results[tf] = {
            "path": str(path),
            "has_data": cov.has_data,
            "bars": cov.bars,
            "expected": cov.expected,
            "coverage_pct": cov.coverage_pct,
            "gaps": cov.gaps,
            "missing_bars": cov.missing_bars,
            "offgrid": cov.offgrid,
            "first_open_time_ms": cov.first_open_time_ms,
            "last_open_time_ms": cov.last_open_time_ms,
        }

    if args.json:
        print(
            json.dumps(
                {
                    "symbol": symbol,
                    "window_minutes": args.window_minutes,
                    "tfs": results,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(
            f"[TF_COVERAGE] symbol={symbol} base_dir={base_dir} window_minutes={args.window_minutes}"
        )
        for tf in tfs:
            row = results.get(tf)
            if not isinstance(row, dict):
                continue
            if row.get("error"):
                print(f"{tf:>3}  ERR  {row.get('error')}")
                continue
            if not row.get("has_data"):
                print(f"{tf:>3}  ---  немає даних (file={row.get('path')})")
                continue
            cov = TfCoverage(
                tf=tf,
                has_data=True,
                bars=int(row.get("bars") or 0),
                expected=int(row.get("expected") or 0),
                coverage_pct=float(row.get("coverage_pct") or 0.0),
                gaps=int(row.get("gaps") or 0),
                missing_bars=int(row.get("missing_bars") or 0),
                first_open_time_ms=row.get("first_open_time_ms"),
                last_open_time_ms=row.get("last_open_time_ms"),
                offgrid=int(row.get("offgrid") or 0),
            )
            print(_format_row(tf, cov))

    if has_gaps > 0:
        return 2
    if missing_files > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
