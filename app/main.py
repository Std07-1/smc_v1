"""AiOne_t — точка входу системи.

Завдання модуля:
    • Bootstrap UnifiedDataStore та пов'язані сервіси (metrics, admin, health)
    • Підготовка списку активів (ручний або автоматичний префільтр)
    • Preload історії / денні рівні / ініціалізація LevelManager
    • Запуск WebSocket стрімера (WSWorker) та Stage1 моніторингу
    • Запуск Screening Producer + публікація початкового snapshot у Redis
    • Підтримка UI/metrics без торгового контуру Stage3

Архітектурні акценти:
    • Єдине джерело даних: UnifiedDataStore (Redis + RAM)
    • Мінімум побічних ефектів у глобальному просторі — все через bootstrap()
    • Логування уніфіковане (RichHandler, українська локалізація повідомлень)
"""

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from redis.asyncio import Redis
from rich.console import Console
from rich.logging import RichHandler

from app.screening_producer import AssetStateManager, screening_producer
from app.settings import load_datastore_cfg, settings
from app.telemetry import publish_ui_metrics
from app.utils.helper import (
    store_to_dataframe,
)
from config.config import (
    FAST_SYMBOLS_TTL_MANUAL,
    FXCM_FAST_SYMBOLS,
    SCREENING_LOOKBACK,
    STAGE1_MONITOR_PARAMS,
    TRADE_REFRESH_INTERVAL,
    UI_EXPERIMENTAL_VIEW_ENABLED,
)

# ─────────────────────────── Імпорти бізнес-логіки ───────────────────────────
# UnifiedDataStore now the single source of truth
from data.fxcm_ingestor import run_fxcm_ingestor
from data.fxcm_price_stream import run_fxcm_price_stream_listener
from data.fxcm_status_listener import run_fxcm_status_listener
from data.unified_store import StoreConfig, StoreProfile, UnifiedDataStore
from stage1.asset_monitoring import AssetMonitorStage1
from UI.publish_full_state import publish_full_state
from UI.ui_consumer import UIConsumer
from utils.utils import get_tick_size

# Завантажуємо налаштування з .env
load_dotenv()

# ───────────────────────────── Логування ─────────────────────────────
logger = logging.getLogger("app.main")
if not logger.handlers:  # захист від повторної ініціалізації
    logger.setLevel(logging.DEBUG)
    # show_path=True для відображення файлу/рядка у WARN/ERROR
    logger.addHandler(RichHandler(console=Console(stderr=True), show_path=True))
    logger.propagate = False


# (FastAPI вилучено) — якщо потрібен REST інтерфейс у майбутньому,
# повернемо створення app/router

# ───────────────────────────── Глобальні змінні модуля ─────────────────────────────
# Єдиний інстанс UnifiedDataStore (створюється в bootstrap)
store: UnifiedDataStore | None = None

# ───────────────────────────── Шлях / каталоги ─────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
# Каталог зі статичними файлами (фронтенд WebApp)
STATIC_DIR = BASE_DIR / "static"


def _create_redis_client(*, decode_responses: bool = False) -> tuple[Redis, str]:
    """Створює Redis-клієнт із підтримкою REDIS_URL."""

    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        client = (
            Redis.from_url(redis_url, decode_responses=True)
            if decode_responses
            else Redis.from_url(redis_url)
        )
        return client, "REDIS_URL"

    kwargs: dict[str, Any] = {
        "host": settings.redis_host,
        "port": settings.redis_port,
    }
    if decode_responses:
        kwargs["decode_responses"] = True
    client = Redis(**kwargs)
    return client, f"{settings.redis_host}:{settings.redis_port}"


