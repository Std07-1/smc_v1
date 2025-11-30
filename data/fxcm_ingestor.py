"""FXCM інжестор OHLCV-даних у UnifiedDataStore.

Шлях: ``data/fxcm_ingestor.py``

Призначення:
    • слухає Redis-канал з OHLCV-пакетами від окремого FXCM-конектора (Python 3.7);
    • перетворює JSON-повідомлення у DataFrame;
    • записує бари у UnifiedDataStore через put_bars(symbol, interval, bars).

Очікуваний формат повідомлення (JSON):
    {
      "symbol": "EURUSD",
      "tf": "1m",
      "bars": [
        {
          "open_time": 1764002100000,
          "close_time": 1764002159999,
          "open": 1.152495,
          "high": 1.152640,
          "low": 1.152450,
          "close": 1.152530,
          "volume": 149.0
        }
      ]
    }

Рішення:
    • інжестор не знає нічого про ForexConnect — тільки Redis;
    • нормалізація символів і tf (m1 → 1m) відбувається на стороні 3.7-конектора;
    • інжестор працює в тому ж процесі/loop, що й Stage1, використовуючи спільний UnifiedDataStore.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd
from redis.asyncio import Redis

from app.settings import settings
from data.unified_store import UnifiedDataStore

try:  # pragma: no cover - опціональна залежність
    from prometheus_client import Counter as PromCounter  # type: ignore[import]
except Exception:  # pragma: no cover - у тестах/CI клієнта може не бути
    PromCounter = None

logger = logging.getLogger("fxcm_ingestor")


FXCM_OHLCV_CHANNEL = "fxcm:ohlcv"


class _NoopCounter:
    def labels(self, *args: Any, **kwargs: Any) -> _NoopCounter:
        return self

    def inc(self, amount: float = 1.0) -> None:
        return None


def _build_counter(
    name: str, description: str, *, labelnames: tuple[str, ...] = ()
) -> Any:
    if PromCounter is None:
        return _NoopCounter()
    try:
        return PromCounter(name, description, labelnames=labelnames)
    except Exception:  # pragma: no cover - реєстр уже містить метрику
        return _NoopCounter()


PROM_FXCM_INVALID_SIG = _build_counter(
    "ai_one_fxcm_invalid_sig_total",
    "Кількість FXCM пакетів з некоректним або відсутнім HMAC.",
    labelnames=("reason",),
)
PROM_FXCM_UNSIGNED_PAYLOAD = _build_counter(
    "ai_one_fxcm_unsigned_payload_total",
    "Кількість FXCM пакетів без підпису при вимозі HMAC.",
)

_UNEXPECTED_SIG_LOGGED = False


def _bars_payload_to_df(bars: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """Конвертує список барів у DataFrame з очікуваними колонками.

    Навмисно не робимо складної валідації, щоб не гальмувати гарячий шлях.
    Перевірка схеми/монотонності покривається validate_on_write у UnifiedDataStore.
    """
    if not bars:
        return pd.DataFrame(
            columns=[
                "open_time",
                "close_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        )

    df = pd.DataFrame(bars)

    # Мінімальний sanity-check: потрібні базові колонки
    required_cols = {
        "open_time",
        "close_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }
    missing = required_cols - set(df.columns)
    if missing:
        logger.warning(
            "[FXCM_INGEST] Відсутні колонки у payload: %s, пропускаю пакет",
            sorted(missing),
        )
        return pd.DataFrame(
            columns=[
                "open_time",
                "close_time",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        )

    # Приводимо типи там, де це має значення для get_df/put_bars
    for col in ("open_time", "close_time"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Сортуємо по часу, щоб не ламати припущення merge/validate у сховищі
    df = df.sort_values("open_time").reset_index(drop=True)
    return df[
        [
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
        ]
    ]


def _normalize_signature(sig: Any) -> str | None:
    if sig is None:
        return None
    if isinstance(sig, bytes):
        value = sig.decode("utf-8", errors="ignore")
    else:
        value = str(sig)
    trimmed = value.strip()
    return trimmed or None


def _log_unexpected_sig_once(symbol: Any, interval: Any) -> None:
    global _UNEXPECTED_SIG_LOGGED
    if _UNEXPECTED_SIG_LOGGED:
        return
    logger.warning(
        "[FXCM_INGEST] Отримано підписаний пакет при вимкненому HMAC "
        "(symbol=%r, tf=%r). Пакет прийнято, але конфіги варто синхронізувати",
        symbol,
        interval,
    )
    _UNEXPECTED_SIG_LOGGED = True


def _verify_hmac_signature(
    payload: Mapping[str, Any],
    sig: str | None,
    *,
    secret: str | None,
    algo: str = "sha256",
) -> bool:
    """Перевіряє HMAC-підпис FXCM-пакету.

    True повертається лише коли secret відсутній і sig теж немає, або коли
    секрет заданий та підпис збігається. Інакше повертаємо False.
    """

    if secret is None:
        return sig is None

    if not sig:
        return False

    try:
        serialized = json.dumps(
            payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
    except (TypeError, ValueError):
        return False

    digest_name = (algo or "sha256").lower()
    digestmod = getattr(hashlib, digest_name, hashlib.sha256)
    try:
        digest = hmac.new(secret.encode("utf-8"), serialized, digestmod)
    except Exception:
        return False
    return hmac.compare_digest(digest.hexdigest(), sig)


async def _process_payload(
    store: UnifiedDataStore,
    payload: Mapping[str, Any],
    *,
    hmac_secret: str | None,
    hmac_algo: str,
    hmac_required: bool,
) -> tuple[int, str | None, str | None]:
    symbol = payload.get("symbol")
    interval = payload.get("tf")
    bars = payload.get("bars")

    if (
        not symbol
        or not interval
        or not isinstance(bars, Sequence)
        or isinstance(bars, (bytes, str))
    ):
        logger.warning(
            "[FXCM_INGEST] Некоректний payload: symbol=%r interval=%r bars_type=%r",
            symbol,
            interval,
            type(bars),
        )
        return 0, None, None

    sig = _normalize_signature(payload.get("sig"))
    base_payload = {"symbol": symbol, "tf": interval, "bars": bars}

    if hmac_secret:
        if not sig:
            PROM_FXCM_INVALID_SIG.labels(reason="missing").inc()
            if hmac_required:
                PROM_FXCM_UNSIGNED_PAYLOAD.inc()
                logger.warning(
                    "[FXCM_INGEST] Відкинуто пакет через відсутній HMAC "
                    "(symbol=%r, tf=%r)",
                    symbol,
                    interval,
                )
                return 0, None, None
            logger.warning(
                "[FXCM_INGEST] FXCM-пакет без HMAC (symbol=%r, tf=%r) — приймаємо, "
                "бо FXCM_HMAC_REQUIRED=False",
                symbol,
                interval,
            )
        elif not _verify_hmac_signature(
            base_payload, sig, secret=hmac_secret, algo=hmac_algo
        ):
            PROM_FXCM_INVALID_SIG.labels(reason="mismatch").inc()
            logger.warning(
                "[FXCM_INGEST] Відкинуто пакет через некоректний HMAC "
                "(symbol=%r, tf=%r)",
                symbol,
                interval,
            )
            return 0, None, None
    else:
        if sig:
            _log_unexpected_sig_once(symbol, interval)

    df = _bars_payload_to_df(bars)
    if df.empty:
        return 0, None, None

    symbol_norm = str(symbol).lower()
    interval_norm = str(interval).lower()

    try:
        await store.put_bars(symbol_norm, interval_norm, df)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[FXCM_INGEST] Помилка під час put_bars(%s, %s): %s",
            symbol_norm,
            interval_norm,
            exc,
        )
        return 0, None, None

    return len(df), symbol_norm, interval_norm


async def run_fxcm_ingestor(
    store: UnifiedDataStore,
    *,
    redis_host: str | None = None,
    redis_port: int | None = None,
    channel: str = FXCM_OHLCV_CHANNEL,
    log_every_n: int = 1,
    hmac_secret: str | None = None,
    hmac_algo: str = "sha256",
    hmac_required: bool = False,
) -> None:
    """Основний цикл інжестора FXCM → UnifiedDataStore.

    Аргументи:
        store: Спільний UnifiedDataStore, який вже використовують Stage1/Stage2.
        redis_host: Хост Redis; за замовчуванням береться з app.settings.
        redis_port: Порт Redis; за замовчуванням береться з app.settings.
        channel: Назва Redis Pub/Sub каналу, з якого читаємо OHLCV-пакети.
        log_every_n: Як часто логувати успішний інжест (щоб уникнути спаму).
        hmac_secret: Якщо задано — перевіряємо HMAC-підпис FXCM payload.
        hmac_algo: Назва алгоритму (наприклад, "sha256").
        hmac_required: True → усі пакети без валідного підпису відкидаємо.
    """
    host = redis_host or settings.redis_host
    port = redis_port or settings.redis_port
    normalized_secret = (
        hmac_secret.strip() if isinstance(hmac_secret, str) else hmac_secret
    )
    normalized_algo = (hmac_algo or "sha256").strip().lower() or "sha256"

    redis = Redis(host=host, port=port)
    pubsub = redis.pubsub()

    logger.info(
        "[FXCM_INGEST] Старт інжестора: host=%s port=%s channel=%s",
        host,
        port,
        channel,
    )

    await pubsub.subscribe(channel)

    processed = 0
    log_every_n = max(1, int(log_every_n))
    hmac_required = bool(hmac_required)

    try:
        async for message in pubsub.listen():
            if message is None:
                continue

            mtype = message.get("type")
            if mtype != "message":
                # subscribe/unsubscribe та інші службові події ігноруємо
                continue

            raw_data = message.get("data")
            if not raw_data:
                continue

            try:
                if isinstance(raw_data, bytes):
                    payload = json.loads(raw_data.decode("utf-8"))
                elif isinstance(raw_data, str):
                    payload = json.loads(raw_data)
                else:
                    # Нестандартний тип від Redis — намагаємось привести до str
                    payload = json.loads(str(raw_data))
            except json.JSONDecodeError:
                logger.warning(
                    "[FXCM_INGEST] Некоректний JSON у повідомленні з каналу %s",
                    channel,
                )
                continue

            if not isinstance(payload, dict):
                logger.warning(
                    "[FXCM_INGEST] Очікував dict у payload, отримав %r",
                    type(payload),
                )
                continue

            rows, symbol, interval = await _process_payload(
                store,
                payload,
                hmac_secret=normalized_secret,
                hmac_algo=normalized_algo,
                hmac_required=hmac_required,
            )

            if rows <= 0:
                continue

            processed += rows
            if processed % log_every_n == 0 and symbol and interval:
                logger.info(
                    "[FXCM_INGEST] Інгестовано барів: %d (останній пакет: %s %s, rows=%d)",
                    processed,
                    symbol,
                    interval,
                    rows,
                )
    except asyncio.CancelledError:
        # Очікуваний шлях завершення при зупинці пайплайна
        logger.info("[FXCM_INGEST] Отримано CancelledError, завершуємо роботу.")
    finally:
        try:
            await pubsub.unsubscribe(channel)
        except Exception:  # noqa: BLE001
            pass
        await pubsub.close()
        await redis.close()
        logger.info("[FXCM_INGEST] Інжестор FXCM зупинено коректно.")


__all__ = ["run_fxcm_ingestor"]
