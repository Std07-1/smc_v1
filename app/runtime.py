"""Утиліти запуску пайплайнів (Redis клієнт, FXCM таски, bootstrap)."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from redis.asyncio import Redis

from app.settings import load_datastore_cfg, settings
from data.fxcm_ingestor import run_fxcm_ingestor
from data.fxcm_price_stream import run_fxcm_price_stream_listener
from data.fxcm_status_listener import run_fxcm_status_listener
from data.unified_store import StoreConfig, StoreProfile, UnifiedDataStore

logger = logging.getLogger("app.runtime")
if not logger.handlers:
    logger.setLevel(logging.DEBUG)
    logger.addHandler(logging.StreamHandler())
    logger.propagate = False

BASE_DIR = Path(__file__).resolve().parent.parent
UI_EXPERIMENTAL_VIEW_ENABLED = True


def _log_smc_universe(cfg) -> None:
    """Логування contract-of-needs для SMC (fxcm_contract)."""

    universe = getattr(cfg, "smc_universe", None)
    contract = getattr(universe, "fxcm_contract", None) if universe else None
    symbols = getattr(contract, "symbols", None) if contract else None
    if not contract or not symbols:
        logger.warning(
            "[SMC_UNIVERSE] fxcm_contract відсутній або порожній — SMC працює без contract-of-needs"
        )
        return

    formatted: list[str] = []
    for entry in symbols:
        tfs = "/".join(entry.tfs)
        status = "(disabled)" if not entry.enabled else ""
        formatted.append(f"{entry.id}:{tfs}:{entry.min_history_bars}{status}")

    logger.info(
        "[SMC_UNIVERSE] fxcm_contract v%s symbols=%s",
        contract.version,
        "; ".join(formatted),
    )


def create_redis_client(*, decode_responses: bool = False) -> tuple[Redis, str]:
    """Створює Redis-клієнт на базі pydantic Settings."""

    kwargs: dict[str, Any] = {
        "host": settings.redis_host,
        "port": settings.redis_port,
    }
    if decode_responses:
        kwargs["decode_responses"] = True
    client = Redis(**kwargs)
    return client, f"{settings.redis_host}:{settings.redis_port}"


def _build_allowed_pairs(cfg) -> set[tuple[str, str]] | None:
    """Формує whitelist (symbol, tf) з fxcm_contract або повертає None."""

    contract = getattr(getattr(cfg, "smc_universe", None), "fxcm_contract", None)
    symbols = getattr(contract, "symbols", None) if contract else None
    if not contract or not symbols:
        return None

    pairs: set[tuple[str, str]] = set()
    for entry in symbols:
        if not entry.enabled:
            continue
        for tf in entry.tfs:
            pairs.add((entry.id.strip().lower(), tf.strip().lower()))
    return pairs or None


def _build_contract_min_history_bars(cfg) -> dict[str, int] | None:
    """Формує мапу symbol->min_history_bars з fxcm_contract або повертає None."""

    contract = getattr(getattr(cfg, "smc_universe", None), "fxcm_contract", None)
    symbols = getattr(contract, "symbols", None) if contract else None
    if not contract or not symbols:
        return None

    out: dict[str, int] = {}
    for entry in symbols:
        if not getattr(entry, "enabled", True):
            continue
        sym = str(getattr(entry, "id", "")).strip().lower()
        if not sym:
            continue
        try:
            out[sym] = int(getattr(entry, "min_history_bars", 0) or 0)
        except Exception:
            continue
    return out or None


def start_fxcm_tasks(
    store_handler: UnifiedDataStore,
    *,
    allowed_pairs: set[tuple[str, str]] | None = None,
) -> list[asyncio.Task[Any]]:
    """Запускає інжестор та FXCM статус/price-stream лістенери."""

    tasks: list[asyncio.Task[Any]] = []

    if allowed_pairs is None:
        logger.info(
            "[SMC_UNIVERSE] FXCM інжестор працює в legacy-mode (без фільтра за контрактом)"
        )
    else:
        pairs_str = ", ".join(f"{s}:{tf}" for (s, tf) in sorted(allowed_pairs))
        logger.info(
            "[SMC_UNIVERSE] Активований universe-фільтр для FXCM: %s",
            pairs_str,
        )

    def _launch(
        factory: Callable[[], Coroutine[Any, Any, Any]],
        success_msg: str,
        fail_prefix: str,
    ) -> None:
        try:
            task = asyncio.create_task(factory())
            tasks.append(task)
            logger.info(success_msg)
        except Exception as exc:  # pragma: no cover
            logger.warning("%s: %s", fail_prefix, exc, exc_info=True)

    _launch(
        lambda: run_fxcm_ingestor(
            store_handler,
            hmac_secret=settings.fxcm_hmac_secret,
            hmac_algo=settings.fxcm_hmac_algo,
            hmac_required=settings.fxcm_hmac_required,
            allowed_pairs=allowed_pairs,
        ),
        "[Pipeline] FXCM інжестор запущено",
        "[Pipeline] Не вдалося запустити FXCM інжестор",
    )

    _launch(
        lambda: run_fxcm_status_listener(
            redis_host=settings.redis_host,
            redis_port=settings.redis_port,
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


def launch_experimental_viewer() -> None:
    """Запускає extended viewer CLI без альтернатив."""

    module_name = "UI.ui_consumer_experimental_entry"
    if UI_EXPERIMENTAL_VIEW_ENABLED:
        logger.info("[UI] Увімкнено experimental viewer під флагом")
    proj_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    viewer_args = ["-m", module_name]

    if sys.platform.startswith("win"):
        command = ["start", "cmd", "/k", "python", *viewer_args]
        subprocess.Popen(command, shell=True, cwd=proj_root)
    else:
        term = shutil.which("gnome-terminal")
        if not term:
            logger.info(
                "UI consumer terminal not available (gnome-terminal not found); skipping launch."
            )
            return
        try:
            command = [term, "--", "python3", *viewer_args]
            subprocess.Popen(command, cwd=proj_root)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Не вдалося запустити UI consumer: %s", exc)


async def bootstrap() -> tuple[UnifiedDataStore, Any]:
    """Ініціалізує UnifiedDataStore та запускає maintenance loop; повертає store і cfg."""

    cfg = load_datastore_cfg()
    logger.info(
        "[Launch] datastore.yaml loaded: namespace=%s base_dir=%s",
        cfg.namespace,
        cfg.base_dir,
    )
    _log_smc_universe(cfg)
    redis, redis_source = create_redis_client()
    logger.info("[Launch] Redis client created via %s", redis_source)
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
    return store, cfg


async def noop_healthcheck() -> None:
    """Легкий healthcheck-плейсхолдер, що прокидається раз на 2 хвилини."""

    while True:
        await asyncio.sleep(120)


__all__ = (
    "BASE_DIR",
    "bootstrap",
    "create_redis_client",
    "launch_experimental_viewer",
    "noop_healthcheck",
    "_build_allowed_pairs",
    "_build_contract_min_history_bars",
    "start_fxcm_tasks",
)
