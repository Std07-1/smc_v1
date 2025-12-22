"""SMC-only точка входу (Stage1 логіка перенесена до ``degrade``)."""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from redis.asyncio import Redis

from app.env import select_env_file
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
    AI_ONE_MODE,
    FAST_SYMBOLS_TTL_MANUAL,
    FXCM_FAST_SYMBOLS,
    NAMESPACE,
    REDIS_CHANNEL_SMC_STATE,
    REDIS_CHANNEL_SMC_VIEWER_EXTENDED,
    REDIS_SNAPSHOT_KEY_SMC,
    REDIS_SNAPSHOT_KEY_SMC_VIEWER,
    SCREENING_LOOKBACK,
    SMC_REFRESH_INTERVAL,
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

logger = logging.getLogger("app.main")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler())
    # Під pytest caplog навішує handler на root logger; щоб він бачив записи,
    # вмикаємо propagate лише у тестовому середовищі.
    logger.propagate = "pytest" in sys.modules


def _silence_expected_websocket_handshake_noise() -> None:
    """Прибирає шумні трейсбеки `websockets` для очікуваних кейсів.

    У проді/за проксі клієнт може розірвати TCP під час handshake (або сканер може
    стукати HTTP запитом у WS порт). `websockets` у таких випадках логує
    "opening handshake failed" з повним traceback, хоча для нас це не є фатально.

    Важливо:
    - фільтруємо лише конкретні повідомлення;
    - інші помилки WS лишаються видимими.
    """

    class _WsHandshakeNoiseFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
            try:
                msg = record.getMessage() or ""
            except Exception:
                return True
            msg_l = msg.lower()
            if "opening handshake failed" in msg_l:
                return False
            if "no close frame received or sent" in msg_l:
                return False
            return True

    flt = _WsHandshakeNoiseFilter()
    for name in (
        "websockets",
        "websockets.server",
        "websockets.asyncio.server",
        "websockets.asyncio.connection",
    ):
        try:
            logging.getLogger(name).addFilter(flt)
        except Exception:
            # Не валимо runtime через різні версії websockets/логерів.
            pass


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(select_env_file(_PROJECT_ROOT))
_silence_expected_websocket_handshake_noise()

_FALSE_ENV_VALUES = {"0", "false", "no", "off"}


def _is_address_in_use_error(exc: BaseException) -> bool:
    """Повертає True, якщо виняток означає "порт зайнятий"."""

    err = getattr(exc, "errno", None)
    if err in {errno.EADDRINUSE, 10048}:
        return True
    text = str(exc).lower()
    return (
        "address already in use" in text
        or "only one usage of each socket address" in text
    )


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

    raw_s = str(raw).strip()
    if not raw_s:
        return default

    try:
        return int(raw_s)
    except ValueError:
        logger.warning(
            "[UI_v2] Некоректне значення %s=%s — використовую %d",
            name,
            raw,
            default,
        )
        return default


def _default_bind_host() -> str:
    """Дефолтний host bind для UI залежно від режиму запуску."""

    mode = str(AI_ONE_MODE or "prod").strip().lower()
    return "127.0.0.1" if mode == "local" else "0.0.0.0"


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
    logger.info(
        "[Launch] Профіль: AI_ONE_MODE=%s NAMESPACE=%s",
        AI_ONE_MODE,
        NAMESPACE,
    )
    if str(AI_ONE_MODE or "").strip().lower() == "local" and NAMESPACE == "ai_one":
        logger.warning(
            "[Launch] УВАГА: локальний режим використовує prod namespace 'ai_one'. "
            "Перевірте ENV/.env: приберіть AI_ONE_NAMESPACE або встановіть ai_one_local."
        )
    tasks: list[asyncio.Task[Any]] = []
    fxcm_tasks: list[asyncio.Task[Any]] = []
    redis_conn: Redis | None = None
    try:
        datastore, cfg = await bootstrap()
        redis_conn, source = create_redis_client(decode_responses=True)
        logger.info("[SMC] Redis async клієнт створено (%s)", source)

        symbols = await _init_fast_symbols(datastore, fast_symbols=FXCM_FAST_SYMBOLS)
        if not symbols:
            return

        _maybe_launch_debug_viewer()

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
        logger.info("[UI_v2] Web сервіс вимкнено через UI_V2_ENABLED=0")
        return []

    ohlcv_provider = None
    if datastore is not None:
        ohlcv_provider = UnifiedStoreOhlcvProvider(datastore)

    snapshot_key = os.getenv("SMC_VIEWER_SNAPSHOT_KEY", REDIS_SNAPSHOT_KEY_SMC_VIEWER)
    default_host = _default_bind_host()
    host = os.getenv("SMC_VIEWER_HTTP_HOST", default_host) or default_host
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
    return tasks