def _start_fxcm_tasks(store_handler: UnifiedDataStore) -> list[asyncio.Task[Any]]:
    """Запускає інжестор, статус- та price-stream лістенери FXCM."""

    tasks: list[asyncio.Task[Any]] = []

    def _launch(
        factory: Callable[[], Coroutine[Any, Any, Any]],
        success_msg: str,
        fail_prefix: str,
    ) -> None:
        try:
            task = asyncio.create_task(factory())
            tasks.append(task)
            logger.info(success_msg)
        except Exception as exc:  # pragma: no cover - best-effort захист
            logger.warning("%s: %s", fail_prefix, exc, exc_info=True)

    _launch(
        lambda: run_fxcm_ingestor(
            store_handler,
            hmac_secret=settings.fxcm_hmac_secret,
            hmac_algo=settings.fxcm_hmac_algo,
            hmac_required=settings.fxcm_hmac_required,
        ),
        "[Pipeline] FXCM інжестор запущено (early)",
        "[Pipeline] Не вдалося запустити FXCM інжестор",
    )

    _launch(
        lambda: run_fxcm_status_listener(
            redis_host=settings.redis_host,
            redis_port=settings.redis_port,
            heartbeat_channel=settings.fxcm_heartbeat_channel,
            market_status_channel=settings.fxcm_market_status_channel,
            status_channel=settings.fxcm_status_channel,
        ),
        "[Pipeline] FXCM статус-лістенер запущено",
        "[Pipeline] Не вдалося запустити FXCM статус-лістенер",
    )

    _launch(
        lambda: run_fxcm_price_stream_listener(
            store_handler,
            redis_host=settings.redis_host,
            redis_port=settings.redis_port,
            channel=settings.fxcm_price_tick_channel,
        ),
        "[Pipeline] FXCM price-stream лістенер запущено",
        "[Pipeline] Не вдалося запустити FXCM price-stream",
    )

    return tasks


async def bootstrap() -> UnifiedDataStore:
    """Ініціалізація інфраструктурних компонентів.

    Кроки:
      1. Завантаження datastore конфігурації
      2. Підключення до Redis
      3. Ініціалізація UnifiedDataStore + maintenance loop
    4. Запуск командного адміністративного циклу
    """
    global store
    cfg = load_datastore_cfg()
    logger.info(
        "[Launch] datastore.yaml loaded: namespace=%s base_dir=%s",
        cfg.namespace,
        cfg.base_dir,
    )
    # Використовуємо значення з app.settings (підтримує .env через pydantic-settings)
    redis, redis_source = _create_redis_client()
    logger.info("[Launch] Redis client created via %s", redis_source)
    # Pydantic v2: use model_dump(); fallback to dict() for backward compat
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
    logger.info("[Launch] UnifiedDataStore maintenance loop started")
    # await _warmup_datastore_from_snapshots(store)
    return store


def launch_ui_consumer() -> None:
    """Запускає відповідний UI-консюмер (стандартний або експериментальний)."""

    module_name = (
        "UI.ui_consumer_experimental_entry"
        if UI_EXPERIMENTAL_VIEW_ENABLED
        else "UI.ui_consumer_entry"
    )
    if UI_EXPERIMENTAL_VIEW_ENABLED:
        logger.info("[UI] Увімкнено experimental viewer під флагом")
    proj_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if sys.platform.startswith("win"):
        subprocess.Popen(
            ["start", "cmd", "/k", "python", "-m", module_name],
            shell=True,
            cwd=proj_root,  # запуск з кореня проекту, щоб UI бачився як модуль
        )
    else:
        # In headless environments (WSL, CI) gnome-terminal may be missing.
        # Check availability and avoid raising FileNotFoundError.
        term = shutil.which("gnome-terminal")
        if not term:
            logger.info(
                "UI consumer terminal not available (gnome-terminal not found); skipping launch."
            )
            return
        try:
            subprocess.Popen([term, "--", "python3", "-m", module_name], cwd=proj_root)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Не вдалося запустити UI consumer: %s", e)


def validate_settings() -> None:
    """Перевіряє необхідні змінні середовища (Redis для FXCM режиму)."""
    missing: list[str] = []
    if not os.getenv("REDIS_URL"):
        if not settings.redis_host:
            missing.append("REDIS_HOST")
        if not settings.redis_port:
            missing.append("REDIS_PORT")

    if missing:
        raise ValueError(f"Відсутні налаштування: {', '.join(missing)}")

    logger.info("Налаштування перевірено — OK.")


# Legacy init_system removed (UnifiedDataStore handles Redis connection)


async def noop_healthcheck() -> None:
    """Легкий healthcheck-плейсхолдер (RAMBuffer видалено)."""
    while True:
        await asyncio.sleep(120)


