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
from typing import Any

from dotenv import load_dotenv
from redis.asyncio import Redis
from rich.console import Console
from rich.logging import RichHandler

from app.cold_start import (
    build_cold_start_report_payload,
    build_status_payload,
    ensure_min_history,
    history_report_to_summary,
    persist_cold_start_report,
    qa_report_to_summary,
)
from app.history_qa_runner import HistoryQaConfig, run_history_qa_for_symbols
from app.screening_producer import AssetStateManager, screening_producer
from app.settings import load_datastore_cfg, settings
from app.utils.helper import (
    store_to_dataframe,
)
from config.config import (
    COLD_START_STATUS_KEY,
    COLD_START_STATUS_TTL_SEC,
    DATASTORE_WARMUP_ENABLED,
    DATASTORE_WARMUP_INTERVALS,
    FAST_SYMBOLS_TTL_MANUAL,
    FXCM_FAST_SYMBOLS,
    HISTORY_QA_SYMBOLS,
    HISTORY_QA_WARMUP_BARS,
    REACTIVE_STAGE1,
    SCREENING_LOOKBACK,
    SMC_PIPELINE_CFG,
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
from smc_core.engine import SmcCoreEngine
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
_LATEST_COLD_STATUS: dict[str, object] | None = None

# Повністю видалено калібрацію та RAMBuffer — єдиний шар даних UnifiedDataStore

# ───────────────────────────── Шлях / каталоги ─────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
# Каталог зі статичними файлами (фронтенд WebApp)
STATIC_DIR = BASE_DIR / "static"
COLD_START_REPORT_PATH = BASE_DIR / "tmp" / "cold_start_report.json"
COLD_START_STALE_THRESHOLD_SEC = 3600
MAX_COLD_STATUS_ENTRIES = 8
FXCM_HISTORY_TIMEOUT_SEC = 60
HISTORY_REQUIRED_BARS = int(SMC_PIPELINE_CFG.get("limit", SCREENING_LOOKBACK))
HISTORY_QA_LIMIT = int(SMC_PIPELINE_CFG.get("qa_history_limit", HISTORY_REQUIRED_BARS))
HISTORY_QA_STEP = max(1, int(SMC_PIPELINE_CFG.get("qa_history_step", 1)))
HISTORY_QA_MIN_BARS = max(10, int(SMC_PIPELINE_CFG.get("qa_history_min_bars", 50)))


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


async def _warmup_datastore_from_snapshots(
    target_store: UnifiedDataStore,
) -> None:
    """Завантажує останні снапшоти барів із диска, щоб прискорити холодний старт."""

    if not DATASTORE_WARMUP_ENABLED:
        logger.info("[Warmup] Пропущено (DATASTORE_WARMUP_ENABLED=False)")
        return

    intervals_cfg = DATASTORE_WARMUP_INTERVALS or {}
    if not intervals_cfg:
        logger.info("[Warmup] Пропущено (немає інтервалів у конфігу)")
        return

    base_dir = Path(target_store.cfg.base_dir)
    if not base_dir.exists():
        logger.warning(
            "[Warmup] Каталог зі снапшотами відсутній: %s", base_dir.as_posix()
        )
        return

    for interval, bars_needed in intervals_cfg.items():
        symbols = _discover_snapshot_symbols(base_dir, interval)
        if not symbols:
            logger.debug(
                "[Warmup] Снапшоти для інтервалу %s не знайдені у %s",
                interval,
                base_dir.as_posix(),
            )
            continue
        bars = max(int(bars_needed), 0)
        logger.info(
            "[Warmup] Прогріваємо %s символів (%s) останніми %s барами",
            len(symbols),
            interval,
            bars,
        )
        try:
            await target_store.warmup(symbols, interval, bars)
        except Exception:
            logger.exception(
                "[Warmup] Помилка під час прогріву інтервалу %s (symbols=%s)",
                interval,
                symbols[:4],
            )


def _discover_snapshot_symbols(base_dir: Path, interval: str) -> list[str]:
    """Повертає список символів, для яких існують snapshot-файли певного інтервалу."""

    marker = f"_bars_{interval}_snapshot"
    symbols: set[str] = set()
    for path in base_dir.rglob(f"*{marker}.*"):
        if not path.is_file():
            continue
        name = path.name
        if marker not in name:
            continue
        symbol = name.split(marker, 1)[0].strip().strip("_")
        if symbol:
            symbols.add(symbol.lower())
    return sorted(symbols)


async def _update_cold_start_status(
    redis_conn: Redis | None,
    payload: dict[str, object],
) -> None:
    """Оновлює Redis-статус cold-start без зриву пайплайна."""

    if redis_conn is None:
        return
    global _LATEST_COLD_STATUS
    try:
        await redis_conn.set(
            COLD_START_STATUS_KEY,
            json.dumps(payload, ensure_ascii=False),
            ex=COLD_START_STATUS_TTL_SEC,
        )
        _LATEST_COLD_STATUS = dict(payload)
    except Exception as exc:  # pragma: no cover — best-effort метрика
        logger.debug("[ColdStart] Не вдалося записати статус: %s", exc)


async def _cold_status_keepalive(redis_conn: Redis | None) -> None:
    """Періодично відновлює TTL cold-start статусу, щоб UI не бачив UNKNOWN."""

    if redis_conn is None:
        return
    interval = max(30, int(COLD_START_STATUS_TTL_SEC * 0.4))
    logger.info(
        "[ColdStart] Запуск keepalive інтервал=%ss ttl=%s",
        interval,
        COLD_START_STATUS_TTL_SEC,
    )
    try:
        while True:
            await asyncio.sleep(interval)
            if not _LATEST_COLD_STATUS:
                continue
            state_val = str(_LATEST_COLD_STATUS.get("state", "unknown"))
            if not state_val or state_val.lower() == "unknown":
                continue
            await _update_cold_start_status(redis_conn, _LATEST_COLD_STATUS)
    except asyncio.CancelledError:
        logger.info("[ColdStart] Keepalive task зупинено")
        raise
    except Exception:
        logger.warning("[ColdStart] Keepalive task впав", exc_info=True)


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
    fxcm_task: asyncio.Task | None = None
    fxcm_status_task: asyncio.Task | None = None
    price_stream_task: asyncio.Task | None = None
    cold_keepalive_task: asyncio.Task | None = None
    redis_conn: Redis | None = None
    cold_summary: dict[str, object] | None = None

    try:
        # 1. Ініціалізація сховища та Redis
        ds = await bootstrap()
        logger.info("[Pipeline] UnifiedDataStore ініціалізовано успішно")

        redis_conn, redis_conn_source = _create_redis_client(decode_responses=True)
        logger.info("[Pipeline] Redis async клієнт створено (%s)", redis_conn_source)
        await _update_cold_start_status(
            redis_conn,
            build_status_payload(phase="initializing"),
        )
        cold_keepalive_task = asyncio.create_task(_cold_status_keepalive(redis_conn))

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

        await _update_cold_start_status(
            redis_conn,
            build_status_payload(phase="initial_load"),
        )

        history_report = None
        cold_summary: dict[str, object] | None = None
        qa_summary: dict[str, object] | None = None
        cold_phase = "initial_load"
        qa_report = None
        cold_disk_payload: dict[str, object] | None = None
        try:
            history_report = await ensure_min_history(
                ds,
                fast_symbols,
                interval=str(SMC_PIPELINE_CFG.get("tf_primary", "1m")),
                required_bars=HISTORY_REQUIRED_BARS,
                timeout_sec=FXCM_HISTORY_TIMEOUT_SEC,
                sleep_sec=1.0,
            )
            if history_report.status == "success":
                logger.info(
                    "[ColdStart] Історія готова (%d/%d символів, >= %d барів)",
                    history_report.symbols_ready,
                    history_report.symbols_total,
                    history_report.required_bars,
                )
            else:
                logger.warning(
                    "[ColdStart] Недостатньо історії (required=%d) для: %s",
                    history_report.required_bars,
                    history_report.symbols_pending,
                )
        except Exception:
            logger.warning("[ColdStart] Перевірка історії не виконана", exc_info=True)
        cold_summary = history_report_to_summary(history_report)
        await _update_cold_start_status(
            redis_conn,
            build_status_payload(
                phase="initial_load",
                history=cold_summary,
            ),
        )

        history_tf = str(SMC_PIPELINE_CFG.get("tf_primary", "1m"))
        tfs_extra = tuple(SMC_PIPELINE_CFG.get("tfs_extra", ("5m", "15m", "1h")))
        qa_symbols_source = HISTORY_QA_SYMBOLS or fast_symbols
        qa_symbols = [str(sym).lower() for sym in qa_symbols_source if sym]
        if not qa_symbols:
            qa_symbols = list(fast_symbols)
        if HISTORY_QA_SYMBOLS:
            logger.info(
                "[ColdStart] History QA symbols override (%d): %s",
                len(qa_symbols),
                qa_symbols,
            )
        else:
            logger.info(
                "[ColdStart] History QA символи збігаються з fast-списком (%d)",
                len(qa_symbols),
            )
        history_qa_cfg = HistoryQaConfig(
            tf_primary=history_tf,
            tfs_extra=tfs_extra,
            limit=HISTORY_QA_LIMIT,
            step=HISTORY_QA_STEP,
            min_bars_per_snapshot=HISTORY_QA_MIN_BARS,
            warmup_bars=HISTORY_QA_WARMUP_BARS,
        )
        qa_engine = SmcCoreEngine()
        try:
            await _update_cold_start_status(
                redis_conn,
                build_status_payload(
                    phase="qa_history",
                    history=cold_summary,
                ),
            )
            qa_report = await run_history_qa_for_symbols(
                ds,
                qa_symbols,
                history_qa_cfg,
                engine=qa_engine,
            )
            qa_summary = qa_report_to_summary(qa_report)
            cold_phase = (
                "ready"
                if history_report is not None
                and history_report.status == "success"
                and qa_report.status == "success"
                else "error"
            )
            if cold_phase == "ready":
                try:
                    cold_disk_payload, _ = await build_cold_start_report_payload(
                        ds,
                        fast_symbols,
                        interval=history_tf,
                        min_rows=HISTORY_REQUIRED_BARS,
                        stale_threshold=COLD_START_STALE_THRESHOLD_SEC,
                    )
                    await asyncio.to_thread(
                        persist_cold_start_report,
                        COLD_START_REPORT_PATH,
                        cold_disk_payload,
                    )
                    logger.info(
                        "[ColdStart] JSON-звіт оновлено: %s",
                        COLD_START_REPORT_PATH.as_posix(),
                    )
                except Exception:
                    logger.warning(
                        "[ColdStart] Не вдалося оновити cold_start_report.json",
                        exc_info=True,
                    )
        except Exception:
            logger.warning("[ColdStart] History QA не виконано", exc_info=True)
            cold_phase = "error"
        finally:
            summary_payload: dict[str, object] | None = None
            entries_payload: list[dict[str, object]] | None = None
            if isinstance(cold_disk_payload, dict):
                summary_value = cold_disk_payload.get("summary")
                if isinstance(summary_value, dict):
                    summary_payload = summary_value
                entries_value = cold_disk_payload.get("entries")
                if isinstance(entries_value, list):
                    filtered_entries = [
                        entry for entry in entries_value if isinstance(entry, dict)
                    ]
                    if filtered_entries:
                        entries_payload = filtered_entries
                if summary_payload is not None:
                    stale_symbols = summary_payload.get("stale_symbols") or []
                    insufficient_symbols = (
                        summary_payload.get("insufficient_symbols") or []
                    )
                    if stale_symbols:
                        cold_phase = "stale"
                        logger.warning(
                            "[ColdStart] Історія знайдена, але символи %s позначені як stale (age > %ss)",
                            stale_symbols,
                            COLD_START_STALE_THRESHOLD_SEC,
                        )
                    elif insufficient_symbols:
                        cold_phase = "error"
                        logger.warning(
                            "[ColdStart] Недостатньо барів для символів %s попри успішний QA",
                            insufficient_symbols,
                        )
            await _update_cold_start_status(
                redis_conn,
                build_status_payload(
                    phase=cold_phase,
                    history=cold_summary,
                    qa=qa_summary,
                    summary=summary_payload,
                    entries=entries_payload,
                ),
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

        if fxcm_task is None:
            try:
                fxcm_task = asyncio.create_task(
                    run_fxcm_ingestor(
                        ds,
                        hmac_secret=settings.fxcm_hmac_secret,
                        hmac_algo=settings.fxcm_hmac_algo,
                        hmac_required=settings.fxcm_hmac_required,
                    )
                )
                logger.info("[Pipeline] FXCM інжестор запущено (early)")
            except Exception as e:
                logger.warning(
                    "[Pipeline] Не вдалося запустити FXCM інжестор: %s",
                    e,
                    exc_info=True,
                )

        if fxcm_status_task is None:
            try:
                fxcm_status_task = asyncio.create_task(
                    run_fxcm_status_listener(
                        redis_host=settings.redis_host,
                        redis_port=settings.redis_port,
                        heartbeat_channel=settings.fxcm_heartbeat_channel,
                        market_status_channel=settings.fxcm_market_status_channel,
                        status_channel=settings.fxcm_status_channel,
                    )
                )
                logger.info("[Pipeline] FXCM статус-лістенер запущено")
            except Exception as e:
                logger.warning(
                    "[Pipeline] Не вдалося запустити FXCM статус-лістенер: %s",
                    e,
                    exc_info=True,
                )

        if price_stream_task is None:
            try:
                price_stream_task = asyncio.create_task(
                    run_fxcm_price_stream_listener(
                        ds,
                        redis_host=settings.redis_host,
                        redis_port=settings.redis_port,
                        channel=settings.fxcm_price_tick_channel,
                    )
                )
                logger.info("[Pipeline] FXCM price-stream лістенер запущено")
            except Exception as e:
                logger.warning(
                    "[Pipeline] Не вдалося запустити FXCM price-stream: %s",
                    e,
                    exc_info=True,
                )

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
            if cold_phase != "ready":
                logger.warning(
                    "[Pipeline] Screening Producer -умовно- 'пропущено: cold_start_state=%s",
                    cold_phase,
                )
                # else: зараз ця умова не потрібна, бо REACTIVE_STAGE1 завжди False
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
            await _update_cold_start_status(
                redis_conn,
                build_status_payload(
                    phase=cold_phase,
                    history=cold_summary,
                    qa=qa_summary,
                ),
            )
        except Exception as e:
            logger.error(
                "[Pipeline] Помилка публікації початкового стану: %s", e, exc_info=True
            )

        tasks_to_run = [health_task, metrics_task]
        if prod is not None:
            tasks_to_run.append(prod)
        if fxcm_task is not None:
            tasks_to_run.append(fxcm_task)
        if fxcm_status_task is not None:
            tasks_to_run.append(fxcm_status_task)
        if price_stream_task is not None:
            tasks_to_run.append(price_stream_task)
        if cold_keepalive_task is not None:
            tasks_to_run.append(cold_keepalive_task)

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
        if redis_conn is not None:
            await _update_cold_start_status(
                redis_conn,
                build_status_payload(phase="error"),
            )
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