def _maybe_launch_debug_viewer() -> None:
    """Опційно запускає console debug viewer (SMC Viewer · Extended).

    Важливо: UI_V2_ENABLED керує лише web-стеком (HTTP/WS). Запуск debug viewer
    виносимо в окремий ENV-прапорець.
    """

    if not _env_flag("DEBUG_VIEWER_ENABLED", False):
        return
    try:
        launch_experimental_viewer()
        logger.info("[UI] Запущено debug viewer (ENV DEBUG_VIEWER_ENABLED=1)")
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("[UI] Не вдалося запустити debug viewer: %s", exc)


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

    retry_delay_sec = 5.0
    backoff_sec = 1.0
    try:
        while True:
            redis = Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                decode_responses=False,
            )
            try:
                store = ViewerStateStore(redis=redis, snapshot_key=snapshot_key)
                server = ViewerStateHttpServer(
                    store=store,
                    ohlcv_provider=ohlcv_provider,
                    host=host,
                    port=port,
                )
                await server.run()
                return
            except asyncio.CancelledError:
                logger.info("[UI_v2] HTTP server task cancelled")
                raise
            except OSError as exc:
                if _is_address_in_use_error(exc):
                    logger.error(
                        "[UI_v2] HTTP server не зміг забіндитись на %s:%d (порт зайнятий). "
                        "Повторю спробу через %.0fс. "
                        "(Підказка: змініть ENV SMC_VIEWER_HTTP_PORT або зупиніть процес, що слухає цей порт.)",
                        host,
                        port,
                        retry_delay_sec,
                    )
                    await asyncio.sleep(retry_delay_sec)
                    retry_delay_sec = min(retry_delay_sec * 1.5, 30.0)
                    continue
                raise
            except Exception:
                logger.warning(
                    "[UI_v2] HTTP server: помилка/Redis недоступний. Повтор через %.1f с.",
                    backoff_sec,
                    exc_info=True,
                )
                await asyncio.sleep(backoff_sec)
                backoff_sec = min(backoff_sec * 2.0, 60.0)
            finally:
                try:
                    await redis.close()
                except Exception:
                    pass
    finally:
        return


async def _run_ui_v2_ws_server(
    *, snapshot_key: str, host: str, port: int, channel: str
) -> None:
    """Фоновий WebSocket сервер для live viewer_state."""

    retry_delay_sec = 5.0
    backoff_sec = 1.0
    try:
        while True:
            redis = Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                decode_responses=False,
            )
            try:
                store = ViewerStateStore(redis=redis, snapshot_key=snapshot_key)
                server = ViewerStateWsServer(
                    store=store,
                    redis=redis,
                    channel_name=channel,
                    host=host,
                    port=port,
                )
                await server.run()
                return
            except asyncio.CancelledError:
                logger.info("[UI_v2] WS server task cancelled")
                raise
            except OSError as exc:
                if _is_address_in_use_error(exc):
                    logger.error(
                        "[UI_v2] WS server не зміг забіндитись на %s:%d (порт зайнятий). "
                        "Повторю спробу через %.0fс. "
                        "(Підказка: змініть ENV SMC_VIEWER_WS_PORT або зупиніть процес, що слухає цей порт.)",
                        host,
                        port,
                        retry_delay_sec,
                    )
                    await asyncio.sleep(retry_delay_sec)
                    retry_delay_sec = min(retry_delay_sec * 1.5, 30.0)
                    continue
                raise
            except Exception:
                logger.warning(
                    "[UI_v2] WS server: помилка/Redis недоступний. Повтор через %.1f с.",
                    backoff_sec,
                    exc_info=True,
                )
                await asyncio.sleep(backoff_sec)
                backoff_sec = min(backoff_sec * 2.0, 60.0)
            finally:
                try:
                    await redis.close()
                except Exception:
                    pass
    finally:
        return


async def _run_fxcm_ohlcv_ws_server(*, host: str, port: int) -> None:
    """Фоновий WebSocket сервер для проксування каналу fxcm:ohlcv у браузер."""

    retry_delay_sec = 5.0
    backoff_sec = 1.0
    try:
        while True:
            redis = Redis(
                host=settings.redis_host,
                port=settings.redis_port,
                decode_responses=False,
            )
            try:
                server = FxcmOhlcvWsServer(
                    redis=redis,
                    channel_name=settings.fxcm_ohlcv_channel,
                    host=host,
                    port=port,
                )
                await server.run()
                return
            except asyncio.CancelledError:
                logger.info("[UI_v2] FXCM OHLCV WS server task cancelled")
                raise
            except OSError as exc:
                if _is_address_in_use_error(exc):
                    logger.error(
                        "[UI_v2] FXCM OHLCV WS server не зміг забіндитись на %s:%d (порт зайнятий). "
                        "Повторю спробу через %.0fс. "
                        "(Підказка: змініть ENV FXCM_OHLCV_WS_PORT або зупиніть процес, що слухає цей порт.)",
                        host,
                        port,
                        retry_delay_sec,
                    )
                    await asyncio.sleep(retry_delay_sec)
                    retry_delay_sec = min(retry_delay_sec * 1.5, 30.0)
                    continue
                raise
            except Exception:
                logger.warning(
                    "[UI_v2] FXCM OHLCV WS server: помилка/Redis недоступний. Повтор через %.1f с.",
                    backoff_sec,
                    exc_info=True,
                )
                await asyncio.sleep(backoff_sec)
                backoff_sec = min(backoff_sec * 2.0, 60.0)
            finally:
                try:
                    await redis.close()
                except Exception:
                    pass
    finally:
        return


if __name__ == "__main__":
    try:
        asyncio.run(run_pipeline())
    except KeyboardInterrupt:
        logger.info("SMC main зупинено користувачем")
        sys.exit(0)
    except Exception as exc:
        logger.error("Помилка виконання: %s", exc, exc_info=True)
        sys.exit(1)
