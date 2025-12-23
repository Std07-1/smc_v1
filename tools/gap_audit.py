"""Аудит гепів у OHLCV (1m/5m) для live-режиму.

Мета: швидко довести/спростувати, що в хвості історії немає розривів часу
між свічками (гепів), зворотних кроків часу та підозрілих дублікатів.

Це утиліта для ручного запуску під час стріму.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from redis.asyncio import Redis

# Важливо: при запуску як `python tools/gap_audit.py` sys.path[0] = tools/,
# тому треба явно додати корінь репозиторію, інакше `import app/...` впаде.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.settings import Settings
from data.unified_store import StoreConfig, UnifiedDataStore, _timeframe_to_ms


@dataclass(frozen=True, slots=True)
class GapFinding:
    idx: int
    prev_open_time: int
    curr_open_time: int
    delta_ms: int


@dataclass(frozen=True, slots=True)
class GapReport:
    symbol: str
    timeframe: str
    expected_step_ms: int
    rows: int
    non_monotonic: int
    duplicates: int
    gaps: int
    max_gap_ms: int | None
    findings: tuple[GapFinding, ...]

    @property
    def is_ok(self) -> bool:
        return self.non_monotonic == 0 and self.gaps == 0


def _to_int_ms(value: object) -> int | None:
    if value is None:
        return None
    try:
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            s = value.strip()
            return int(float(s)) if s else None
        # pandas / numpy scalar
        return int(float(value))  # type: ignore[arg-type]
    except Exception:
        return None


def analyze_open_times(
    open_times_ms: Iterable[int],
    *,
    expected_step_ms: int,
    max_findings: int = 20,
) -> GapReport:
    # Очікуємо послідовність у хронологічному порядку (UDS її відсортовує).
    # Тут навмисно НЕ сортуємо, щоб ловити non_monotonic (кроки назад у часі).
    seq = [int(x) for x in open_times_ms]
    if not seq:
        return GapReport(
            symbol="",
            timeframe="",
            expected_step_ms=expected_step_ms,
            rows=0,
            non_monotonic=0,
            duplicates=0,
            gaps=0,
            max_gap_ms=None,
            findings=(),
        )

    non_monotonic = 0
    duplicates = 0
    gaps = 0
    max_gap_ms: int | None = None
    findings: list[GapFinding] = []

    prev = seq[0]
    for i, curr in enumerate(seq[1:], start=1):
        delta = int(curr - prev)
        if delta < 0:
            non_monotonic += 1
            if len(findings) < max_findings:
                findings.append(
                    GapFinding(
                        idx=i,
                        prev_open_time=int(prev),
                        curr_open_time=int(curr),
                        delta_ms=delta,
                    )
                )
        elif delta == 0:
            duplicates += 1
        elif delta != expected_step_ms:
            gaps += 1
            if max_gap_ms is None or delta > max_gap_ms:
                max_gap_ms = delta
            if len(findings) < max_findings:
                findings.append(
                    GapFinding(
                        idx=i,
                        prev_open_time=int(prev),
                        curr_open_time=int(curr),
                        delta_ms=delta,
                    )
                )
        prev = curr

    return GapReport(
        symbol="",
        timeframe="",
        expected_step_ms=expected_step_ms,
        rows=len(seq),
        non_monotonic=non_monotonic,
        duplicates=duplicates,
        gaps=gaps,
        max_gap_ms=max_gap_ms,
        findings=tuple(findings),
    )


def analyze_df(
    df: pd.DataFrame,
    *,
    symbol: str,
    timeframe: str,
    expected_step_ms: int,
    max_findings: int = 20,
) -> GapReport:
    if df is None or df.empty or "open_time" not in df.columns:
        base = analyze_open_times([], expected_step_ms=expected_step_ms)
        return GapReport(
            symbol=symbol,
            timeframe=timeframe,
            expected_step_ms=expected_step_ms,
            rows=0,
            non_monotonic=base.non_monotonic,
            duplicates=base.duplicates,
            gaps=base.gaps,
            max_gap_ms=base.max_gap_ms,
            findings=base.findings,
        )

    open_times = []
    for v in df["open_time"].tolist():
        t = _to_int_ms(v)
        if t is not None:
            open_times.append(t)

    base = analyze_open_times(
        open_times, expected_step_ms=expected_step_ms, max_findings=max_findings
    )
    return GapReport(
        symbol=symbol,
        timeframe=timeframe,
        expected_step_ms=expected_step_ms,
        rows=base.rows,
        non_monotonic=base.non_monotonic,
        duplicates=base.duplicates,
        gaps=base.gaps,
        max_gap_ms=base.max_gap_ms,
        findings=base.findings,
    )


def _fmt_ms(ms: int | None) -> str:
    if ms is None:
        return "-"
    if ms < 1000:
        return f"{ms}ms"
    if ms < 60_000:
        return f"{ms/1000:.1f}s"
    return f"{ms/60_000:.2f}m"


def print_report(report: GapReport) -> None:
    print(
        f"[gap_audit] {report.symbol} {report.timeframe}: rows={report.rows} "
        f"gaps={report.gaps} non_monotonic={report.non_monotonic} "
        f"duplicates={report.duplicates} max_gap={_fmt_ms(report.max_gap_ms)}"
    )
    if report.findings:
        print("[gap_audit] Перші аномалії (idx, prev, curr, delta_ms):")
        for f in report.findings:
            print(
                f"  - {f.idx}: {f.prev_open_time} -> {f.curr_open_time} "
                f"(Δ={f.delta_ms})"
            )


async def _run_for_tf(
    store: UnifiedDataStore,
    *,
    symbol: str,
    timeframe: str,
    limit: int,
    max_findings: int,
) -> GapReport:
    tf_ms = _timeframe_to_ms(timeframe)
    if not tf_ms:
        raise ValueError(f"Невідомий TF: {timeframe}")

    df = await store.get_df(symbol, timeframe, limit=limit)
    rep = analyze_df(
        df,
        symbol=symbol,
        timeframe=timeframe,
        expected_step_ms=int(tf_ms),
        max_findings=max_findings,
    )
    return rep


async def main_async(args: argparse.Namespace) -> int:
    settings = Settings()

    redis = Redis(host=settings.redis_host, port=settings.redis_port)
    cfg = StoreConfig(
        validate_on_read=False,
        validate_on_write=False,
        write_behind=False,
    )
    store = UnifiedDataStore(redis=redis, cfg=cfg)

    t0 = time.perf_counter()
    reports: list[GapReport] = []
    try:
        for tf in args.timeframes:
            rep = await _run_for_tf(
                store,
                symbol=args.symbol,
                timeframe=tf,
                limit=args.limit,
                max_findings=args.max_findings,
            )
            reports.append(rep)
            print_report(rep)
    finally:
        try:
            await redis.close()
        except Exception:
            pass

    dt = time.perf_counter() - t0
    ok = all(r.is_ok for r in reports)
    print(f"[gap_audit] Час виконання: {dt:.3f}s; OK={ok}")
    return 0 if ok else 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="gap_audit",
        description="Аудит гепів/дір у 1m/5m (строгий крок TF).",
    )
    p.add_argument("--symbol", required=True, help="Напр. xauusd")
    p.add_argument(
        "--timeframes",
        nargs="+",
        default=["1m", "5m"],
        help="Список TF (за замовчуванням: 1m 5m)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=2000,
        help="Скільки останніх барів брати з UDS",
    )
    p.add_argument(
        "--max-findings",
        type=int,
        default=20,
        help="Максимум аномалій для виводу",
    )
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        code = asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("[gap_audit] Перервано користувачем")
        raise
    except Exception as e:
        print(f"[gap_audit] Помилка: {e}")
        sys.exit(1)
    sys.exit(code)


if __name__ == "__main__":
    main()
