"""CLI-скрипт для перевірки пропусків (gaps) в OHLCV-історії з UnifiedDataStore.

MVP-мета:
- підтвердити, що у live-частині немає пропусків більших за TF;
- показати максимальний gap і кількість gap-ів > 1 бару.

Приклад:
    python -m tools.uds_ohlcv_gap_check --symbol XAUUSD --tf 1m --from 2025-12-11 --to 2025-12-12
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from redis.asyncio import Redis

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from app.settings import load_datastore_cfg, settings
from data.unified_store import StoreConfig, StoreProfile, UnifiedDataStore


@dataclass(frozen=True)
class GapStat:
    prev_open_time_ms: int
    curr_open_time_ms: int
    delta_ms: int


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Перевіряє gaps у OHLCV (open_time) з UnifiedDataStore",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--symbol", required=True, help="Символ, напр. XAUUSD")
    parser.add_argument("--tf", required=True, help="Таймфрейм, напр. 1m або 5m")
    parser.add_argument(
        "--from",
        dest="from_dt",
        required=True,
        help="Початок діапазону (YYYY-MM-DD або ISO datetime)",
    )
    parser.add_argument(
        "--to",
        dest="to_dt",
        required=True,
        help="Кінець діапазону (YYYY-MM-DD або ISO datetime). Для дати — інтерпретується як кінець дня (exclusive: +1d).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Скільки найбільших gap-ів показати",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Опціональний ліміт барів при читанні з UDS (за замовчуванням — розраховується з діапазону)",
    )
    parser.add_argument(
        "--snapshot-file",
        default=None,
        help="Опційно: шлях до локального jsonl snapshot-файла (режим без Redis/UDS)",
    )
    return parser.parse_args(argv)


def _parse_tf_ms(tf: str) -> int:
    token = (tf or "").strip().lower()
    match = re.fullmatch(r"(\d+)([mhd])", token)
    if not match:
        raise ValueError(f"Невідомий формат tf: {tf!r}")
    value = int(match.group(1))
    unit = match.group(2)
    if unit == "m":
        return value * 60_000
    if unit == "h":
        return value * 60 * 60_000
    if unit == "d":
        return value * 24 * 60 * 60_000
    raise ValueError(f"Непідтримуваний суфікс tf: {tf!r}")


def _parse_utc_dt(value: str) -> datetime:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("Порожня дата")

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        dt = datetime.fromisoformat(raw)
        return dt.replace(tzinfo=UTC)

    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _coerce_int_ms(value: Any) -> int | None:
    if value is None:
        return None
    try:
        if isinstance(value, pd.Timestamp):
            return int(value.timestamp() * 1000)
        num = int(value)
        return num
    except Exception:
        return None


def _read_open_times_from_jsonl_snapshot(path: Path) -> list[int]:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"snapshot-file не знайдено: {path}")

    out: list[int] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue
            ms = _coerce_int_ms(obj.get("open_time"))
            if ms is None:
                continue
            out.append(ms)
    out = sorted(set(out))
    return out


async def _init_store() -> tuple[UnifiedDataStore, Redis]:
    cfg = load_datastore_cfg()
    redis = Redis(host=settings.redis_host, port=settings.redis_port)
    try:
        profile_data = cfg.profile.model_dump()
    except Exception:
        profile_data = cfg.profile.dict()

    store_cfg = StoreConfig(
        namespace=cfg.namespace,
        base_dir=cfg.base_dir,
        profile=StoreProfile(**profile_data),
        intervals_ttl=cfg.intervals_ttl,
        write_behind=cfg.write_behind,
        validate_on_read=cfg.validate_on_read,
        validate_on_write=cfg.validate_on_write,
        io_retry_attempts=cfg.io_retry_attempts,
        io_retry_backoff=cfg.io_retry_backoff,
    )
    store = UnifiedDataStore(redis=redis, cfg=store_cfg)
    await store.start_maintenance()
    return store, redis


async def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    symbol = str(args.symbol).strip().lower()
    tf = str(args.tf).strip().lower()
    tf_ms = _parse_tf_ms(tf)

    dt_from = _parse_utc_dt(args.from_dt)
    dt_to = _parse_utc_dt(args.to_dt)
    # Якщо to задано як дата (YYYY-MM-DD), трактуємо як кінець дня: +1d (exclusive)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.to_dt.strip()):
        dt_to = dt_to + timedelta(days=1)

    start_ms = int(dt_from.timestamp() * 1000)
    end_ms = int(dt_to.timestamp() * 1000)
    if end_ms <= start_ms:
        raise SystemExit("Некоректний діапазон: --to має бути пізніше за --from")

    # Оцінка потрібної кількості барів + запас
    estimated_bars = int((end_ms - start_ms) // tf_ms) + 5
    limit = (
        int(args.limit)
        if args.limit is not None
        else max(200, min(50_000, estimated_bars))
    )

    open_times: list[int]
    store: UnifiedDataStore | None = None
    redis: Redis | None = None
    try:
        if args.snapshot_file:
            snapshot_path = Path(args.snapshot_file).expanduser().resolve()
            open_times = _read_open_times_from_jsonl_snapshot(snapshot_path)
        else:
            store, redis = await _init_store()
            df = await store.get_df(symbol, tf, limit=limit)
            if df is None or df.empty:
                print(f"UDS повернув порожній DF для {symbol.upper()} {tf}")
                return

            if "open_time" not in df.columns:
                print(
                    "UDS DF не має колонки open_time; gap-check наразі підтримує лише open_time"
                )
                return

            # Нормалізація і фільтрація по діапазону
            work = df.copy()
            work["open_time_ms"] = pd.to_numeric(work["open_time"], errors="coerce")
            work = work.dropna(subset=["open_time_ms"]).copy()
            work["open_time_ms"] = work["open_time_ms"].astype("int64")
            work = work.sort_values("open_time_ms").reset_index(drop=True)
            open_times = work["open_time_ms"].tolist()

        # Фільтрація по діапазону (для обох режимів)
        open_times = [ms for ms in open_times if start_ms <= ms < end_ms]
        if not open_times:
            print(
                f"У вікні {dt_from.isoformat()}..{dt_to.isoformat()} немає барів для {symbol.upper()} {tf}"
            )
            return

        gaps: list[GapStat] = []
        max_delta_ms = 0
        gap_gt_1 = 0

        for prev, curr in zip(open_times, open_times[1:], strict=False):
            try:
                prev_ms = int(prev)
                curr_ms = int(curr)
            except Exception:
                continue
            delta_ms = curr_ms - prev_ms
            if delta_ms > max_delta_ms:
                max_delta_ms = delta_ms

            # missing bars: якщо між open_time більше ніж 1 TF
            missing_bars = max(0, (delta_ms // tf_ms) - 1)
            if missing_bars > 0:
                gap_gt_1 += 1
                gaps.append(
                    GapStat(
                        prev_open_time_ms=prev_ms,
                        curr_open_time_ms=curr_ms,
                        delta_ms=delta_ms,
                    )
                )

        max_gap_bars = max(0, (max_delta_ms // tf_ms) - 1)
        print(f"Symbol: {symbol.upper()}  TF: {tf}  tf_ms={tf_ms}")
        print(f"Window: {dt_from.isoformat()} .. {dt_to.isoformat()} (exclusive)")
        print(f"Bars in window: {len(open_times)}")
        print(
            f"Max delta(open_time): {max_delta_ms} ms  (~{max_delta_ms / 60000:.2f} min)"
        )
        print(f"Max gap (missing bars): {max_gap_bars}")
        print(f"Gaps with missing_bars>=1: {gap_gt_1}")

        top_n = max(0, int(args.top))
        if top_n and gaps:
            print(f"\nTop {min(top_n, len(gaps))} gaps:")
            for item in sorted(gaps, key=lambda x: x.delta_ms, reverse=True)[:top_n]:
                missing_bars = max(0, (item.delta_ms // tf_ms) - 1)
                prev_dt = datetime.fromtimestamp(item.prev_open_time_ms / 1000, tz=UTC)
                curr_dt = datetime.fromtimestamp(item.curr_open_time_ms / 1000, tz=UTC)
                print(
                    f"- {prev_dt.isoformat()} -> {curr_dt.isoformat()}  delta={item.delta_ms}ms  missing={missing_bars}"
                )

        # MVP-критерій з опису: у live-частині max gap <= tf
        if max_delta_ms <= tf_ms:
            print("\nOK: max delta у вікні <= tf")
        else:
            print(
                "\nWARN: max delta у вікні > tf (можливий пропуск у live або відсутня історія)"
            )
    finally:
        if store is not None:
            await store.stop_maintenance()
        if redis is not None:
            await redis.close()


if __name__ == "__main__":
    asyncio.run(main())
