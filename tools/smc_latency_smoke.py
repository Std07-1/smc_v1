"""SMC latency smoke tool (Wave F3).

Ціль: перед деплоєм/канарейкою швидко поміряти wall-time циклу SMC без CI-гейту.

Вивід:
- p50/p75/p95 (мс) по викликах `build_smc_input_from_store` + `SmcCoreEngine.process_snapshot`;
- лічильники `no_data` (немає кадру для tf_primary) та `exceptions`.

Інструмент не змінює runtime-поведінку прод-пайплайна: це окремий tools/* скрипт.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING, cast

import pandas as pd

if TYPE_CHECKING:
    from data.unified_store import UnifiedDataStore

# Важливо: при запуску як `python tools/smc_latency_smoke.py` sys.path[0] = tools/,
# тому корінь репо не видно. Додаємо repo-root явно (інструмент, не runtime).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from smc_core.engine import SmcCoreEngine
from smc_core.input_adapter import build_smc_input_from_store


@dataclass(slots=True)
class SmokeResult:
    samples: int
    p50_ms: float | None
    p75_ms: float | None
    p95_ms: float | None
    no_data: int
    exceptions: int


class _SnapshotStore:
    """Мінімальний async-store для build_smc_input_from_store.

    Повертає preloaded DataFrame з кеша (без I/O у циклі).
    """

    def __init__(self, frames: dict[tuple[str, str], pd.DataFrame]) -> None:
        self._frames = frames

    async def get_df(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        key = (str(symbol).lower(), str(timeframe))
        df = self._frames.get(key)
        if df is None or df.empty:
            return pd.DataFrame()
        if limit and limit > 0 and len(df) > limit:
            return df.tail(int(limit)).copy()
        return df.copy()


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    if p <= 0:
        return float(min(values))
    if p >= 1:
        return float(max(values))

    xs = sorted(values)
    n = len(xs)
    if n == 1:
        return float(xs[0])

    pos = p * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return float(xs[lo] * (1.0 - frac) + xs[hi] * frac)


def _parse_symbols(raw: str) -> list[str]:
    parts = [p.strip().lower() for p in (raw or "").split(",")]
    return [p for p in parts if p]


def _parse_tfs(raw: str) -> list[str]:
    parts = [p.strip() for p in (raw or "").split(",")]
    return [p for p in parts if p]


def _candidate_snapshot_paths(base_dir: str, symbol: str, tf: str) -> list[str]:
    sym = str(symbol).lower()
    # У repo є кілька варіантів, наприклад xauusd_bars_1h_snapshot.jsonl,
    # xauusd_bars_1h_last7d_snapshot.jsonl — пробуємо найбільш канонічний.
    return [
        os.path.join(base_dir, f"{sym}_bars_{tf}_snapshot.jsonl"),
        os.path.join(base_dir, f"{sym}_bars_{tf}_snapshot.json"),
    ]


def _load_snapshot_df(base_dir: str, symbol: str, tf: str) -> pd.DataFrame:
    for path in _candidate_snapshot_paths(base_dir, symbol, tf):
        if not os.path.exists(path):
            continue
        try:
            if path.endswith(".jsonl"):
                df = pd.read_json(path, orient="records", lines=True)
            else:
                df = pd.read_json(path)
        except Exception:
            return pd.DataFrame()

        if df is None or df.empty:
            return pd.DataFrame()

        # Мінімальний guard: інструмент не виправляє дані, лише читає.
        if "open_time" in df.columns:
            df = df.sort_values("open_time", kind="stable")
        return df.reset_index(drop=True)

    return pd.DataFrame()


async def _run_smoke(
    *,
    symbols: list[str],
    tf_primary: str,
    tfs_extra: list[str],
    limit: int,
    cycles: int,
    base_dir: str,
) -> SmokeResult:
    # preload
    frames: dict[tuple[str, str], pd.DataFrame] = {}
    for sym in symbols:
        for tf in [tf_primary, *tfs_extra]:
            frames[(sym, tf)] = _load_snapshot_df(base_dir, sym, tf)

    store = _SnapshotStore(frames)
    engine = SmcCoreEngine()

    samples_ms: list[float] = []
    no_data = 0
    exceptions = 0

    for _ in range(max(0, int(cycles))):
        for sym in symbols:
            t0 = time.perf_counter()
            try:
                smc_input = await build_smc_input_from_store(
                    cast("UnifiedDataStore", store),
                    sym,
                    tf_primary,
                    tfs_extra=tfs_extra,
                    limit=limit,
                    context=None,
                )
                hint = engine.process_snapshot(smc_input)
                # no_data: нема кадру для tf_primary (або він порожній після нормалізації)
                frame = smc_input.ohlc_by_tf.get(tf_primary)
                if frame is None or frame.empty:
                    no_data += 1
                # best-effort використання результату, щоб уникнути мертвого коду
                _ = hint.meta.get("snapshot_tf")
            except Exception:
                exceptions += 1
            finally:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                samples_ms.append(elapsed_ms)

    return SmokeResult(
        samples=len(samples_ms),
        p50_ms=_percentile(samples_ms, 0.50),
        p75_ms=_percentile(samples_ms, 0.75),
        p95_ms=_percentile(samples_ms, 0.95),
        no_data=no_data,
        exceptions=exceptions,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SMC latency smoke: p50/p75/p95 + no_data/exceptions"
    )
    parser.add_argument(
        "--symbols",
        default="xauusd",
        help="Символи через кому (напр. xauusd,eurusd)",
    )
    parser.add_argument(
        "--tf",
        dest="tf_primary",
        default="5m",
        help="tf_primary (за замовчуванням 5m)",
    )
    parser.add_argument(
        "--extra",
        default="1m,15m,1h",
        help="Додаткові TF через кому (за замовчуванням 1m,15m,1h)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Скільки барів брати (tail) з кожного TF",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=200,
        help="Кількість циклів (кожен цикл = 1 виклик на кожен symbol)",
    )
    parser.add_argument(
        "--datastore-dir",
        default=os.path.join(os.path.dirname(__file__), "..", "datastore"),
        help="Каталог зі snapshot JSONL (за замовчуванням ./datastore)",
    )

    args = parser.parse_args()

    symbols = _parse_symbols(args.symbols)
    if not symbols:
        print("[SMC][LATENCY] Помилка: symbols порожній")
        return 2

    tfs_extra = _parse_tfs(args.extra)
    tf_primary = str(args.tf_primary)

    base_dir = os.path.abspath(str(args.datastore_dir))

    result = asyncio.run(
        _run_smoke(
            symbols=symbols,
            tf_primary=tf_primary,
            tfs_extra=tfs_extra,
            limit=int(args.limit),
            cycles=int(args.cycles),
            base_dir=base_dir,
        )
    )

    print("[SMC][LATENCY] smoke завершено")
    print(
        f"  symbols={symbols} tf_primary={tf_primary} extra={tfs_extra} limit={int(args.limit)}"
    )
    print(f"  datastore_dir={base_dir}")
    print(
        f"  samples={result.samples} no_data={result.no_data} exceptions={result.exceptions}"
    )
    if result.p50_ms is not None:
        print(
            f"  p50_ms={result.p50_ms:.2f} p75_ms={result.p75_ms:.2f} p95_ms={result.p95_ms:.2f}"
        )
    else:
        print("  p50_ms=- p75_ms=- p95_ms=-")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
