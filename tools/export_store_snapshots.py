"""CLI-утиліта для вибірки діапазонів із UnifiedDataStore та експорту в snapshot-файли."""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

import pandas as pd
from redis.asyncio import Redis

from app.settings import load_datastore_cfg, settings
from data.unified_store import StoreConfig, StoreProfile, UnifiedDataStore

logger = logging.getLogger("tools.export_store_snapshots")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


@dataclass(frozen=True)
class WindowSpec:
    """Опис часового вікна для обрізання історії."""

    token: str
    minutes: int

    @property
    def label(self) -> str:
        return f"last{self.token}"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Експортує фрагменти історії з UnifiedDataStore у JSONL снапшоти",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("symbol", help="Символ, напр. XAUUSD")
    parser.add_argument(
        "--intervals",
        nargs="*",
        default=("1m", "5m", "1h"),
        help="Перелік таймфреймів для експорту",
    )
    parser.add_argument(
        "--windows",
        nargs="*",
        default=("7d", "14d", "30d"),
        help="Діапазони (7d, 2w, 1m). Використовується позначення <value><d|w|m>",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Кастомний каталог. За замовчуванням — datastore.base_dir",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Перезаписувати файли, якщо вони вже існують",
    )
    return parser.parse_args(argv)


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


def _parse_interval_minutes(raw: str) -> int:
    token = raw.strip().lower()
    match = re.fullmatch(r"(\d+)([mhd])", token)
    if not match:
        raise ValueError(f"Невідомий формат таймфрейму: {raw}")
    value = int(match.group(1))
    suffix = match.group(2)
    if suffix == "m":
        return value
    if suffix == "h":
        return value * 60
    if suffix == "d":
        return value * 24 * 60
    raise ValueError(f"Непідтримуваний суфікс у таймфреймі: {raw}")


def _parse_window(token: str) -> WindowSpec:
    cleaned = token.strip().lower()
    match = re.fullmatch(r"(\d+)([dwm])", cleaned)
    if not match:
        raise ValueError(f"Невідомий формат вікна '{token}'. Очікується <число><d|w|m>")
    value = int(match.group(1))
    suffix = match.group(2)
    multiplier = {"d": 1, "w": 7, "m": 30}[suffix]
    minutes = value * multiplier * 24 * 60
    return WindowSpec(token=cleaned, minutes=minutes)


async def _export_interval(
    store: UnifiedDataStore,
    symbol: str,
    interval: str,
    windows: list[WindowSpec],
    *,
    output_dir: Path | None,
    overwrite: bool,
) -> None:
    minutes = _parse_interval_minutes(interval)
    max_minutes = max(w.minutes for w in windows)
    max_bars = int(math.ceil(max_minutes / minutes))
    df = await store.get_df(symbol, interval, limit=max_bars + 100)
    if df is None or df.empty:
        logger.warning("Дані для %s %s відсутні", symbol, interval)
        return

    base_dir = output_dir or Path(store.cfg.base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    for window in windows:
        bars_needed = int(math.ceil(window.minutes / minutes))
        subset = df.tail(min(len(df), bars_needed)).copy()
        if subset.empty:
            logger.warning(
                "Пропускаю %s %s %s — недостатньо барів",
                symbol,
                interval,
                window.token,
            )
            continue
        virtual_interval = f"{interval}_{window.label}"
        path = Path(base_dir) / f"{symbol}_bars_{virtual_interval}_snapshot.jsonl"
        if path.exists() and not overwrite:
            logger.info("Файл %s вже існує. Використовую існуючий", path)
            continue
        _write_jsonl(subset, path)
        logger.info(
            "Збережено %s барів у %s",
            len(subset),
            path,
        )


def _write_jsonl(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_json(
        path_or_buf=tmp,
        orient="records",
        lines=True,
        date_format="iso",
        date_unit="ms",
        force_ascii=False,
        index=False,
    )
    tmp.replace(path)


async def _run(args: argparse.Namespace) -> None:
    symbol = args.symbol.lower()
    if not symbol:
        raise ValueError("Порожній символ")
    if not args.intervals:
        raise ValueError("Список таймфреймів порожній")
    windows = [_parse_window(token) for token in args.windows]

    store, redis = await _init_store()
    try:
        for interval in args.intervals:
            await _export_interval(
                store,
                symbol,
                interval.lower(),
                windows,
                output_dir=Path(args.output_dir) if args.output_dir else None,
                overwrite=args.force,
            )
    finally:
        await store.stop_maintenance()
        await redis.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        logger.warning("Перервано користувачем")
        return 130
    except Exception as exc:
        logger.error("Помилка експорту снапшотів: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
