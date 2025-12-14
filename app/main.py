"""SMC-only точка входу (Stage1 логіка перенесена до ``degrade``)."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from typing import Any

from dotenv import load_dotenv
from redis.asyncio import Redis
from rich.logging import RichHandler

from app.console_status_bar import run_console_status_bar
from app.fxcm_warmup_requester import build_requester_from_config
from app.runtime import (
    _build_allowed_pairs,
    _build_contract_min_history_bars,
    bootstrap,
    create_redis_client,
    launch_experimental_viewer,
    noop_healthcheck,
    start_fxcm_tasks,
)
from app.settings import settings
from app.smc_producer import smc_producer
from app.smc_state_manager import SmcStateManager
from app.telemetry import publish_ui_metrics
from config.config import (
    FAST_SYMBOLS_TTL_MANUAL,
    FXCM_FAST_SYMBOLS,
    REDIS_CHANNEL_SMC_STATE,
    REDIS_CHANNEL_SMC_VIEWER_EXTENDED,
    REDIS_SNAPSHOT_KEY_SMC,
    REDIS_SNAPSHOT_KEY_SMC_VIEWER,
    SCREENING_LOOKBACK,
    SMC_REFRESH_INTERVAL,
    UI_V2_DEBUG_VIEWER_ENABLED,
    UI_V2_DEBUG_VIEWER_SYMBOLS,
)
from data.unified_store import UnifiedDataStore
from UI_v2.fxcm_ohlcv_ws_server import FxcmOhlcvWsServer
from UI_v2.ohlcv_provider import OhlcvProvider, UnifiedStoreOhlcvProvider
from UI_v2.smc_viewer_broadcaster import (
    SmcViewerBroadcaster,
    SmcViewerBroadcasterConfig,
)
from UI_v2.viewer_state_server import ViewerStateHttpServer
from UI_v2.viewer_state_store import ViewerStateStore
from UI_v2.viewer_state_ws_server import ViewerStateWsServer
from utils.rich_console import get_rich_console

logger = logging.getLogger("app.main")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    logger.addHandler(RichHandler(console=get_rich_console(), show_path=True))
    # Під pytest caplog навішує handler на root logger; щоб він бачив записи,
    # вмикаємо propagate лише у тестовому середовищі.
    logger.propagate = "pytest" in sys.modules

load_dotenv()

_FALSE_ENV_VALUES = {"0", "false", "no", "off"}


def _env_flag(name: str, default: bool) -> bool:
    """Зчитує булів прапорець із ENV із дефолтним значенням."""

    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSE_ENV_VALUES


def _env_int(name: str, default: int) -> int:
    """Безпечно читає int із ENV з відкатом до дефолту."""

    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning(
            "[UI_v2] Некоректне значення %s=%s — використовую %d",
            name,
            raw,
            default,
        )
        return default


def _validate_fast_symbols_against_universe(
    fast_symbols: list[str], allowed_pairs: set[tuple[str, str]] | None
) -> None:
    """Логує розриви між FXCM_FAST_SYMBOLS та smc_universe.fxcm_contract."""

    if allowed_pairs is None:
        logger.info(
            "[SMC_UNIVERSE] FXCM_FAST_SYMBOLS використовуються без contract-фільтра (legacy mode, перевірка пропущена)."
        )
        return

    universe_symbols = {sym for (sym, _tf) in allowed_pairs}
    fast_norm = {str(sym).strip().lower() for sym in fast_symbols if sym}

    missing = sorted(fast_norm - universe_symbols)
    if missing:
        logger.warning(
            "[SMC_UNIVERSE] Частина FXCM_FAST_SYMBOLS відсутня у fxcm_contract: %s",
            ", ".join(missing),
        )


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
        datastore, cfg = await bootstrap()
        redis_conn, source = create_redis_client(decode_responses=True)
        logger.info("[SMC] Redis async клієнт створено (%s)", source)

        # Живий статус у консолі (Rich Live) — не блокує пайплайн.
        # Вимикається через SMC_CONSOLE_STATUS_BAR=0.
        tasks.append(
            asyncio.create_task(
                run_console_status_bar(
                    redis_conn=redis_conn,
                    snapshot_key=REDIS_SNAPSHOT_KEY_SMC,
                    console=get_rich_console(),
                ),
                name="console_status_bar",
            )
        )

        symbols = await _init_fast_symbols(datastore, fast_symbols=FXCM_FAST_SYMBOLS)
        if not symbols:
            return

        viewer_tasks = _launch_ui_v2_tasks(datastore)
        tasks.extend(viewer_tasks)

        state_manager = SmcStateManager(symbols, cache_handler=datastore)
        logger.info("[SMC] SmcStateManager створено count=%d", len(state_manager.state))

        allowed_pairs = _build_allowed_pairs(cfg)
        contract_min_bars = _build_contract_min_history_bars(cfg)
        _validate_fast_symbols_against_universe(symbols, allowed_pairs)
        fxcm_tasks = start_fxcm_tasks(datastore, allowed_pairs=allowed_pairs)

        requester = build_requester_from_config(
            redis=redis_conn,
            store=datastore,
            allowed_pairs=allowed_pairs,
            min_history_bars_by_symbol=contract_min_bars,
        )
        if requester is not None:
            tasks.append(
                asyncio.create_task(
                    requester.run_forever(),
                    name="fxcm_warmup_requester",
                )
            )
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
                contract_min_bars=contract_min_bars,
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


def _launch_ui_v2_tasks(datastore: UnifiedDataStore | None) -> list[asyncio.Task[Any]]:
    """Створює таски для UI_v2 broadcaster + HTTP/WS серверів."""

    if not _env_flag("UI_V2_ENABLED", False):
        logger.info(
            "[UI_v2] Viewer стек вимкнено через UI_V2_ENABLED=0 — запускаю legacy viewer"
        )
        launch_experimental_viewer()
        return []

    ohlcv_provider = None
    if datastore is not None:
        ohlcv_provider = UnifiedStoreOhlcvProvider(datastore)

    snapshot_key = os.getenv("SMC_VIEWER_SNAPSHOT_KEY", REDIS_SNAPSHOT_KEY_SMC_VIEWER)
    host = os.getenv("SMC_VIEWER_HTTP_HOST", "127.0.0.1") or "127.0.0.1"
    port = _env_int("SMC_VIEWER_HTTP_PORT", 8080)

    cfg = SmcViewerBroadcasterConfig(
        smc_state_channel=REDIS_CHANNEL_SMC_STATE,
        smc_snapshot_key=REDIS_SNAPSHOT_KEY_SMC,
        viewer_state_channel=REDIS_CHANNEL_SMC_VIEWER_EXTENDED,
        viewer_snapshot_key=snapshot_key,
    )

    tasks: list[asyncio.Task[Any]] = []
    tasks.append(
        asyncio.create_task(
            _run_ui_v2_broadcaster(cfg),
            name="ui_v2_broadcaster",
        )
    )
    tasks.append(
        asyncio.create_task(
            _run_ui_v2_http_server(
                snapshot_key=cfg.viewer_snapshot_key,
                host=host,
                port=port,
                ohlcv_provider=ohlcv_provider,
            ),
            name="ui_v2_http_server",
        )
    )

    ws_host = os.getenv("SMC_VIEWER_WS_HOST", host) or host
    ws_port = _env_int("SMC_VIEWER_WS_PORT", 8081)
    ws_enabled = _env_flag("SMC_VIEWER_WS_ENABLED", True)
    if ws_enabled:
        tasks.append(
            asyncio.create_task(
                _run_ui_v2_ws_server(
                    snapshot_key=cfg.viewer_snapshot_key,
                    host=ws_host,
                    port=ws_port,
                    channel=cfg.viewer_state_channel,
                ),
                name="ui_v2_ws_server",
            )
        )

    fxcm_ohlcv_ws_enabled = _env_flag("FXCM_OHLCV_WS_ENABLED", True)
    fxcm_ohlcv_ws_host = os.getenv("FXCM_OHLCV_WS_HOST", host) or host
    fxcm_ohlcv_ws_port = _env_int("FXCM_OHLCV_WS_PORT", 8082)
    if fxcm_ohlcv_ws_enabled:
        tasks.append(
            asyncio.create_task(
                _run_fxcm_ohlcv_ws_server(
                    host=fxcm_ohlcv_ws_host,
                    port=fxcm_ohlcv_ws_port,
                ),
                name="fxcm_ohlcv_ws_server",
            )
        )

    logger.info(
        "[UI_v2] Активовано стек viewer (HTTP %s:%d, WS %s:%s, snapshot=%s, ws=%s)",
        host,
        port,
        ws_host if ws_enabled else "-",
        ws_port if ws_enabled else "-",
        snapshot_key,
        "on" if ws_enabled else "off",
    )
    if fxcm_ohlcv_ws_enabled:
        logger.info(
            "[UI_v2] FXCM OHLCV WS увімкнено: %s:%d (/fxcm/ohlcv)",
            fxcm_ohlcv_ws_host,
            fxcm_ohlcv_ws_port,
        )
    _launch_ui_v2_debug_viewer()
    return tasks


async def _run_ui_v2_broadcaster(cfg: SmcViewerBroadcasterConfig) -> None:
    """Фоновий раннер broadcaster-а SMC -> viewer_state."""

    redis = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        decode_responses=False,
    )
    try:
        broadcaster = SmcViewerBroadcaster(redis=redis, cfg=cfg)
        await broadcaster.run_forever()
    except asyncio.CancelledError:
        logger.info("[UI_v2] Broadcaster task cancelled")
        raise
    finally:
        await redis.close()


async def _run_ui_v2_http_server(
    *,
    snapshot_key: str,
    host: str,
    port: int,
    ohlcv_provider: OhlcvProvider | None,
) -> None:
    """Фоновий HTTP сервер для доступу до viewer snapshot."""

    redis = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        decode_responses=False,
    )
    store = ViewerStateStore(redis=redis, snapshot_key=snapshot_key)
    server = ViewerStateHttpServer(
        store=store,
        ohlcv_provider=ohlcv_provider,
        host=host,
        port=port,
    )
    try:
        await server.run()
    except asyncio.CancelledError:
        logger.info("[UI_v2] HTTP server task cancelled")
        raise
    finally:
        await redis.close()


async def _run_ui_v2_ws_server(
    *, snapshot_key: str, host: str, port: int, channel: str
) -> None:
    """Фоновий WebSocket сервер для live viewer_state."""

    redis = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        decode_responses=False,
    )
    store = ViewerStateStore(redis=redis, snapshot_key=snapshot_key)
    server = ViewerStateWsServer(
        store=store,
        redis=redis,
        channel_name=channel,
        host=host,
        port=port,
    )
    try:
        await server.run()
    except asyncio.CancelledError:
        logger.info("[UI_v2] WS server task cancelled")
        raise
    finally:
        await redis.close()


async def _run_fxcm_ohlcv_ws_server(*, host: str, port: int) -> None:
    """Фоновий WebSocket сервер для проксування каналу fxcm:ohlcv у браузер."""

    redis = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        decode_responses=False,
    )
    server = FxcmOhlcvWsServer(
        redis=redis,
        channel_name="fxcm:ohlcv",
        host=host,
        port=port,
    )
    try:
        await server.run()
    except asyncio.CancelledError:
        logger.info("[UI_v2] FXCM OHLCV WS server task cancelled")
        raise
    finally:
        await redis.close()


def _build_debug_viewer_popen_kwargs(
    *, platform: str | None = None, creation_flag: int | None = None
) -> dict[str, Any]:
    """Формує параметри Popen для запуску debug viewer в окремій консолі."""

    params: dict[str, Any] = {"stdout": None, "stderr": None, "stdin": None}
    platform_norm = (platform or os.name).lower()
    new_console_flag = (
        creation_flag
        if creation_flag is not None
        else int(getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    )
    if platform_norm == "nt":
        if new_console_flag:
            params["creationflags"] = new_console_flag
    else:
        params["start_new_session"] = True
    return params


def _launch_ui_v2_debug_viewer() -> None:
    """Запускає debug viewer v2 як окремий процес (за потреби)."""

    if not UI_V2_DEBUG_VIEWER_ENABLED:
        logger.info("[UI_v2] Debug viewer v2 вимкнено конфігом")
        return

    try:
        popen_kwargs = _build_debug_viewer_popen_kwargs()
        subprocess.Popen(
            [sys.executable, "-m", "UI_v2.debug_viewer_v2"],
            **popen_kwargs,
        )
        logger.info(
            "[UI_v2] Debug viewer v2 запущено для %s",
            ", ".join(UI_V2_DEBUG_VIEWER_SYMBOLS) or "(немає символів)",
        )
    except Exception:
        logger.exception("[UI_v2] Не вдалося запустити debug viewer v2")


if __name__ == "__main__":
    try:
        asyncio.run(run_pipeline())
    except KeyboardInterrupt:
        logger.info("SMC main зупинено користувачем")
        sys.exit(0)
    except Exception as exc:
        logger.error("Помилка виконання: %s", exc, exc_info=True)
        sys.exit(1)
