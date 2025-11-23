# app/thresholds.py

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from data.unified_store import UnifiedDataStore

log = logging.getLogger("thresholds")
log.setLevel(logging.WARNING)

# ──────────────────────────── Константи ──────────────────────────────
CACHE_TTL_DAYS: int = 14  # скільки днів тримати пороги в Redis
OPTUNA_SQLITE_URI = "sqlite:///storage/optuna.db"  # база Optuna (Heat-map, Dashboard)

# ══════════════════════════ SQLite-історія (опційно) ═════════════════════════
# import sqlite3
# DB_PATH = Path("storage/thresholds_history.db")
#
# def _get_conn() -> sqlite3.Connection:
#     """Повертає SQLite-коннектор, створюючи таблицю, якщо її ще нема."""
#     DB_PATH.parent.mkdir(parents=True, exist_ok=True)
#     conn = sqlite3.connect(DB_PATH)
#     conn.execute(
#         """
#         CREATE TABLE IF NOT EXISTS thresholds_history (
#             id           INTEGER PRIMARY KEY AUTOINCREMENT,
#             symbol       TEXT NOT NULL,
#             tuned_at     INTEGER NOT NULL,
#             payload_json TEXT NOT NULL,
#             study_uuid   TEXT,
#             comment      TEXT
#         )
#         """
#     )
#     return conn
# ══════════════════════════════════════════════════════════════════════════════


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _deepcopy_dict(data: Mapping[str, Any] | None) -> dict[str, Any]:
    if isinstance(data, Mapping):
        return dict(deepcopy(data))
    return {}


