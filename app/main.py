"""SMC-only точка входу (Stage1 логіка перенесена до ``degrade``)."""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from dotenv import load_dotenv
from redis.asyncio import Redis
from rich.console import Console
from rich.logging import RichHandler

from app.runtime import (
    bootstrap,
    create_redis_client,
    launch_experimental_viewer,
    noop_healthcheck,
    start_fxcm_tasks,
)
from app.smc_producer import smc_producer
from app.smc_state_manager import SmcStateManager
from app.telemetry import publish_ui_metrics
from config.config import (
    FAST_SYMBOLS_TTL_MANUAL,
    FXCM_FAST_SYMBOLS,
    SCREENING_LOOKBACK,
    SMC_REFRESH_INTERVAL,
)

logger = logging.getLogger("app.main")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    logger.addHandler(RichHandler(console=Console(stderr=True), show_path=True))
    logger.propagate = False

load_dotenv()


async def _init_fast_symbols(store, *, fast_symbols: list[str]) -> list[str]:
    """Прописує fast-символи в UnifiedDataStore та повертає підтверджений список."""

    symbols = [sym.lower() for sym in fast_symbols if sym]
    if not symbols:
        logger.error("[SMC] Список FXCM_FAST_SYMBOLS порожній — завершення")
        return []
    try:
        await store.set_fast_symbols(symbols, ttl=FAST_SYMBOLS_TTL_MANUAL)
        logger.info(
            "[SMC] Fast-символи встановлено ttl=%s count=%d",
            FAST_SYMBOLS_TTL_MANUAL,
            len(symbols),
        )
    except Exception as exc:
        logger.error("[SMC] Не вдалося встановити fast-символи: %s", exc)
        return []
    confirmed = await store.get_fast_symbols()
    if not confirmed:
        logger.error("[SMC] Порожній список fast-символів після запису")
        return []
    return [sym.lower() for sym in confirmed]


async def run_pipeline() -> None:
    """Запускає мінімальний SMC пайплайн."""

    logger.info("[SMC] Старт run_pipeline()")
    tasks: list[asyncio.Task[Any]] = []
    fxcm_tasks: list[asyncio.Task[Any]] = []
    redis_conn: Redis | None = None
    try:
        datastore = await bootstrap()
        redis_conn, source = create_redis_client(decode_responses=True)
        logger.info("[SMC] Redis async клієнт створено (%s)", source)

        launch_experimental_viewer()
        symbols = await _init_fast_symbols(datastore, fast_symbols=FXCM_FAST_SYMBOLS)
        if not symbols:
            return

        state_manager = SmcStateManager(symbols, cache_handler=datastore)
        logger.info("[SMC] SmcStateManager створено count=%d", len(state_manager.state))

        fxcm_tasks = start_fxcm_tasks(datastore)
        health_task = asyncio.create_task(noop_healthcheck())
        tasks.append(health_task)

        redis_pub = None
        try:
            redis_pub = getattr(getattr(datastore, "redis", None), "r", None)
        except Exception:
            redis_pub = None
        metrics_task = asyncio.create_task(
            publish_ui_metrics(datastore, redis_pub, channel="ui.metrics", interval=5.0)
        )
        tasks.append(metrics_task)

        smc_task = asyncio.create_task(
            smc_producer(
                store=datastore,
                store_fast_symbols=datastore,
                assets=symbols,
                redis_conn=redis_conn,
                timeframe="1m",
                lookback=SCREENING_LOOKBACK,
                interval_sec=SMC_REFRESH_INTERVAL,
                state_manager=state_manager,
            )
        )
        tasks.append(smc_task)
        tasks.extend(fxcm_tasks)

        logger.info("[SMC] Запущено %d задач", len(tasks))
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("[SMC] Завершення за CancelledError")
        raise
    finally:
        for task in tasks + fxcm_tasks:
            if isinstance(task, asyncio.Task) and not task.done():
                task.cancel()
        logger.info("[SMC] run_pipeline завершено")


if __name__ == "__main__":
    try:
        asyncio.run(run_pipeline())
    except KeyboardInterrupt:
        logger.info("SMC main зупинено користувачем")
        sys.exit(0)
    except Exception as exc:
        logger.error("Помилка виконання: %s", exc, exc_info=True)
        sys.exit(1)
