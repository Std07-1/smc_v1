"""CLI-раннер для локального запуску SMC-core на історичних даних."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

import pandas as pd
from redis.asyncio import Redis

from app.settings import load_datastore_cfg, settings
from config.config import SMC_BACKTEST_ENABLED
from data.unified_store import StoreConfig, StoreProfile, UnifiedDataStore
from smc_core.engine import SmcCoreEngine
from smc_core.input_adapter import build_smc_input_from_store


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Формує SmcHint по історії з UnifiedDataStore"
    )
    parser.add_argument("symbol", help="Символ, напр. BTCUSDT")
    parser.add_argument(
        "--tf", dest="tf_primary", default="5m", help="Головний таймфрейм"
    )
    parser.add_argument(
        "--extra",
        dest="tfs_extra",
        nargs="*",
        default=("15m", "1h"),
        help="Додаткові TF для контексту",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Максимальна кількість барів на кожен TF",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ігнорувати прапор SMC_BACKTEST_ENABLED",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    if not SMC_BACKTEST_ENABLED and not args.force:
        raise SystemExit(
            "SMC backtest вимкнено конфігом. Запустіть із --force для ручного тесту."
        )

    store, redis = await _init_store()
    engine = SmcCoreEngine()

    try:
        smc_input = await build_smc_input_from_store(
            store,
            args.symbol.lower(),
            args.tf_primary,
            tfs_extra=args.tfs_extra,
            limit=args.limit,
            context={"runner": "cli"},
        )
        hint = engine.process_snapshot(smc_input)
        print(json.dumps(_to_plain(hint), ensure_ascii=False, indent=2))
    finally:
        await store.stop_maintenance()
        await redis.close()


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


def _to_plain(value: Any) -> Any:
    # Переконуємось, що це інстанс dataclass, а не клас, оскільки asdict працює лише з інстансами
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _to_plain(v) for k, v in asdict(value).items()}
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, dict):
        return {k: _to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    return value


if __name__ == "__main__":
    asyncio.run(main())