async def run_pipeline() -> None:
    """Основний асинхронний цикл застосунку (оркестрація компонентів)."""
    logger.info("[Pipeline] Старт run_pipeline() (FXCM режим)")
    tasks_to_run: list[asyncio.Task] = []
    redis_conn: Redis | None = None
    fxcm_tasks: list[asyncio.Task[Any]] = []

    try:
        # 1. Ініціалізація сховища та Redis
        ds = await bootstrap()
        logger.info("[Pipeline] UnifiedDataStore ініціалізовано успішно")

        redis_conn, redis_conn_source = _create_redis_client(decode_responses=True)
        logger.info("[Pipeline] Redis async клієнт створено (%s)", redis_conn_source)

        launch_ui_consumer()
        logger.info("[Pipeline] Запуск UI consumer")

        fast_symbols = [str(sym).lower() for sym in FXCM_FAST_SYMBOLS if sym]
        if not fast_symbols:
            logger.error("[Pipeline] FXCM_FAST_SYMBOLS порожній — завершення")
            return

        try:
            await ds.set_fast_symbols(fast_symbols, ttl=FAST_SYMBOLS_TTL_MANUAL)
            logger.info(
                "[Pipeline] FXCM fast-символи зафіксовано ttl=%s count=%d",
                FAST_SYMBOLS_TTL_MANUAL,
                len(fast_symbols),
            )
        except Exception as e:
            logger.error(
                "[Pipeline] Не вдалося встановити FXCM fast-символи: %s",
                e,
                exc_info=True,
            )
            return

        fast_symbols = await ds.get_fast_symbols()
        if not fast_symbols:
            logger.error("[Pipeline] Порожній список fast-символів — завершення")
            return

        logger.info(
            "[Pipeline] Початковий fast-список (count=%d): %s",
            len(fast_symbols),
            fast_symbols,
        )

        for sym in fast_symbols:
            try:
                df_1m = await store_to_dataframe(ds, sym, limit=500)
                price_hint = (
                    float(df_1m["close"].iloc[-1])
                    if df_1m is not None and not df_1m.empty
                    else None
                )
                _tick_size = get_tick_size(sym, price_hint=price_hint)
            except Exception as e:
                logger.debug(
                    "[Pipeline] Ініціалізація даних для %s пропущена: %s", sym, e
                )

        assets_current = [s.lower() for s in fast_symbols]
        state_manager = AssetStateManager(assets_current)
        logger.info(
            "[Pipeline] AssetStateManager створено count=%d", len(assets_current)
        )

        logger.info("[Pipeline] Ініціалізація AssetMonitorStage1...")
        try:
            monitor = AssetMonitorStage1(
                cache_handler=ds,
                state_manager=state_manager,
                vol_z_threshold=float(
                    STAGE1_MONITOR_PARAMS.get("vol_z_threshold", 2.0)
                ),
                rsi_overbought=STAGE1_MONITOR_PARAMS.get("rsi_overbought"),
                rsi_oversold=STAGE1_MONITOR_PARAMS.get("rsi_oversold"),
                min_reasons_for_alert=int(
                    STAGE1_MONITOR_PARAMS.get("min_reasons_for_alert", 2)
                ),
                dynamic_rsi_multiplier=float(
                    STAGE1_MONITOR_PARAMS.get("dynamic_rsi_multiplier", 1.1)
                ),
                on_alert=None,
            )
            logger.info(
                "[Pipeline] AssetMonitorStage1 OK vol_z=%.1f rsi_ob=%s rsi_os=%s dyn_mult=%.2f min_reasons=%d",
                getattr(monitor, "vol_z_threshold", None),
                getattr(monitor, "rsi_overbought", None),
                getattr(monitor, "rsi_oversold", None),
                getattr(monitor, "dynamic_rsi_multiplier", None),
                getattr(monitor, "min_reasons_for_alert", None),
            )
            cfg_thr = STAGE1_MONITOR_PARAMS or {}
            logger.info(
                "[Pipeline] Stage1 дефолтні пороги: low_gate=%.4f high_gate=%.4f vwap_dev=%.4f min_atr_pct=%.4f",
                float(cfg_thr.get("atr_low_gate", 0.0035)),
                float(cfg_thr.get("atr_high_gate", 0.015)),
                float(cfg_thr.get("vwap_deviation", 0.02)),
                float(cfg_thr.get("min_atr_percent", 0.0)),
            )
        except Exception as e:
            logger.error(
                "[Pipeline] Помилка створення AssetMonitorStage1: %s", e, exc_info=True
            )
            return

        try:
            ds.stage1_monitor = monitor  # type: ignore[attr-defined]
            logger.info("[Pipeline] Монітор прив'язано до datastore")
        except Exception as e:
            logger.debug("[Pipeline] Не вдалося прив'язати монітор до datastore: %s", e)

        logger.info("[Pipeline] FXCM режим: legacy крипто-WSWorker не запускається")
        fxcm_tasks = _start_fxcm_tasks(ds)

        health_task = asyncio.create_task(noop_healthcheck())
        logger.info("[Pipeline] Healthcheck task створено")

        redis_pub = None
        try:
            redis_pub = getattr(getattr(ds, "redis", None), "r", None)
        except Exception:
            redis_pub = None
        metrics_task = asyncio.create_task(
            publish_ui_metrics(
                ds,
                redis_pub,
                channel="ui.metrics",
                interval=5.0,
            )
        )
        logger.info("[Pipeline] Metrics publisher запущено")

        logger.info("[Pipeline] Ініціалізація UIConsumer...")
        try:
            UIConsumer()
            logger.info("[Pipeline] UIConsumer ініціалізовано")
        except Exception as e:
            logger.warning("[Pipeline] UIConsumer не ініціалізовано: %s", e)

        prod = None
        try:
            logger.info("[Pipeline] Запуск Screening Producer (batch mode)")
            prod = asyncio.create_task(
                screening_producer(
                    monitor=monitor,
                    store=ds,
                    store_fast_symbols=ds,
                    assets=fast_symbols,
                    redis_conn=redis_conn,
                    timeframe="1m",
                    lookback=SCREENING_LOOKBACK,
                    interval_sec=TRADE_REFRESH_INTERVAL,
                    state_manager=state_manager,
                )
            )
            logger.info("[Pipeline] Screening Producer task створено")
        except Exception as e:
            logger.error(
                "[Pipeline] Помилка запуску Screening Producer: %s",
                e,
                exc_info=True,
            )

        logger.info("[Pipeline] Публікація початкового стану в Redis...")
        try:
            await publish_full_state(state_manager, ds, redis_conn)
            logger.info("[Pipeline] Початковий стан опубліковано успішно")

        except Exception as e:
            logger.error(
                "[Pipeline] Помилка публікації початкового стану: %s", e, exc_info=True
            )

        tasks_to_run = [health_task, metrics_task]
        if prod is not None:
            tasks_to_run.append(prod)
        if fxcm_tasks:
            tasks_to_run.extend(fxcm_tasks)

        logger.info("[Pipeline] Запуск %d фон-виконавчих задач", len(tasks_to_run))
        logger.info(
            "[Pipeline] Очікуємо завершення всіх завдань (у нормі вони довготривалі)"
        )
        await asyncio.gather(*tasks_to_run)
        logger.info(
            "[Pipeline] asyncio.gather завершено (неочікувано для довгоживих задач)"
        )
    except asyncio.CancelledError:
        logger.info("[Pipeline] Завершення за скасуванням (CancelledError)")
        raise

    finally:
        to_cancel: list[asyncio.Task] = []
        for task in tasks_to_run:
            if isinstance(task, asyncio.Task) and not task.done():
                task.cancel()
                to_cancel.append(task)
        if to_cancel:
            await asyncio.gather(*to_cancel, return_exceptions=True)
            logger.info("[Pipeline] Незавершені задачі скасовано")
        logger.info("[Pipeline] Завершення run_pipeline()")


# (metrics endpoint видалено разом із FastAPI роутингом)


if __name__ == "__main__":
    try:
        asyncio.run(run_pipeline())
    except KeyboardInterrupt:
        # М'яке завершення без стека трейсів
        logger.info("Зупинено користувачем")
    except Exception as e:
        logger.error("Помилка виконання: %s", e, exc_info=True)
        sys.exit(1)
