"""S3: воркер, який просить конектор FXCM про warmup/backfill.

Призначення:
- періодично проходить по whitelist (symbol, tf) з fxcm_contract;
- виконує S2-перевірку history (insufficient/stale_tail);
- публікує команди в Redis (rate-limited), не лізучи у внутрішній кеш конектора.

Це best-effort механізм: навіть якщо FXCM market/price/ohlcv не ок,
ми можемо відправляти команди — виконання на стороні конектора.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from typing import Any

try:  # pragma: no cover
    from redis.asyncio import Redis
except Exception:  # pragma: no cover
    Redis = Any  # type: ignore[assignment]

from app.fxcm_history_state import compute_history_status, timeframe_to_ms
from config.config import (
    FXCM_COMMANDS_CHANNEL,
    SMC_RUNTIME_PARAMS,
    SMC_S2_STALE_K,
    SMC_S3_COMMANDS_CHANNEL,
    SMC_S3_COOLDOWN_SEC,
    SMC_S3_POLL_SEC,
    SMC_S3_REQUESTER_ENABLED,
)
from core.serialization import json_dumps, utc_now_ms
from data.fxcm_status_listener import get_fxcm_feed_state
from data.unified_store import UnifiedDataStore

logger = logging.getLogger("app.fxcm_warmup_requester")


# Runtime snapshot для Rich status bar.
# Це *локальний* стан процесу (не Redis), потрібен для прозорості: чи S3 живий,
# чи відправляє команди, і що саме було останнім.
_S3_RUNTIME_SNAPSHOT: dict[str, Any] = {
    "enabled": False,
    "sent_total": 0,
}


def get_s3_runtime_snapshot() -> dict[str, Any]:
    """Повертає shallow-copy runtime snapshot S3 requester-а.

    Використовується console status bar-ом для діагностики.
    """

    return dict(_S3_RUNTIME_SNAPSHOT)


def _update_s3_runtime_snapshot(**kwargs: Any) -> None:
    _S3_RUNTIME_SNAPSHOT.update(kwargs)


def _compute_lookback_minutes(*, tf_ms: int, min_history_bars: int) -> int:
    """Оцінює lookback_minutes з min_history_bars та tf."""

    tf_ms = max(1, int(tf_ms))
    bars = max(1, int(min_history_bars))
    minutes = math.ceil((bars * tf_ms) / 60_000.0)
    return max(1, int(minutes))


def _desired_lookback_bars() -> int:
    """Повертає бажаний lookback у барах для warmup/backfill.

    Вимога UX: requester має просити «останні відомі N свічок» (типово 300),
    щоб не блокувати систему на великих contract-порогах.
    """

    try:
        limit = int((SMC_RUNTIME_PARAMS or {}).get("limit", 300) or 300)
    except Exception:
        limit = 300
    return max(1, int(limit))


def _bars_for_tf_from_contract(*, tf_ms: int, contract_1m_bars: int) -> int:
    """Перераховує ціль контракту (в 1m барах) у bars для конкретного TF.

    У контракті ми зберігаємо одну цифру на символ: «еквівалент хвилин / 1m барів».
    Для TF>1m конвертуємо: bars = ceil(contract_minutes / minutes_per_bar).
    """

    tf_ms = max(1, int(tf_ms))
    contract_1m_bars = max(0, int(contract_1m_bars))
    if contract_1m_bars <= 0:
        return 0
    minutes_per_bar = max(1.0, tf_ms / 60_000.0)
    return max(1, int(math.ceil(contract_1m_bars / minutes_per_bar)))


@dataclass(slots=True)
class FxcmWarmupRequester:
    """Періодичний requester команд warmup/backfill."""

    redis: Redis  # type: ignore[type-arg]
    store: UnifiedDataStore
    allowed_pairs: set[tuple[str, str]]
    min_history_bars_by_symbol: dict[str, int]

    commands_channel: str = FXCM_COMMANDS_CHANNEL
    poll_sec: int = 60
    cooldown_sec: int = 900
    stale_k: float = 3.0

    _last_request_ms: dict[tuple[str, str, str], int] = field(default_factory=dict)

    async def run_forever(self) -> None:
        if not self.allowed_pairs:
            logger.info("[S3] allowed_pairs порожній — requester не стартує")
            return

        _update_s3_runtime_snapshot(
            enabled=True,
            channel=self.commands_channel,
            poll_sec=int(self.poll_sec),
            cooldown_sec=int(self.cooldown_sec),
            stale_k=float(self.stale_k),
            allowed_pairs=int(len(self.allowed_pairs)),
            started_ts_ms=utc_now_ms(),
        )

        logger.info(
            "[S3] Warmup requester стартував: pairs=%d poll=%ds cooldown=%ds channel=%s",
            len(self.allowed_pairs),
            self.poll_sec,
            self.cooldown_sec,
            self.commands_channel,
        )

        while True:
            await self._run_once()
            await asyncio.sleep(max(1, int(self.poll_sec)))

    async def _run_once(self) -> None:
        feed = get_fxcm_feed_state()
        fxcm_status = _build_fxcm_status_block(feed)
        now_ms = utc_now_ms()

        _update_s3_runtime_snapshot(
            last_loop_ts_ms=now_ms,
            active_issues=int(len(self._last_request_ms)),
        )

        for symbol, tf in sorted(self.allowed_pairs):
            sym = str(symbol).strip().lower()
            tf_norm = str(tf).strip().lower()
            if not sym or not tf_norm:
                continue

            desired_bars = _desired_lookback_bars()
            contract_1m_bars = int(self.min_history_bars_by_symbol.get(sym, 0) or 0)
            tf_ms = timeframe_to_ms(tf_norm) or 60_000
            contract_bars_tf = _bars_for_tf_from_contract(
                tf_ms=tf_ms,
                contract_1m_bars=contract_1m_bars,
            )
            min_bars = (
                max(int(desired_bars), int(contract_bars_tf))
                if contract_bars_tf > 0
                else int(desired_bars)
            )

            status = await compute_history_status(
                store=self.store,
                symbol=sym,
                timeframe=tf_norm,
                min_history_bars=min_bars,
                stale_k=self.stale_k,
                now_ms=now_ms,
            )

            if status.state == "ok":
                self._clear_active_issue(sym=sym, tf=tf_norm)
                continue

            cmd_type = None
            reason = None
            if status.needs_warmup:
                cmd_type = "fxcm_warmup"
                reason = "insufficient_history"
            elif status.needs_backfill:
                # Практика інтеграції: у FXCM конекторі backfill для 1m може бути не
                # реалізований (позначається як tick TF). Щоб не «стріляти в нікуди»,
                # для 1m просимо warmup з lookback_minutes.
                if tf_norm == "1m":
                    cmd_type = "fxcm_warmup"
                else:
                    cmd_type = "fxcm_backfill"
                reason = "stale_tail"

            if not cmd_type:
                continue

            key = (sym, tf_norm, cmd_type)
            if not self._rate_limit_ok(key=key, now_ms=now_ms):
                continue

            lookback_minutes = _compute_lookback_minutes(
                tf_ms=tf_ms,
                min_history_bars=min_bars,
            )

            payload: dict[str, Any] = {
                "type": cmd_type,
                "symbol": sym.upper(),
                "tf": tf_norm,
                "min_history_bars": min_bars,
                "lookback_bars": min_bars,
                "lookback_minutes": lookback_minutes,
                "reason": reason,
                "s2": {
                    "history_state": status.state,
                    "bars_count": status.bars_count,
                    "last_open_time_ms": status.last_open_time_ms,
                },
                "fxcm_status": fxcm_status,
            }

            try:
                await self.redis.publish(
                    self.commands_channel,
                    json_dumps(payload),
                )
                self._last_request_ms[key] = now_ms

                prev_total = int(_S3_RUNTIME_SNAPSHOT.get("sent_total") or 0)
                _update_s3_runtime_snapshot(
                    sent_total=prev_total + 1,
                    last_command={
                        "ts_ms": now_ms,
                        "type": cmd_type,
                        "symbol": sym.upper(),
                        "tf": tf_norm,
                        "reason": reason,
                        "channel": self.commands_channel,
                    },
                    active_issues=int(len(self._last_request_ms)),
                )

                logger.info(
                    "S3: send %s for %s %s (bars=%d, last_open=%s, reason=%s, channel=%s)",
                    cmd_type,
                    sym.upper(),
                    _pretty_tf(tf_norm),
                    int(status.bars_count),
                    str(status.last_open_time_ms),
                    reason,
                    self.commands_channel,
                )
            except Exception:
                logger.warning(
                    "[S3] Не вдалося publish команду (%s) у %s",
                    cmd_type,
                    self.commands_channel,
                    exc_info=True,
                )

    def _rate_limit_ok(self, *, key: tuple[str, str, str], now_ms: int) -> bool:
        last = self._last_request_ms.get(key)
        if last is None:
            return True
        cooldown_ms = max(1, int(self.cooldown_sec)) * 1000
        return (now_ms - int(last)) >= cooldown_ms

    def _clear_active_issue(self, *, sym: str, tf: str) -> None:
        """Скидає 'active issue' для (symbol, tf), коли стан повернувся в ok.

        Вимога: якщо history_state стає ok, ми очищаємо лічильники, щоб при
        майбутньому погіршенні можна було знову надіслати команду без очікування cooldown.
        """

        for cmd_type in ("fxcm_warmup", "fxcm_backfill"):
            self._last_request_ms.pop((sym, tf, cmd_type), None)

        _update_s3_runtime_snapshot(
            last_clear_ts_ms=utc_now_ms(),
            active_issues=int(len(self._last_request_ms)),
        )


def build_requester_from_config(
    *,
    redis: Redis,  # type: ignore[type-arg]
    store: UnifiedDataStore,
    allowed_pairs: set[tuple[str, str]] | None,
    min_history_bars_by_symbol: dict[str, int] | None,
) -> FxcmWarmupRequester | None:
    """Фабрика requester-а з config.config.

    Важливо: бізнес-параметри (enable/poll/cooldown/stale_k/channel) не керуються через ENV.
    """

    if not bool(SMC_S3_REQUESTER_ENABLED):
        return None

    pairs = allowed_pairs or set()
    mins = min_history_bars_by_symbol or {}

    channel = str(SMC_S3_COMMANDS_CHANNEL or FXCM_COMMANDS_CHANNEL)
    poll_sec = int(SMC_S3_POLL_SEC)
    cooldown_sec = int(SMC_S3_COOLDOWN_SEC)
    stale_k = float(SMC_S2_STALE_K)

    return FxcmWarmupRequester(
        redis=redis,
        store=store,
        allowed_pairs=pairs,
        min_history_bars_by_symbol=mins,
        commands_channel=channel,
        poll_sec=poll_sec,
        cooldown_sec=cooldown_sec,
        stale_k=stale_k,
    )


def _pretty_tf(tf: str) -> str:
    """Форматує 1m/5m/1h у стиль m1/m5/h1 для коротких логів."""

    token = (tf or "").strip().lower()
    if len(token) < 2:
        return token or "?"
    unit = token[-1]
    value = token[:-1]
    if unit in {"m", "h", "d"} and value.isdigit():
        return f"{unit}{value}"
    return token


def _normalize_market_state(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw in {"open", "opened"}:
        return "open"
    if raw in {"closed", "close"}:
        return "closed"
    return "unknown"


def _normalize_price_state(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw == "ok":
        return "ok"
    if raw in {"stale", "lag", "delayed"}:
        return "lag"
    if raw in {"down", "error", "fail", "failed"}:
        return "down"
    return "down" if raw else "down"


def _normalize_ohlcv_state(value: str | None) -> str:
    raw = (value or "").strip().lower()
    if raw == "ok":
        return "ok"
    if raw in {"lag", "stale", "delayed"}:
        return "delayed"
    if raw in {"down", "error", "fail", "failed"}:
        return "down"
    return "down" if raw else "down"


def _build_fxcm_status_block(feed: Any) -> dict[str, str]:
    """Будує стабільний diag-блок fxcm_status для команд.

    Конектор має право ігнорувати цей блок, але ми завжди додаємо його.
    """

    market = _normalize_market_state(getattr(feed, "market_state", None))
    price = _normalize_price_state(getattr(feed, "price_state", None))
    ohlcv = _normalize_ohlcv_state(getattr(feed, "ohlcv_state", None))
    return {"market": market, "price": price, "ohlcv": ohlcv}
