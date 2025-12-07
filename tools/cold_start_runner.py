"""CLI-утиліта для cold-start аудиту кешів UnifiedDataStore."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from dotenv import load_dotenv
from redis.asyncio import Redis

from app.cold_start import (
    build_cold_start_report_payload,
    format_cold_report_table,
    persist_cold_start_report,
)
from app.settings import load_datastore_cfg, settings
from config.config import FXCM_FAST_SYMBOLS, SCREENING_LOOKBACK
from data.unified_store import (
    ColdStartCacheEntry,
    StoreConfig,
    StoreProfile,
    UnifiedDataStore,
)

logger = logging.getLogger("tools.cold_start_runner")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def _normalize_symbols(symbols: Sequence[str] | None) -> list[str]:
    raw = symbols if symbols else FXCM_FAST_SYMBOLS
    uniq: list[str] = []
    seen: set[str] = set()
    for sym in raw:
        if not sym:
            continue
        key = str(sym).lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(key)
    return uniq


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cold-start аудит кешів Stage1 через UnifiedDataStore",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--symbols",
        nargs="*",
        help="Список символів (якщо не вказано — FXCM_FAST_SYMBOLS з config)",
    )
    parser.add_argument(
        "--interval",
        default="1m",
        help="Таймфрейм для перевірки",
    )
    parser.add_argument(
        "--min-rows",
        type=int,
        default=SCREENING_LOOKBACK,
        help="Мінімальна кількість барів, яка вважається " "достатньою для warm start",
    )
    parser.add_argument(
        "--stale-threshold",
        type=int,
        default=3600,
        help="Макс. допустима "
        "давність останнього бару (сек) перед позначенням як stale",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Файл для збереження JSON-звіту",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Виводити JSON у stdout (інакше — таблицю)",
    )
    return parser.parse_args(args=argv)


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
    datastore = UnifiedDataStore(redis=redis, cfg=store_cfg)
    await datastore.start_maintenance()
    return datastore, redis


async def run_audit(
    args: argparse.Namespace,
) -> tuple[dict[str, object], list[ColdStartCacheEntry]]:
    symbols = _normalize_symbols(args.symbols)
    if not symbols:
        raise ValueError("Список символів порожній")

    datastore, redis = await _init_store()
    try:
        payload, report = await build_cold_start_report_payload(
            datastore,
            symbols,
            interval=args.interval,
            min_rows=max(1, args.min_rows),
            stale_threshold=max(1, args.stale_threshold),
        )
    finally:
        await datastore.stop_maintenance()
        await redis.close()
    return payload, report


def _emit_output(
    data: dict[str, object],
    report: Sequence[ColdStartCacheEntry],
    args: argparse.Namespace,
) -> None:
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        table = format_cold_report_table(report)
        print(table)
        print()
        print(json.dumps(data["summary"], ensure_ascii=False, indent=2))

    if args.output:
        path = persist_cold_start_report(Path(args.output), data)
        logger.info("JSON-звіт збережено у %s", path)


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    args = parse_args(argv)
    try:
        data, report = asyncio.run(run_audit(args))
    except KeyboardInterrupt:
        logger.warning("Перервано користувачем")
        return 130
    except Exception as exc:
        logger.error("Помилка cold_start_runner: %s", exc, exc_info=True)
        return 1
    _emit_output(data, report, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
