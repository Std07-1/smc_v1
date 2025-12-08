"""Центральне джерело конфігурації AiOne_t.

У модулі зібрані константи Stage1 та допоміжні типи.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

__all__ = [
    "SymbolInfo",
    "NAMESPACE",
    "DATASTORE_BASE_DIR",
    "DEPTH_SEMAPHORE",
    "OI_SEMAPHORE",
    "KLINES_SEMAPHORE",
    "FAST_SYMBOLS_TTL_AUTO",
    "FAST_SYMBOLS_TTL_MANUAL",
    "FXCM_FAST_SYMBOLS",
    "FXCM_STALE_LAG_SECONDS",
    "FXCM_PRICE_TICK_CHANNEL",
    "FXCM_STATUS_CHANNEL",
    "PRICE_TICK_STALE_SECONDS",
    "PRICE_TICK_DROP_SECONDS",
    "REACTIVE_STAGE1",
    "SCREENING_LOOKBACK",
    "SCREENING_BATCH_SIZE",
    "SCREENING_LEVELS_UPDATE_EVERY",
    "DEFAULT_TIMEFRAME",
    "DEFAULT_LOOKBACK",
    "DEFAULT_TIMEZONE",
    "MIN_READY_PCT",
    "TRADE_REFRESH_INTERVAL",
    "WS_GAP_STATUS_PATH",
    "INTERVAL_TTL_MAP",
    "TICK_SIZE_MAP",
    "TICK_SIZE_BRACKETS",
    "TICK_SIZE_DEFAULT",
    "PROM_GAUGES_ENABLED",
    "PROM_HTTP_PORT",
    "SMC_BACKTEST_ENABLED",
    "SMC_RUNTIME_PARAMS",
    "REDIS_CACHE_TTL",
    "REDIS_CHANNEL_SMC_STATE",
    "REDIS_SNAPSHOT_KEY_SMC",
    "UI_SMC_PAYLOAD_SCHEMA_VERSION",
    "UI_SMC_SNAPSHOT_TTL_SEC",
    "UI_VIEWER_ALT_SCREEN_ENABLED",
    "UI_VIEWER_SNAPSHOT_DIR",
    "SMC_PIPELINE_ENABLED",
    "SMC_REFRESH_INTERVAL",
    "SMC_BATCH_SIZE",
]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASTORE_BASE_DIR = str(_PROJECT_ROOT / "datastore")
NAMESPACE = "ai_one"


# ───────────────────────────── Допоміжні типи ─────────────────────────────


class SymbolInfo(dict):
    """Легка обгортка навколо біржового exchangeInfo зі збереженням сирих полів."""

    def __init__(self, **data: Any) -> None:
        super().__init__(data)
        self.symbol = str(data.get("symbol", ""))

    def __getattr__(self, item: str) -> Any:
        try:
            return self[item]
        except KeyError as exc:  # pragma: no cover
            raise AttributeError(item) from exc


class _LoopBoundSemaphore:
    """Семафор, що створює свій asyncio.Semaphore для кожного event loop."""

    __slots__ = ("_value", "_per_loop")

    def __init__(self, value: int) -> None:
        self._value = max(1, int(value))
        self._per_loop: dict[int, asyncio.Semaphore] = {}

    def _get(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        key = id(loop)
        semaphore = self._per_loop.get(key)
        if semaphore is None:
            semaphore = asyncio.Semaphore(self._value)
            self._per_loop[key] = semaphore
        return semaphore

    async def acquire(self) -> None:
        await self._get().acquire()

    def release(self) -> None:
        self._get().release()

    async def __aenter__(self) -> _LoopBoundSemaphore:
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.release()


DEPTH_SEMAPHORE = _LoopBoundSemaphore(8)
OI_SEMAPHORE = _LoopBoundSemaphore(8)
KLINES_SEMAPHORE = _LoopBoundSemaphore(4)
REDIS_CACHE_TTL = 15 * 60

# ──────────────────────────────────────────────────────────────────────────────
# REDIS: КАНАЛИ ТА КЛЮЧІ
# ──────────────────────────────────────────────────────────────────────────────
#: Канал публікації чистого SMC-стану
REDIS_CHANNEL_SMC_STATE: str = f"{NAMESPACE}:ui:smc_state"

#: Ключ снапшота для SMC-пайплайна
REDIS_SNAPSHOT_KEY_SMC: str = f"{NAMESPACE}:ui:smc_snapshot"

#: Канал для адмін-команд (узгоджено з AdminCfg.commands_channel)
ADMIN_COMMANDS_CHANNEL: str = f"{NAMESPACE}:admin:commands"

#: Ключі для агрегованих статистик у Redis
STATS_CORE_KEY: str = f"{NAMESPACE}:stats:core"
STATS_HEALTH_KEY: str = f"{NAMESPACE}:stats:health"

# TTL для ключів (секунди)
UI_SNAPSHOT_TTL_SEC: int = 180
UI_SMC_SNAPSHOT_TTL_SEC: int = 180

# Версія схеми UI payload (для консюмерів/міграцій)
UI_PAYLOAD_SCHEMA_VERSION: str = "1.2"
UI_SMC_PAYLOAD_SCHEMA_VERSION: str = "1.2"
UI_VIEWER_ALT_SCREEN_ENABLED: bool = True
UI_VIEWER_SNAPSHOT_DIR: str = "tmp"

# ── Підготовчі прапорці для WS gap‑бекфілу (за замовчуванням вимкнено) ──
WS_GAP_BACKFILL: dict[str, int | bool] = {
    "enabled": True,
    # максимум хвилин до бекфілу REST при виявленні пропусків у live‑стрімі
    "max_minutes": 15,
    # TTL статусу ресинхронізації у Redis/UI (сек)
    "status_ttl": 15 * 60,
}

#: Redis-шлях для статусу WS-ресинхронізації (ai_one:stream:resync)
WS_GAP_STATUS_PATH: tuple[str, ...] = ("stream", "resync")

# ───────────────────────────── Stage1 параметри ─────────────────────────────

FAST_SYMBOLS_TTL_AUTO = 15 * 60
FAST_SYMBOLS_TTL_MANUAL = 60 * 60
# Окремий whitelist для FXCM-режиму — не змішуємо з історичними крипто-юніверсами
FXCM_FAST_SYMBOLS = [
    "xauusd",
    # "xagusd",  # у всій системі символи зберігаємо у lower-case
]

FXCM_STALE_LAG_SECONDS = 120
FXCM_PRICE_TICK_CHANNEL = "fxcm:price_tik"
# Канал агрегованого статусу конектора FXCM (process/market/price/ohlcv/session)
FXCM_STATUS_CHANNEL = "fxcm:status"
# Скільки секунд mid вважається «свіжим» для UI/алгоритмів
PRICE_TICK_STALE_SECONDS = 15
# Коли вважати снапшот повністю протухлим і видаляти його з кешу
PRICE_TICK_DROP_SECONDS = 120

REACTIVE_STAGE1 = False
SCREENING_LOOKBACK = 240
SCREENING_BATCH_SIZE = 12
SCREENING_LEVELS_UPDATE_EVERY = 30
DEFAULT_TIMEFRAME = "1m"
DEFAULT_LOOKBACK = 3
DEFAULT_TIMEZONE = "UTC"
MIN_READY_PCT = 0.6
TRADE_REFRESH_INTERVAL = 3  # цикл Stage1 синхронізовано з 2–3 сек FXCM тиками
SMC_REFRESH_INTERVAL = 5  # окремий цикл для SmcCore без Stage1 логіки
SMC_BATCH_SIZE = 12
SMC_PIPELINE_ENABLED = True


# ───────────────────────────── Логування / Метрики ───────────────────────────

PROM_GAUGES_ENABLED = False
PROM_HTTP_PORT = 9108

# SMC snapshot / backtest режим (CLI-утиліти)
SMC_BACKTEST_ENABLED: bool = False

# Робочі параметри SmcCore у Stage1 → UI
SMC_RUNTIME_PARAMS: dict[str, Any] = {
    "enabled": True,
    "tf_primary": "1m",
    "tfs_extra": ("5m", "15m", "1h"),
    "limit": 300,
    "max_concurrency": 4,
    "log_latency": True,
}
INTERVAL_TTL_MAP = {
    "1m": 90,
    "3m": 3 * 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}
TICK_SIZE_DEFAULT = 0.01
TICK_SIZE_BRACKETS = [
    (0.1, 0.0001),
    (1, 0.0005),
    (10, 0.001),
    (100, 0.01),
    (1_000, 0.1),
]
TICK_SIZE_MAP = {
    "xauusd": 0.01,
    "xagusd": 0.001,
}