def _merge_dicts(base: Mapping[str, Any], addon: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(deepcopy(base))
    for key, value in addon.items():
        existing = result.get(key)
        if isinstance(value, Mapping) and isinstance(existing, Mapping):
            result[key] = _merge_dicts(existing, value)
        else:
            result[key] = deepcopy(value)
    return result


def _apply_override_path(
    container: dict[str, Any], path: list[str], delta: Any
) -> None:
    key = path[0]
    if len(path) == 1:
        current = container.get(key)
        if isinstance(delta, Mapping) and isinstance(current, Mapping):
            container[key] = _merge_dicts(current, delta)
            return
        if isinstance(current, (int, float)) and isinstance(delta, (int, float)):
            container[key] = round(current + float(delta), 6)
            return
        container[key] = deepcopy(delta)
        return

    child = container.get(key)
    if not isinstance(child, dict):
        child = {}
        container[key] = child
    _apply_override_path(child, path[1:], delta)


def _apply_state_overrides(
    snapshot: dict[str, Any], overrides: Mapping[str, Any]
) -> dict[str, Any]:
    if not isinstance(overrides, Mapping):
        return snapshot
    result = deepcopy(snapshot)
    for raw_key, delta in overrides.items():
        if not isinstance(raw_key, str):
            continue
        segments = raw_key.split(".")
        _apply_override_path(result, segments, delta)
    return result


# ────────────────────────────── Dataclass ─────────────────────────────────────
@dataclass
class Thresholds:
    """Гнучкі пороги для конкретного символу Stage1.

    Підтримує первинну конфігурацію (`config`), вкладені `signal_thresholds`
    та `state_overrides`, а також надає сумісні helper'и `effective_thresholds`
    і `to_dict` для стратегії Stage1.
    """

    symbol: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    low_gate: float = 0.006
    high_gate: float = 0.015
    atr_target: float = 0.50
    vol_z_threshold: float = 1.2
    vwap_deviation: float = 0.02
    min_atr_percent: float = 0.0
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    signal_thresholds: dict[str, Any] = field(default_factory=dict)
    state_overrides: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.config = _deepcopy_dict(self.config)
        self.signal_thresholds = _deepcopy_dict(self.signal_thresholds)
        self.state_overrides = _deepcopy_dict(self.state_overrides)
        self.meta = _deepcopy_dict(self.meta)
        if self.config:
            self._apply_mapping(self.config)
        self._normalize_numeric()

    def _apply_mapping(self, data: Mapping[str, Any]) -> None:
        aliases = {
            "volume_z_threshold": "vol_z_threshold",
            "vol_z_threshold": "vol_z_threshold",
            "low_gate": "low_gate",
            "high_gate": "high_gate",
            "atr_target": "atr_target",
            "vwap_deviation": "vwap_deviation",
            "min_atr_percent": "min_atr_percent",
            "rsi_overbought": "rsi_overbought",
            "rsi_oversold": "rsi_oversold",
        }
        for key, attr in aliases.items():
            if key in data and data[key] is not None:
                setattr(self, attr, _as_float(data[key], getattr(self, attr)))

        if not self.symbol:
            symbol_val = data.get("symbol")
            if isinstance(symbol_val, str):
                self.symbol = symbol_val.upper()

        if isinstance(data.get("signal_thresholds"), Mapping):
            self.signal_thresholds = _deepcopy_dict(data["signal_thresholds"])
        if isinstance(data.get("state_overrides"), Mapping):
            self.state_overrides = _deepcopy_dict(data["state_overrides"])
        if isinstance(data.get("meta"), Mapping):
            self.meta = _deepcopy_dict(data["meta"])

    def _normalize_numeric(self) -> None:
        self.low_gate = round(float(self.low_gate), 4)
        self.high_gate = round(float(self.high_gate), 4)
        self.atr_target = round(float(self.atr_target), 2)
        self.vol_z_threshold = round(float(self.vol_z_threshold), 2)
        self.vwap_deviation = round(float(self.vwap_deviation), 4)
        self.min_atr_percent = round(float(self.min_atr_percent), 4)
        self.rsi_overbought = round(float(self.rsi_overbought), 2)
        self.rsi_oversold = round(float(self.rsi_oversold), 2)

    # ───── Утиліти ─────
    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> Thresholds:
        """Створює Thresholds із довільного mapping (JSON/Redis/CSV)."""

        if not isinstance(data, Mapping):
            raise TypeError("Очікується mapping з полями порогів")
        payload = dict(data)
        config_payload = payload.get("config")
        if isinstance(config_payload, Mapping):
            cfg = dict(config_payload)
        else:
            cfg = payload
        kwargs: dict[str, Any] = {"config": cfg}
        for key in (
            "symbol",
            "low_gate",
            "high_gate",
            "atr_target",
            "vol_z_threshold",
            "volume_z_threshold",
            "vwap_deviation",
            "min_atr_percent",
            "rsi_overbought",
            "rsi_oversold",
        ):
            if key in payload:
                target_key = "vol_z_threshold" if key == "volume_z_threshold" else key
                kwargs[target_key] = payload[key]
        if isinstance(payload.get("signal_thresholds"), Mapping):
            kwargs["signal_thresholds"] = payload["signal_thresholds"]
        if isinstance(payload.get("state_overrides"), Mapping):
            kwargs["state_overrides"] = payload["state_overrides"]
        if isinstance(payload.get("meta"), Mapping):
            kwargs["meta"] = payload["meta"]
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        """Повертає копію актуальних порогів без посилань на внутрішній стан."""

        return {
            "symbol": self.symbol,
            "low_gate": self.low_gate,
            "high_gate": self.high_gate,
            "atr_target": self.atr_target,
            "vol_z_threshold": self.vol_z_threshold,
            "vwap_deviation": self.vwap_deviation,
            "min_atr_percent": self.min_atr_percent,
            "rsi_overbought": self.rsi_overbought,
            "rsi_oversold": self.rsi_oversold,
            "signal_thresholds": deepcopy(self.signal_thresholds),
            "state_overrides": deepcopy(self.state_overrides),
            "meta": deepcopy(self.meta),
        }

    def effective_thresholds(self, market_state: str | None) -> dict[str, Any]:
        """Повертає пороги з урахуванням `state_overrides` для стану ринку."""

        snapshot = self.to_dict()
        if market_state and self.state_overrides:
            overrides = self.state_overrides.get(market_state)
            if overrides:
                snapshot = _apply_state_overrides(snapshot, overrides)
        return snapshot


# ───────────────────────────── Redis-ключ ─────────────────────────────────────
def _redis_key(symbol: str) -> str:
    """Формує ключ у Redis для порогів символу."""
    return f"thresholds:{symbol}"


# ───────────────────────────── Збереження ─────────────────────────────────────
async def save_thresholds(
    symbol: str,
    thr: Thresholds,
    cache: UnifiedDataStore,
) -> None:
    """Зберігає Thresholds у Redis (+ JSON-бек-ап)."""
    key = _redis_key(symbol)
    payload_str = json.dumps(asdict(thr), ensure_ascii=False)
    payload = payload_str.encode("utf-8")
    ttl_seconds = int(timedelta(days=CACHE_TTL_DAYS).total_seconds())

    # 1) Redis
    await cache.store_in_cache(
        key,
        "global",
        payload,
        ttl=ttl_seconds,
        raw=True,
    )

    # 2) Локальний резерв (dev-mode, не впливає на Heroku slug)
    try:
        Path("optuna_runs").mkdir(exist_ok=True)
        (Path("optuna_runs") / f"{symbol}.json").write_text(
            json.dumps({"best_params": asdict(thr)}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:  # noqa: BLE001
        # Лише попередження — не критично у production
        print(f"[WARN] Cannot write local backup for {symbol}: {exc}")


async def save_thresholds_version(
    cache: UnifiedDataStore,
    symbol: str,
    thr: Thresholds,
    *,
    study_uuid: str | None = None,
    comment: str = "",
    ttl_days: int = CACHE_TTL_DAYS,
) -> None:
    """Запис «живих» порогів + (опційно) версію в SQLite-історію."""
    # Redis
    payload = json.dumps(asdict(thr)).encode("utf-8")
    ttl_seconds = int(timedelta(days=ttl_days).total_seconds())
    await cache.store_in_cache(
        _redis_key(symbol),
        "global",
        payload,
        ttl=ttl_seconds,
        raw=True,
    )

    # SQLite — вимкнено, залишено для майбутнього
    # conn = _get_conn()
    # with conn:
    #     conn.execute(
    #         "INSERT INTO thresholds_history "
    #         "(symbol, tuned_at, payload_json, study_uuid, comment) "
    #         "VALUES (?,?,?,?,?)",
    #         (
    #             symbol,
    #             int(time.time()),
    #             json.dumps(asdict(thr)),
    #             study_uuid,
    #             comment,
    #         ),
    #     )


# ───────────────────────────── Завантаження ──────────────────────────────────
async def load_thresholds(
    symbol: str,
    cache: UnifiedDataStore,
) -> Thresholds:
    """
    Завантажує Thresholds з Redis. Якщо ключа нема або JSON некоректний,
    повертає дефолтні значення Thresholds().

    Params:
        symbol – ticker, наприклад "BTCUSDT"
        cache  – екземпляр UnifiedDataStore для взаємодії з Redis
    """
    key = _redis_key(symbol)
    raw = await cache.fetch_from_cache(key, "global", raw=True)
    if not raw:
        log.debug(
            "[%s] load_thresholds: ключ %s відсутній у Redis, використовуємо дефолт",
            symbol,
            key,
        )
        return Thresholds()

    # raw може бути bytes або str
    raw_str = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
    log.debug("[%s] load_thresholds: знайдено в Redis: %s...", symbol, raw_str[:100])
    try:
        data = json.loads(raw_str)
        thr = Thresholds.from_mapping(data)
        log.debug("[%s] load_thresholds: повертаємо з Redis %s", symbol, thr)
        return thr
    except Exception as exc:
        log.warning(
            "[%s] load_thresholds: не вдалося розпарсити JSON (%s), використовуємо дефолт",
            symbol,
            exc,
        )
        return Thresholds()


# ──────────────────────── (опціонально) вибір за часом ───────────────────────
# def load_thresholds_by_time(symbol: str, ts: int) -> Thresholds | None:
#     """Повертає останні пороги *до* зазначеної мітки часу."""
#     conn = _get_conn()
#     row = conn.execute(
#         """
#         SELECT payload_json
#         FROM   thresholds_history
#         WHERE  symbol=? AND tuned_at<=?
#         ORDER  BY tuned_at DESC
#         LIMIT  1
#         """,
#         (symbol, ts),
#     ).fetchone()
#     return Thresholds.from_mapping(json.loads(row[0])) if row else None
