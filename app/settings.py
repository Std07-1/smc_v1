"""Конфігураційні моделі застосунку (Redis, DataStore, Prometheus, Admin).

Шлях: ``app/settings.py``

Використовує pydantic для декларативних моделей та YAML-файл для DataStore частини.

Уніфікація: базові константи (namespace, base_dir, admin channel) тягнемо з
`config.config` як єдиного джерела правди.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.env import select_env_file
from config.config import (
    DATASTORE_BASE_DIR as CFG_DATASTORE_BASE_DIR,
    FXCM_OHLCV_CHANNEL,
    FXCM_PRICE_TICK_CHANNEL,
    FXCM_STATUS_CHANNEL,
    NAMESPACE as CFG_NAMESPACE,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DATASTORE_CFG = _PROJECT_ROOT / "config" / "datastore.yaml"
logger = logging.getLogger("app.settings")
if not logger.handlers:
    logger.addHandler(logging.NullHandler())

_ENV_FILE = select_env_file(_PROJECT_ROOT)
load_dotenv(_ENV_FILE)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",  # ігноруємо невідомі змінні замість ValidationError
    )

    redis_host: str = "localhost"
    redis_port: int = 6379
    telegram_token: str | None = None
    admin_id: int = 0
    # Permit disabling Admin Pub/Sub loop via ENV override (e.g., ADMIN_ENABLED=false)
    admin_enabled: bool | None = None
    # Додаткові (не критичні) змінні середовища для сумісності зі старою конфігурацією
    log_level: str | None = None
    log_to_file: bool | None = None

    # FXCM конектор у цьому репо НЕ використовує user/password/access_token напряму.
    # Єдині налаштування, які потрібні тут — HMAC (для перевірки підписів) і назви каналів.
    fxcm_hmac_secret: str | None = None
    fxcm_hmac_algo: str = "sha256"  # алгоритм HMAC-підпису для FXCM
    fxcm_hmac_required: bool = True  # чи вимагати HMAC-підписи від FXCM
    fxcm_ohlcv_channel: str = FXCM_OHLCV_CHANNEL
    fxcm_price_tick_channel: str = FXCM_PRICE_TICK_CHANNEL
    fxcm_status_channel: str = FXCM_STATUS_CHANNEL

    # Проста валідація полів перенесена на рівень запуску/конфігів; додаткові
    # pydantic-валідатори не використовуємо тут для сумісності зі stubs mypy.

    # Робастний парсер для admin_enabled з ENV (обробляє пробіли/регістр)
    @field_validator("admin_enabled", mode="before")
    @classmethod
    def _coerce_admin_enabled(cls, v):  # type: ignore[no-untyped-def]
        if v is None:
            return v
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            s = v.strip().lower()
            if s in {"1", "true", "yes", "on"}:
                return True
            if s in {"0", "false", "no", "off", ""}:
                return False
        return v

    @field_validator("fxcm_hmac_secret", mode="before")
    @classmethod
    def _strip_fxcm_hmac_secret(cls, v):  # type: ignore[no-untyped-def]
        if v is None:
            return None
        if isinstance(v, str):
            value = v.strip()
            return value or None
        return v

    @field_validator("fxcm_hmac_algo", mode="before")
    @classmethod
    def _normalize_fxcm_hmac_algo(cls, v):  # type: ignore[no-untyped-def]
        if v is None:
            return "sha256"
        value = str(v).strip().lower()
        return value or "sha256"

    @field_validator("fxcm_hmac_required", mode="before")
    @classmethod
    def _coerce_fxcm_hmac_required(cls, v):  # type: ignore[no-untyped-def]
        if v is None:
            return False
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            s = v.strip().lower()
            if s in {"1", "true", "yes", "on"}:
                return True
            if s in {"0", "false", "no", "off", ""}:
                return False
        return bool(v)

    @field_validator(
        "fxcm_ohlcv_channel",
        "fxcm_price_tick_channel",
        "fxcm_status_channel",
        mode="before",
    )
    @classmethod
    def _normalize_fxcm_channels(  # type: ignore[no-untyped-def]
        cls, v, info: ValidationInfo
    ):
        if v is None:
            return v
        text = str(v).strip()
        if text:
            return text
        default_map = {
            "fxcm_ohlcv_channel": FXCM_OHLCV_CHANNEL,
            "fxcm_price_tick_channel": FXCM_PRICE_TICK_CHANNEL,
            "fxcm_status_channel": FXCM_STATUS_CHANNEL,
        }
        field_name = info.field_name or ""
        return default_map.get(field_name, text)


settings = Settings()  # буде валідовано під час імпорту

# Уніфіковані значення з config.config
REDIS_NAMESPACE = CFG_NAMESPACE
DATASTORE_BASE_DIR = CFG_DATASTORE_BASE_DIR
RAM_BUFFER_MAX_BARS = 50000  # Максимальна кількість барів у RAMBuffer на symbol/timeframe (30d 1m ≈ 43_200)

# Дозволені таймфрейми для SMC contract-of-needs.
# Етап 1: робимо TF-правду фізичною — UDS має тримати реальні 5m/1h/4h ряди.
SMC_SUPPORTED_TFS: tuple[str, ...] = ("1m", "5m", "1h", "4h")


class SmcUniverseSymbolCfg(BaseModel):
    """Опис одиничного символу у SMC contract-of-needs."""

    id: str
    tfs: list[str]
    min_history_bars: int
    enabled: bool = True

    @field_validator("id", mode="before")
    @classmethod
    def _normalize_id(cls, v: Any) -> str:
        text = str(v or "").strip().lower()
        if not text:
            raise ValueError("id символу не може бути порожнім")
        return text

    @field_validator("tfs")
    @classmethod
    def _validate_tfs(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("tfs має містити хоча б один таймфрейм")
        cleaned: list[str] = []
        for tf in v:
            tf_norm = str(tf or "").strip().lower()
            if tf_norm not in SMC_SUPPORTED_TFS:
                raise ValueError(
                    f"Непідтримуваний таймфрейм {tf_norm!r}; дозволено {SMC_SUPPORTED_TFS}"
                )
            cleaned.append(tf_norm)
        return cleaned

    @field_validator("min_history_bars")
    @classmethod
    def _validate_min_history(cls, v: int) -> int:
        try:
            value = int(v)
        except (TypeError, ValueError) as exc:
            raise ValueError("min_history_bars має бути цілим числом") from exc
        if value < 100:
            raise ValueError("min_history_bars має бути не менше 100")
        # Потрібно для кейсів на кшталт 14 днів 1m історії (~20160 барів).
        if value > 200000:
            raise ValueError("min_history_bars має бути не більше 200000")
        return value


class SmcFxcmContractCfg(BaseModel):
    """Contract-of-needs для FXCM джерела (версія + список символів)."""

    version: int = 1
    symbols: list[SmcUniverseSymbolCfg] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ensure_unique_symbol_tf(self) -> SmcFxcmContractCfg:
        seen: set[tuple[str, str]] = set()
        for entry in self.symbols:
            for tf in entry.tfs:
                key = (entry.id, tf)
                if key in seen:
                    raise ValueError(
                        f"Дублікат символу/TF у контрактi: {entry.id} {tf}"
                    )
                seen.add(key)
        return self


class SmcUniverseCfg(BaseModel):
    """Контейнер для SMC universe контрактів (fxcm тощо)."""

    fxcm_contract: SmcFxcmContractCfg | None = None


class Profile(BaseModel):
    name: str = "small"
    ram_limit_mb: int = 512
    max_symbols_hot: int = 96
    hot_ttl_sec: int = 6 * 3600
    warm_ttl_sec: int = 24 * 3600
    flush_batch_max: int = 8
    flush_queue_soft: int = 200
    flush_queue_hard: int = 1000


class TradeUpdaterCfg(BaseModel):
    skipped_ewma_alpha: float = 0.3
    backoff_multiplier: float = 1.5
    max_backoff_sec: int = 300
    drift_warn_high: float = 2.5
    drift_warn_low: float = 0.5
    pressure_warn: float = 2.0  # skipped_ewma / active_trades
    cycle_histogram_buckets: list[float] = Field(
        default_factory=lambda: [0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120, 300]
    )
    # ── Optional optimization hooks (disabled by default) ──
    auto_interval_scale_enabled: bool = (
        False  # enable adaptive interval scaling when pressure stays high
    )
    auto_interval_scale_cycles: int = (
        3  # how many consecutive high-pressure cycles before scaling interval
    )
    auto_interval_scale_factor: float = (
        1.25  # multiplier applied to dynamic_interval when triggered
    )
    auto_interval_scale_cap: float = 900.0  # hard cap for scaled interval

    auto_alpha_enabled: bool = False  # adapt skipped_ewma_alpha based on turbulence
    alpha_min: float = 0.05
    alpha_max: float = 0.6
    alpha_step: float = 0.05  # step to increase/decrease
    alpha_turbulence_drift: float = (
        2.0  # drift threshold to consider turbulent (above high warn still counts)
    )
    alpha_turbulence_pressure: float = (
        1.5  # pressure threshold to consider turbulent (below pressure_warn for pre-empt)
    )
    alpha_calm_drift: float = 1.05  # drift below this AND pressure low => calm
    alpha_calm_pressure: float = 0.5
    alpha_calm_cycles: int = (
        5  # consecutive calm cycles before lowering alpha (longer memory)
    )

    skip_reasons_top_n: int = 5  # number of top skip reasons to publish (if enabled)
    publish_skip_reasons: bool = False

    dynamic_priority_enabled: bool = (
        False  # future: temporarily drop low-priority symbols under pressure
    )
    dynamic_priority_min_active: int = 10  # don't drop if active universe already small


class AdminCfg(BaseModel):
    enabled: bool = True
    # Уникаємо прямого імпорту константи під час імпорту модуля,
    # щоб не створювати крихкі залежності; значення еквівалентне
    # config.config.ADMIN_COMMANDS_CHANNEL
    commands_channel: str = f"{CFG_NAMESPACE}:admin:commands"
    health_ping_sec: int = 30


class DataStoreCfg(BaseModel):
    namespace: str = REDIS_NAMESPACE
    base_dir: str = DATASTORE_BASE_DIR
    profile: Profile = Profile()
    trade_updater: TradeUpdaterCfg = TradeUpdaterCfg()
    intervals_ttl: dict[str, int] = Field(
        default_factory=lambda: {
            "1m": 21600,
            "5m": 43200,
            "15m": 86400,
            "1h": 259200,
            "4h": 604800,
            "1d": 2592000,
        }
    )
    write_behind: bool = True
    validate_on_write: bool = True
    validate_on_read: bool = True
    io_retry_attempts: int = 3
    io_retry_backoff: float = 0.25
    admin: AdminCfg = AdminCfg()
    smc_universe: SmcUniverseCfg = SmcUniverseCfg()


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(
            f"Файл конфігурації {path} має бути мапою, а не {type(loaded)!r}"
        )
    return loaded


def load_datastore_cfg(path: str | Path | None = None) -> DataStoreCfg:
    cfg_path = Path(path) if path else _DEFAULT_DATASTORE_CFG
    if not cfg_path.exists():
        logger.warning(
            "Файл %s не знайдено, використовуємо значення за замовчуванням",
            cfg_path,
        )
        return DataStoreCfg()
    try:
        payload = _read_yaml(cfg_path)
    except FileNotFoundError:
        logger.warning(
            "Файл %s зник під час читання, повертаємо дефолтний DataStoreCfg",
            cfg_path,
        )
        return DataStoreCfg()
    except yaml.YAMLError as exc:  # pragma: no cover - винятковий випадок
        logger.error("Не вдалося розпарсити %s: %s", cfg_path, exc)
        raise ValueError(f"Некоректний YAML у {cfg_path}") from exc

    cfg = DataStoreCfg(**payload)

    # Профілі запуску (prod/local) та ізоляція namespace.
    # Мета: одним прапорцем уникати ситуації, коли локальний запуск пише у прод Redis.
    env_mode = str(os.getenv("AI_ONE_MODE") or "").strip().lower()
    env_ns = os.getenv("AI_ONE_NAMESPACE")
    chosen_ns: str | None = None
    if env_ns is not None and str(env_ns).strip():
        chosen_ns = str(env_ns).strip()
    elif env_mode in {"local", "dev"}:
        chosen_ns = "ai_one_local"
    if chosen_ns:
        try:
            cfg = cfg.model_copy(update={"namespace": chosen_ns})
        except Exception:
            pass

    # Нормалізація base_dir:
    # - YAML часто задає відносний шлях (напр. "./datastore"), який залежить від CWD;
    # - у прод/службах CWD може бути не корінь репозиторію → UI стартує з 1 бару.
    # Тому:
    # 1) якщо base_dir абсолютний — лишаємо;
    # 2) якщо відносний — пробуємо резолвити відносно кореня проєкту,
    #    а якщо такого каталогу немає — відносно папки YAML.
    try:
        raw_base = str(getattr(cfg, "base_dir", "") or "").strip()
        if raw_base:
            base_path = Path(raw_base).expanduser()
            if not base_path.is_absolute():
                candidate_project = (_PROJECT_ROOT / base_path).resolve()
                candidate_yaml = (cfg_path.resolve().parent / base_path).resolve()
                chosen = (
                    candidate_project
                    if candidate_project.exists()
                    else (
                        candidate_yaml if candidate_yaml.exists() else candidate_project
                    )
                )
                cfg = cfg.model_copy(update={"base_dir": str(chosen)})
            else:
                cfg = cfg.model_copy(update={"base_dir": str(base_path.resolve())})
    except Exception:
        # best-effort: не ламаємо bootstrap через нормалізацію шляху
        pass

    return cfg
