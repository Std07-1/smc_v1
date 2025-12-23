"""Центральне джерело конфігурації AiOne_t.

У модулі зібрані константи Stage1 та допоміжні типи.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

__all__ = [
    "SymbolInfo",
    "AI_ONE_MODE",
    "NAMESPACE",
    "DATASTORE_BASE_DIR",
    "DEPTH_SEMAPHORE",
    "OI_SEMAPHORE",
    "KLINES_SEMAPHORE",
    "FAST_SYMBOLS_TTL_AUTO",
    "FAST_SYMBOLS_TTL_MANUAL",
    "FXCM_FAST_SYMBOLS",
    "FXCM_STALE_LAG_SECONDS",
    "FXCM_COMMANDS_CHANNEL",
    "FXCM_OHLCV_CHANNEL",
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
    "REDIS_CHANNEL_SMC_VIEWER_EXTENDED",
    "REDIS_SNAPSHOT_KEY_SMC_VIEWER",
    "UI_SMC_PAYLOAD_SCHEMA_VERSION",
    "UI_SMC_SNAPSHOT_TTL_SEC",
    "UI_VIEWER_ALT_SCREEN_ENABLED",
    "UI_VIEWER_SNAPSHOT_DIR",
    "SMC_PIPELINE_ENABLED",
    "SMC_REFRESH_INTERVAL",
    "SMC_BATCH_SIZE",
    "SMC_MAX_ASSETS_PER_CYCLE",
    "SMC_CYCLE_BUDGET_MS",
    "_FALSE_ENV_VALUES",
]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASTORE_BASE_DIR = str(_PROJECT_ROOT / "datastore")


def _env_run_mode(default: str = "prod") -> str:
    """Повертає режим запуску: `prod` або `local`.

    Використовується як простий перемикач профілю, щоб:
    - локальний запуск не чіпав прод Redis;
    - дефолтні ключі/канали/namespace були передбачувані.

    Пріоритети:
    - якщо `AI_ONE_MODE` задано → беремо його;
    - інакше → `prod`.
    """

    raw = os.getenv("AI_ONE_MODE")
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if value in {"local", "dev"}:
        return "local"
    if value in {"prod", "production"}:
        return "prod"
    return default


AI_ONE_MODE: str = _env_run_mode("local")  # За замовчуванням локальний режим


def _default_namespace_for_mode(mode: str) -> str:
    mode_norm = str(mode or "").strip().lower()
    if mode_norm == "local":
        return "ai_one_local"
    return "ai_one"


def _env_namespace(default: str = "ai_one") -> str:
    """Повертає namespace для Redis ключів/каналів.

    Важливо: це інфраструктурний параметр.
    Використовується для ізоляції локальних/dev запусків від прод Redis.
    """

    raw = os.getenv("AI_ONE_NAMESPACE")
    if raw is None:
        return default
    value = str(raw).strip()
    return value or default


def _env_str(name: str, default: str) -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = str(raw).strip()
    return value or default


NAMESPACE = _env_namespace(_default_namespace_for_mode(AI_ONE_MODE))


def _default_fxcm_channel_prefix(mode: str) -> str:
    """Повертає дефолтний prefix каналів FXCM-конектора.

    Важливо:
    - FXCM конектор живе в окремому репо, і в цьому проєкті більшість коду/доків
        та утиліт орієнтуються на канонічні канали `fxcm:*`.
    - Якщо потрібно ізолювати dev/локальний конектор, задайте явний
        `FXCM_CHANNEL_PREFIX=fxcm_local` (або інший prefix) через ENV.
    """

    _ = str(mode or "").strip().lower()
    return "fxcm"


_FALSE_ENV_VALUES = {"0", "false", "no", "off"}

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

#: Канал публікації готового viewer_state (extended UI)
REDIS_CHANNEL_SMC_VIEWER_EXTENDED: str = f"{NAMESPACE}:ui:smc_viewer_extended"

#: Ключ снапшота viewer_state (мапа symbol -> SmcViewerState)
REDIS_SNAPSHOT_KEY_SMC_VIEWER: str = f"{NAMESPACE}:ui:smc_viewer_snapshot"

#: Канал для адмін-команд (узгоджено з AdminCfg.commands_channel)
ADMIN_COMMANDS_CHANNEL: str = f"{NAMESPACE}:admin:commands"

#: Канал команд для FXCM-конектора (S3 warmup/backfill requester -> connector subscriber)
#: Важливо: цей канал НЕ має namespace `ai_one:*`, бо конектор живе в окремому репо.
_FXCM_CHANNEL_PREFIX: str = _env_str(
    "FXCM_CHANNEL_PREFIX",
    _default_fxcm_channel_prefix(AI_ONE_MODE),
)

FXCM_COMMANDS_CHANNEL: str = _env_str(
    "FXCM_COMMANDS_CHANNEL",
    f"{_FXCM_CHANNEL_PREFIX}:commands",
)

#: Ключі для агрегованих статистик у Redis
STATS_CORE_KEY: str = f"{NAMESPACE}:stats:core"
STATS_HEALTH_KEY: str = f"{NAMESPACE}:stats:health"

# TTL для ключів (секунди)
UI_SNAPSHOT_TTL_SEC: int = 180
UI_SMC_SNAPSHOT_TTL_SEC: int = 180

# Версія схеми UI payload (для консюмерів/міграцій)
UI_PAYLOAD_SCHEMA_VERSION: str = "1.2"
UI_SMC_PAYLOAD_SCHEMA_VERSION: str = "smc_state_v1"
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
    # "eurusd",
    # "gbpusd",
    # "usdjpy",
    # "usdchf",
    # "usdcad",
    # "audusd",
]

#: Канал живих bid/ask/mid тиков від FXCM конектора
FXCM_OHLCV_CHANNEL: str = _env_str(
    "FXCM_OHLCV_CHANNEL",
    f"{_FXCM_CHANNEL_PREFIX}:ohlcv",
)

FXCM_PRICE_TICK_CHANNEL: str = _env_str(
    "FXCM_PRICE_TICK_CHANNEL",
    f"{_FXCM_CHANNEL_PREFIX}:price_tik",
)

FXCM_STALE_LAG_SECONDS = 120
# Канал агрегованого статусу конектора FXCM (process/market/price/ohlcv/session)
FXCM_STATUS_CHANNEL: str = _env_str(
    "FXCM_STATUS_CHANNEL",
    f"{_FXCM_CHANNEL_PREFIX}:status",
)
# Скільки секунд mid вважається «свіжим» для UI/алгоритмів
PRICE_TICK_STALE_SECONDS = 15
# Коли вважати снапшот повністю протухлим і видаляти його з кешу
PRICE_TICK_DROP_SECONDS = 120

# ───────────────────────────── S2/S3 (SMC-core) ─────────────────────────────
# Важливо: це бізнес/стратегічні параметри. Не керуємо ними через ENV.

# S2 поріг stale_tail: age_ms > stale_k * tf_ms
SMC_S2_STALE_K: float = 3.0

# S3 requester (warmup/backfill) — увімкнено для авто-прогріву/бекфілу.
SMC_S3_REQUESTER_ENABLED: bool = True
SMC_S3_POLL_SEC: int = 60
SMC_S3_COOLDOWN_SEC: int = 900
SMC_S3_COMMANDS_CHANNEL: str = FXCM_COMMANDS_CHANNEL

# Live auto-repair: негайний backfill/warmup при виявленні гепа у live-стрімі.
# ВАЖЛИВО:
# - за замовчуванням вимкнено (kill-switch) — щоб не створити зайве навантаження
#   на FXCM-конектор/Redis;
# - це НЕ прямі команди в FXCM: ми лише публікуємо payload у Redis-канал конектора;
# - використовує той самий FXCM_COMMANDS_CHANNEL, що і S3 requester.
SMC_LIVE_GAP_BACKFILL_ENABLED: bool = False
SMC_LIVE_GAP_BACKFILL_COOLDOWN_SEC: int = 120
SMC_LIVE_GAP_BACKFILL_LOOKBACK_BARS: int = 800
SMC_LIVE_GAP_BACKFILL_MAX_GAP_MINUTES: int = 180

# Rich status bar у консолі (TTY-гейт залишається в коді).
SMC_CONSOLE_STATUS_BAR_ENABLED: bool = True

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

# SMC capacity / scheduler v0
# 0 або <0 → legacy-режим (обробляємо всі ready-активи за цикл)
SMC_MAX_ASSETS_PER_CYCLE: int = 4

# М'який бюджет тривалості циклу (поки лише для логів/телеметрії)
SMC_CYCLE_BUDGET_MS: int = 400


# ───────────────────────────── Логування / Метрики ───────────────────────────

PROM_GAUGES_ENABLED = False
PROM_HTTP_PORT = 9108

# SMC snapshot / backtest режим (CLI-утиліти)
SMC_BACKTEST_ENABLED: bool = False

# SSOT TF-план для SMC (Stage0: TF-правда).
# - tf_exec: TF для «виконавчого» шару/живих метрик (не обов'язково primary для compute).
# - tf_structure: головний TF для структури/ліквідності/зон (tf_primary для SMC-core).
# - tf_context: HTF-контекст, який підтягуємо як додаткові фрейми (best-effort).
SMC_TF_PLAN: dict[str, Any] = {
    "tf_exec": "1m",
    "tf_structure": "5m",
    "tf_context": ("1h", "4h"),
}

# Робочі параметри SmcCore у Stage1 → UI
SMC_RUNTIME_PARAMS: dict[str, Any] = {
    "enabled": True,
    # TF-правда: compute відбувається на tf_structure (5m).
    "tf_primary": str(SMC_TF_PLAN["tf_structure"]),
    # Підтягуємо exec + HTF контекст + TF для UI/огляду.
    "tfs_extra": (
        str(SMC_TF_PLAN["tf_exec"]),
        *tuple(SMC_TF_PLAN["tf_context"]),
    ),
    # Stage6: анти-фліп/гістерезис живе поза SMC-core (в SmcStateManager).
    # Ці значення — бізнес-рейки UX, не керуємо через ENV.
    "stage6": {
        # Мінімальна пауза між змінами stable-сценарію.
        "ttl_sec": 180,
        # Скільки послідовних циклів новий сценарій має триматися.
        "confirm_bars": 2,
        # Наскільки нова впевненість має бути вищою за stable, щоб дозволити switch.
        # (confidence у Stage6 нормалізований у діапазон ~0.50..0.95)
        "switch_delta": 0.08,
        # Micro-events (Stage5 execution) — лише як підтвердження, НІКОЛИ не як вибір 4_2/4_3.
        # Працює тільки коли execution.meta.in_play=True, distance<=dmax та події в межах TTL.
        "micro_confirm_enabled": True,
        # TTL для актуальності micro-подій (сек).
        "micro_ttl_sec": 90,
        # Максимальна відстань до POI/target у ATR-одиницях (рахуємо від execution.meta.in_play_ref).
        "micro_dmax_atr": 0.80,
        # Буст confidence при повному підтвердженні (набір подій для конкретного raw сценарію).
        "micro_boost": 0.05,
        # Буст confidence при частковому підтвердженні (лише одна з потрібних подій).
        "micro_boost_partial": 0.02,
    },
    # Мінімальна історія (у барах) для старту SMC + S3 warmup/backfill requester.
    # Вимога UX: на старті підкачати хоча б ~300 останніх 1m барів
    # (а для інших TF — еквівалент за часом).
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
