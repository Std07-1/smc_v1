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
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from redis.asyncio import Redis
from rich.console import Console
from rich.logging import RichHandler

from app.screening_producer import AssetStateManager, screening_producer
from app.settings import load_datastore_cfg, settings
from app.thresholds import Thresholds
from app.utils.helper import (
    store_to_dataframe,
)
from config.config import (
    FAST_SYMBOLS_TTL_MANUAL,
    FXCM_FAST_SYMBOLS,
    REACTIVE_STAGE1,
    SCREENING_LOOKBACK,
    STAGE1_MONITOR_PARAMS,
    UI_EXPERIMENTAL_VIEW_ENABLED,
)
from config.TOP100_THRESHOLDS import TOP100_THRESHOLDS

# ─────────────────────────── Імпорти бізнес-логіки ───────────────────────────
# UnifiedDataStore now the single source of truth
from data.fxcm_ingestor import run_fxcm_ingestor
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

# Повністю видалено калібрацію та RAMBuffer — єдиний шар даних UnifiedDataStore

# ───────────────────────────── Шлях / каталоги ─────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
# Каталог зі статичними файлами (фронтенд WebApp)
STATIC_DIR = BASE_DIR / "static"


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
    redis = Redis(
        host=settings.redis_host,
        port=settings.redis_port,
    )
    logger.info(
        "[Launch] Redis client created host=%s port=%s",
        settings.redis_host,
        settings.redis_port,
    )
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
    """Перевіряє необхідні змінні середовища (Redis + Binance ключі)."""
    missing: list[str] = []
    if not os.getenv("REDIS_URL"):
        if not settings.redis_host:
            missing.append("REDIS_HOST")
        if not settings.redis_port:
            missing.append("REDIS_PORT")

    # Binance-ключі необхідні тільки коли джерело даних Binance
    if settings.data_source.lower() == "binance":
        if not settings.binance_api_key:
            missing.append("BINANCE_API_KEY")
        if not settings.binance_secret_key:
            missing.append("BINANCE_SECRET_KEY")

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
    fxcm_task: asyncio.Task | None = None

    try:
        # 1. Ініціалізація сховища та Redis
        ds = await bootstrap()
        logger.info("[Pipeline] UnifiedDataStore ініціалізовано успішно")

        redis_conn = Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            decode_responses=True,
            encoding="utf-8",
        )
        logger.info(
            "[Pipeline] Redis async клієнт створено host=%s port=%s",
            settings.redis_host,
            settings.redis_port,
        )

        launch_ui_consumer()
        logger.info("[Pipeline] Спроба запуску UI consumer ініційована")

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
        logger.info(
            "[Pipeline] FXCM режим: пропускаємо preload_1m_history (джерело не Binance)"
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
        except Exception as e:
            logger.error(
                "[Pipeline] Помилка створення AssetMonitorStage1: %s", e, exc_info=True
            )
            return

        # Завантажуємо пороги
        try:
            for sym, cfg in TOP100_THRESHOLDS.items():
                monitor._symbol_cfg[sym] = Thresholds.from_mapping(
                    {"symbol": sym, "config": cfg}
                )
            logger.info(
                "[Pipeline] TOP100_THRESHOLDS попередньо завантажено (%d записів)",
                len(TOP100_THRESHOLDS),
            )
        except Exception as e:
            logger.warning("[Pipeline] Не вдалося завантажити TOP100_THRESHOLDS: %s", e)

        try:
            sample_syms = list(fast_symbols)[:5]
            if sample_syms:
                lines = []
                for sym in sample_syms:
                    if sym not in monitor._symbol_cfg and sym in TOP100_THRESHOLDS:
                        monitor._symbol_cfg[sym] = Thresholds.from_mapping(
                            {"symbol": sym, "config": TOP100_THRESHOLDS[sym]}
                        )
                    thr_obj = monitor._symbol_cfg.get(sym) or Thresholds.from_mapping(
                        {"symbol": sym, "config": {}}
                    )
                    eff = thr_obj.effective_thresholds(market_state=None)
                    parts = [
                        f"low_gate={eff.get('low_gate')}",
                        f"high_gate={eff.get('high_gate')}",
                        f"vol_z_threshold={eff.get('vol_z_threshold')}",
                        f"vwap_deviation={eff.get('vwap_deviation')}",
                        f"rsi_os={eff.get('rsi_oversold')}",
                        f"rsi_ob={eff.get('rsi_overbought')}",
                    ]
                    lines.append(f"  {sym}: " + ", ".join(parts))
                logger.info(
                    "[Pipeline] Пороги (sample %d):\n%s",
                    len(sample_syms),
                    "\n".join(lines),
                )
        except Exception as e:
            logger.debug("[Pipeline] Приклад порогів не сформовано: %s", e)

        try:
            ds.stage1_monitor = monitor  # type: ignore[attr-defined]
            logger.info("[Pipeline] Монітор прив'язано до datastore")
        except Exception as e:
            logger.debug("[Pipeline] Не вдалося прив'язати монітор до datastore: %s", e)

        logger.info("[Pipeline] FXCM режим: WSWorker (Binance) не запускається")

        health_task = asyncio.create_task(noop_healthcheck())
        logger.info("[Pipeline] Healthcheck task створено")

        async def ui_metrics_publisher() -> None:
            channel = "ui.metrics"
            logger.info("[Pipeline] Старт ui_metrics_publisher channel=%s", channel)
            while True:
                snap = ds.metrics_snapshot()
                try:
                    lru = getattr(getattr(ds, "ram", None), "_lru", None)
                    if lru is not None:
                        hot_symbols = list({s for (s, _i) in lru.keys()})
                        snap["hot_symbols"] = len(hot_symbols)
                    else:
                        snap["hot_symbols"] = None
                except Exception:
                    snap["hot_symbols"] = None
                try:
                    redis_pub = getattr(getattr(ds, "redis", None), "r", None)
                    if redis_pub is not None:
                        await redis_pub.publish(channel, json.dumps(snap))
                except Exception as e:
                    logger.debug("[Pipeline] ui_metrics publish fail: %s", e)
                await asyncio.sleep(5)

        metrics_task = asyncio.create_task(ui_metrics_publisher())
        logger.info("[Pipeline] Metrics publisher запущено")

        logger.info("[Pipeline] Ініціалізація UIConsumer...")
        try:
            UIConsumer()
            logger.info("[Pipeline] UIConsumer ініціалізовано")
        except Exception as e:
            logger.warning("[Pipeline] UIConsumer не ініціалізовано: %s", e)

        try:
            reactive_enabled = bool(REACTIVE_STAGE1)
        except Exception:
            reactive_enabled = False
        logger.info("[Pipeline] REACTIVE_STAGE1=%s", reactive_enabled)

        prod = None
        if not reactive_enabled:
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
                        interval_sec=12,
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

        if fxcm_task is None:
            try:
                fxcm_task = asyncio.create_task(run_fxcm_ingestor(ds))
                logger.info("[Pipeline] FXCM інжестор запущено")
            except Exception as e:
                logger.warning(
                    "[Pipeline] Не вдалося запустити FXCM інжестор: %s",
                    e,
                    exc_info=True,
                )

        tasks_to_run = [health_task, metrics_task]
        if prod is not None:
            tasks_to_run.append(prod)
        if fxcm_task is not None:
            tasks_to_run.append(fxcm_task)

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
    except Exception:
        logger.error("[Pipeline] run_pipeline error", exc_info=True)
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
