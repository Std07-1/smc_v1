"""Центральне джерело конфігурації AiOne_t.

У модулі зібрані константи Stage1 та допоміжні типи, необхідні для
холодного старту без зовнішніх YAML/ENV.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "FilterParams",
    "MetricResults",
    "SymbolInfo",
    "NAMESPACE",
    "DATASTORE_BASE_DIR",
    "DEPTH_SEMAPHORE",
    "OI_SEMAPHORE",
    "KLINES_SEMAPHORE",
    "FAST_SYMBOLS_TTL_AUTO",
    "FAST_SYMBOLS_TTL_MANUAL",
    "MANUAL_FAST_SYMBOLS_SEED",
    "CANARY_SYMBOLS",
    "PREFILTER_BASE_PARAMS",
    "PREFILTER_INTERVAL_SEC",
    "PRELOAD_1M_LOOKBACK_INIT",
    "PRELOAD_DAILY_DAYS",
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
    "STAGE1_PREFILTER_THRESHOLDS",
    "STAGE1_METRICS_BATCH",
    "STAGE1_PREFILTER_HEAVY_LIMIT",
    "STAGE1_MONITOR_PARAMS",
    "STAGE1_BEARISH_REASON_BONUS",
    "STAGE1_BEARISH_TRIGGER_TAGS",
    "USE_VOL_ATR",
    "DIRECTIONAL_PARAMS",
    "INTERVAL_TTL_MAP",
    "TICK_SIZE_MAP",
    "TICK_SIZE_BRACKETS",
    "TICK_SIZE_DEFAULT",
    "TRIGGER_NAME_MAP",
    "TRIGGER_TP_SL_SWAP_LONG",
    "TRIGGER_TP_SL_SWAP_SHORT",
    "PROM_GAUGES_ENABLED",
    "PROM_HTTP_PORT",
    "REDIS_CACHE_TTL",
]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASTORE_BASE_DIR = str(_PROJECT_ROOT / "datastore")
NAMESPACE = "ai_one"


# ───────────────────────────── Допоміжні типи ─────────────────────────────


@dataclass(slots=True)
class FilterParams:
    """Параметри Stage1 prefilter/оптимізованого відбору активів."""

    min_quote_volume: float = 2_000_000.0
    min_price_change: float = 2.5
    min_open_interest: float = 400_000.0
    min_orderbook_depth: float = 40_000.0
    min_atr_percent: float = 0.4
    max_symbols: int = 40
    dynamic: bool = True

    def dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MetricResults:
    """Статистика виконання Stage1 prefilter."""

    initial_count: int = 0
    prefiltered_count: int = 0
    filtered_count: int = 0
    result_count: int = 0
    elapsed_time: float = 0.0
    params: dict[str, Any] = field(default_factory=dict)

    def dict(self) -> dict[str, Any]:
        return asdict(self)


class SymbolInfo(dict):
    """Легка обгортка навколо Binance exchangeInfo зі збереженням сирих полів."""

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
#: Канал публікації агрегованого стану активів для UI-консюмера (нова схема v2)
REDIS_CHANNEL_ASSET_STATE: str = f"{NAMESPACE}:ui:asset_state"

#: Ключ для зберігання останнього знімка стану (для «холодного» старту UI)
REDIS_SNAPSHOT_KEY: str = f"{NAMESPACE}:ui:snapshot"

#: Канал для адмін-команд (узгоджено з AdminCfg.commands_channel)
ADMIN_COMMANDS_CHANNEL: str = f"{NAMESPACE}:admin:commands"

#: Ключі для агрегованих статистик у Redis
STATS_CORE_KEY: str = f"{NAMESPACE}:stats:core"
STATS_HEALTH_KEY: str = f"{NAMESPACE}:stats:health"

# TTL для ключів (секунди)
UI_SNAPSHOT_TTL_SEC: int = 180

# Версія схеми UI payload (для консюмерів/міграцій)
UI_PAYLOAD_SCHEMA_VERSION: str = "1.0"


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

# ───────────────────────────── Stage1 / Prefilter ─────────────────────────────

FAST_SYMBOLS_TTL_AUTO = 15 * 60
FAST_SYMBOLS_TTL_MANUAL = 60 * 60
MANUAL_FAST_SYMBOLS_SEED = [
    "btcusdt",
    "ethusdt",
    "solusdt",
    "tonusdt",
    "snxusdt",
]
CANARY_SYMBOLS = [sym.upper() for sym in MANUAL_FAST_SYMBOLS_SEED]
PREFILTER_BASE_PARAMS = {
    "min_depth": 50_000,
    "min_atr": 0.35,
    "dynamic": True,
}
PREFILTER_INTERVAL_SEC = 10 * 60
# Мінімально потрібна глибина 1m-історії для Stage1 (~EMA200 H1).
PRELOAD_1M_LOOKBACK_INIT = 26_600
PRELOAD_DAILY_DAYS = 45
REACTIVE_STAGE1 = False
SCREENING_LOOKBACK = 240
SCREENING_BATCH_SIZE = 12
SCREENING_LEVELS_UPDATE_EVERY = 30
DEFAULT_TIMEFRAME = "1m"
DEFAULT_LOOKBACK = 180
DEFAULT_TIMEZONE = "UTC"
MIN_READY_PCT = 0.6
TRADE_REFRESH_INTERVAL = 30
STAGE1_PREFILTER_THRESHOLDS = {
    "MIN_QUOTE_VOLUME": 2_000_000.0,
    "MIN_PRICE_CHANGE": 2.0,
    "MIN_OPEN_INTEREST": 400_000.0,
    "MAX_SYMBOLS": 40,
}
STAGE1_METRICS_BATCH = 15
STAGE1_PREFILTER_HEAVY_LIMIT = 250
STAGE1_MONITOR_PARAMS = {
    "vol_z_threshold": 2.2,
    "rsi_overbought": 68.0,
    "rsi_oversold": 32.0,
    "dynamic_rsi_multiplier": 1.1,
    "min_reasons_for_alert": 2,
}
STAGE1_BEARISH_REASON_BONUS = 0.15
STAGE1_BEARISH_TRIGGER_TAGS = (
    "whale_dump",
    "liquidity_sweep_down",
)
USE_VOL_ATR = True
DIRECTIONAL_PARAMS = {
    "w_short": 3,
    "min_total_volume": 250_000.0,
}

# ───────────────────────────── Логування / Метрики ───────────────────────────

PROM_GAUGES_ENABLED = False
PROM_HTTP_PORT = 9108
SMC_BACKTEST_ENABLED: bool = False
INTERVAL_TTL_MAP = {
    "1m": 90,
    "3m": 3 * 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}

TRIGGER_NAME_MAP = {
    "volume_spike": "volume_spike",
    "volatility_spike": "volatility_spike",
    "breakout_level": "breakout_level",
    "rsi_divergence": "rsi_divergence",
    "vwap_deviation": "vwap_deviation",
}
TRIGGER_TP_SL_SWAP_LONG = {"breakout_level"}
TRIGGER_TP_SL_SWAP_SHORT = {"breakout_level"}
TICK_SIZE_DEFAULT = 0.01
TICK_SIZE_BRACKETS = [
    (0.1, 0.0001),
    (1, 0.0005),
    (10, 0.001),
    (100, 0.01),
    (1_000, 0.1),
]
TICK_SIZE_MAP = {
    "btcusdt": 0.1,
    "ethusdt": 0.01,
    "solusdt": 0.001,
    "tonusdt": 0.0001,
    "snxusdt": 0.001,
}
