"""Консольний snapshot статусу пайплайна.

Раніше тут був Rich Live status bar. Його прибрано на користь простого логування,
але ми залишаємо `build_status_snapshot()` для тестів та потенційних інтеграцій.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

from data.fxcm_status_listener import FxcmFeedState

try:  # pragma: no cover - S3 requester може бути вимкнений/не імпортуватись у тестах
    from app.fxcm_warmup_requester import get_s3_runtime_snapshot
except Exception:  # pragma: no cover

    def get_s3_runtime_snapshot() -> dict[str, Any]:  # type: ignore[override]
        return {}


def _stderr_is_tty() -> bool:
    """Перевіряє TTY саме по stderr."""

    try:
        return bool(sys.stderr.isatty())
    except Exception:
        return False


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_iso_datetime(value: Any) -> datetime | None:
    """Парсить ISO-рядок дати/часу у datetime(UTC)."""

    if value is None:
        return None
    if isinstance(value, datetime):
        dt_value = value
    else:
        text = _coerce_str(value)
        if not text:
            return None
        try:
            dt_value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None

    if dt_value.tzinfo is None:
        dt_value = dt_value.replace(tzinfo=UTC)
    return dt_value.astimezone(UTC)


def _format_next_open(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt_value = value
    else:
        text = _coerce_str(value)
        if not text:
            return None
        try:
            dt_value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text

    if dt_value.tzinfo is None:
        dt_value = dt_value.replace(tzinfo=UTC)
    dt_utc = dt_value.astimezone(UTC).replace(microsecond=0)
    return dt_utc.strftime("%Y-%m-%d %H:%M UTC")


def _format_short_duration(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    try:
        seconds_value = float(seconds)
    except (TypeError, ValueError):
        return None
    seconds_value = max(0.0, seconds_value)
    total = int(seconds_value)
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m{secs:02d}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes:02d}"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours:02d}"


def _format_poll_interval(seconds: float | None) -> str | None:
    """Форматує інтервал опитування (poll) без округлення до 0s."""

    if seconds is None:
        return None
    try:
        seconds_value = float(seconds)
    except (TypeError, ValueError):
        return None
    seconds_value = max(0.0, seconds_value)
    if seconds_value < 1.0:
        return f"{int(round(seconds_value * 1000.0))}ms"
    if seconds_value < 10.0:
        return f"{seconds_value:.1f}s"
    return f"{int(round(seconds_value))}s"


def _format_uptime(seconds: float | None) -> str | None:
    """Форматує час роботи процесу у зручному вигляді.

    Вимога UX: показувати дні, коли тривалість перевищує 23:59.
    """

    if seconds is None:
        return None
    try:
        seconds_value = float(seconds)
    except (TypeError, ValueError):
        return None
    if not (seconds_value >= 0.0):
        return None

    total_seconds = int(max(0.0, seconds_value))
    days, remainder = divmod(total_seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, secs = divmod(remainder, 60)

    if days > 0:
        return f"{days}d {hours:02d}h{minutes:02d}m"
    if hours > 0:
        return f"{hours}h{minutes:02d}m"
    if minutes > 0:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def build_status_snapshot(
    *,
    smc_payload: Mapping[str, Any] | None,
    fxcm_state: FxcmFeedState | None,
    redis_connected: bool,
    sleep_for: float | None,
    uptime_seconds: float | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Будує уніфікований snapshot для ConsoleStatusBar.

    Підтримує 2 джерела:
    - SMC snapshot із Redis (payload від `publish_smc_state`), якщо доступний.
    - Поточний `FxcmFeedState` із `fxcm_status_listener`.
    """

    meta = smc_payload.get("meta") if isinstance(smc_payload, Mapping) else None
    if not isinstance(meta, Mapping):
        meta = {}

    mode = (
        _coerce_str(meta.get("pipeline_state")) or _coerce_str(meta.get("state")) or "?"
    )

    fxcm_block = None
    if isinstance(smc_payload, Mapping):
        fxcm_block = smc_payload.get("fxcm") or meta.get("fxcm")
    if not isinstance(fxcm_block, Mapping):
        fxcm_block = {}

    market_state = (
        _coerce_str(fxcm_block.get("market_state"))
        or _coerce_str(getattr(fxcm_state, "market_state", None))
        or "unknown"
    ).lower()
    price_state = (
        _coerce_str(fxcm_block.get("price_state"))
        or _coerce_str(getattr(fxcm_state, "price_state", None))
        or "unknown"
    ).lower()
    ohlcv_state = (
        _coerce_str(fxcm_block.get("ohlcv_state"))
        or _coerce_str(getattr(fxcm_state, "ohlcv_state", None))
        or "unknown"
    ).lower()

    market_open = market_state == "open"

    lag_seconds = _coerce_float(fxcm_block.get("lag_seconds"))
    if lag_seconds is None:
        lag_seconds = _coerce_float(getattr(fxcm_state, "lag_seconds", None))

    next_open = (
        fxcm_block.get("next_open_utc")
        or getattr(fxcm_state, "next_open_utc", None)
        or getattr(fxcm_state, "next_open_ms", None)
    )

    idle_reason = _coerce_str(meta.get("fxcm_idle_reason")) or _coerce_str(
        meta.get("cycle_reason")
    )

    cycle_reason = _coerce_str(meta.get("cycle_reason"))
    fxcm_idle_reason = _coerce_str(meta.get("fxcm_idle_reason"))

    # Явний стан SMC: чи рахуємо важкий цикл, чи «чекаємо».
    smc_state = "WAIT"
    mode_upper = str(mode or "?").upper()
    if mode_upper in {"COLD", "WARMUP"}:
        # UX: режим прогріву не повинен виглядати як IDLE,
        # навіть якщо цикл зараз «загейтений» станом FXCM.
        smc_state = "WARMUP"
    elif mode_upper == "IDLE" or cycle_reason == "smc_idle_fxcm_status":
        smc_state = "IDLE"
    elif cycle_reason == "smc_screening":
        smc_state = "RUN"
    elif cycle_reason == "smc_insufficient_data":
        smc_state = "WARMUP"

    smc_reason = fxcm_idle_reason or cycle_reason

    # SMC meta (pipeline/cycle) — корисно бачити у status bar.
    pipeline_ready_assets = _coerce_int(meta.get("pipeline_ready_assets"))
    pipeline_assets_total = _coerce_int(meta.get("pipeline_assets_total"))
    pipeline_ready_pct = _coerce_float(meta.get("pipeline_ready_pct"))
    pipeline_processed_assets = _coerce_int(meta.get("pipeline_processed_assets"))
    pipeline_skipped_assets = _coerce_int(meta.get("pipeline_skipped_assets"))
    cycle_seq = _coerce_int(meta.get("cycle_seq") or meta.get("seq"))
    cycle_duration_ms = _coerce_float(meta.get("cycle_duration_ms"))

    # Вік snapshot (секунди від останньої публікації UI payload).
    now_utc = now_utc or datetime.now(tz=UTC)
    published_dt = (
        _parse_iso_datetime(meta.get("ts"))
        or _parse_iso_datetime(meta.get("cycle_ready_ts"))
        or _parse_iso_datetime(meta.get("cycle_started_ts"))
    )
    snapshot_age_seconds = (
        max(0.0, (now_utc - published_dt).total_seconds()) if published_dt else None
    )

    # "Конект" з конектором: дивимось на свіжість fxcm:status.
    status_ts = _coerce_float(fxcm_block.get("status_ts"))
    if status_ts is None:
        status_ts = _coerce_float(getattr(fxcm_state, "status_ts", None))
    connector_age_seconds = None
    connector_state = "unknown"
    if status_ts is not None:
        connector_age_seconds = max(0.0, float(now_utc.timestamp() - float(status_ts)))
        if connector_age_seconds <= 10.0:
            connector_state = "ok"
        elif connector_age_seconds <= 60.0:
            connector_state = "lag"
        else:
            connector_state = "down"

    # Узгодження: якщо ticks_alive=true, а market_state=closed (але конектор не down),
    # то для UI/консолі вважаємо market=open, щоб не вводити в оману.
    if (
        market_state == "closed"
        and price_state == "ok"
        and connector_state in {"ok", "lag"}
    ):
        market_state = "open"
        market_open = True

    # Додаткові FXCM деталі.
    process_state = (
        _coerce_str(fxcm_block.get("process_state"))
        or _coerce_str(getattr(fxcm_state, "process_state", None))
        or "unknown"
    ).lower()

    session_name = _coerce_str(fxcm_block.get("session_name")) or _coerce_str(
        getattr(fxcm_state, "session_name", None)
    )
    session_state = _coerce_str(fxcm_block.get("session_state")) or _coerce_str(
        getattr(fxcm_state, "session_state", None)
    )
    session_seconds_to_close = _coerce_float(
        fxcm_block.get("session_seconds_to_close")
        or getattr(fxcm_state, "session_seconds_to_close", None)
    )
    session_seconds_to_next_open = _coerce_float(
        fxcm_block.get("session_seconds_to_next_open")
        or getattr(fxcm_state, "session_seconds_to_next_open", None)
    )

    # UX: якщо ринок закритий, не показуємо sess як open.
    # Також `to_close` в режимі closed плутає (показуємо лише `to_open`).
    session_state_lower = (session_state or "").strip().lower()
    if not market_open:
        if session_state_lower not in {"error", "down", "fail"}:
            session_state = "CLOSED"
        session_seconds_to_close = None
    else:
        session_seconds_to_next_open = None

    # S2 summary (з meta, якщо SMC producer його додає).
    s2_insufficient = _coerce_int(meta.get("s2_insufficient_assets"))
    s2_stale_tail = _coerce_int(meta.get("s2_stale_tail_assets"))
    s2_unknown = _coerce_int(meta.get("s2_unknown_assets"))
    s2_ok = _coerce_int(meta.get("s2_ok_assets"))
    s2_active_symbol = _coerce_str(meta.get("s2_active_symbol"))
    s2_active_state = _coerce_str(meta.get("s2_active_state"))
    s2_active_age_ms = _coerce_int(meta.get("s2_active_age_ms"))

    # S3 runtime (локально у процесі; якщо requester не запущений — буде порожньо).
    s3_runtime = get_s3_runtime_snapshot() or {}
    if not isinstance(s3_runtime, Mapping):
        s3_runtime = {}
    s3_enabled = bool(s3_runtime.get("enabled"))
    s3_channel = _coerce_str(s3_runtime.get("channel"))
    s3_poll_sec = _coerce_int(s3_runtime.get("poll_sec"))
    s3_cooldown_sec = _coerce_int(s3_runtime.get("cooldown_sec"))
    s3_sent_total = _coerce_int(s3_runtime.get("sent_total"))
    s3_active_issues = _coerce_int(s3_runtime.get("active_issues"))
    last_cmd = s3_runtime.get("last_command")
    if not isinstance(last_cmd, Mapping):
        last_cmd = {}
    s3_last_type = _coerce_str(last_cmd.get("type"))
    s3_last_symbol = _coerce_str(last_cmd.get("symbol"))
    s3_last_tf = _coerce_str(last_cmd.get("tf"))
    s3_last_reason = _coerce_str(last_cmd.get("reason"))
    s3_last_ts_ms = _coerce_int(last_cmd.get("ts_ms"))
    s3_last_age_seconds = None
    if s3_last_ts_ms is not None:
        s3_last_age_seconds = max(
            0.0, (now_utc.timestamp() * 1000.0 - s3_last_ts_ms) / 1000.0
        )

    return {
        "mode": mode,
        "market_open": market_open,
        "calendar_open": market_open,  # у smc_v1 немає окремого календарного прапорця
        "ticks_alive": price_state == "ok",
        "redis_connected": bool(redis_connected),
        "lag_seconds": lag_seconds,
        "next_open": next_open,
        "idle_reason": idle_reason,
        "cycle_reason": cycle_reason,
        "fxcm_idle_reason": fxcm_idle_reason,
        "smc_state": smc_state,
        "smc_reason": smc_reason,
        "sleep_for": sleep_for,
        "uptime_seconds": uptime_seconds,
        "fxcm_price_state": price_state,
        "fxcm_ohlcv_state": ohlcv_state,
        "fxcm_market_state": market_state,
        "fxcm_process_state": process_state,
        "session_name": session_name,
        "session_state": session_state,
        "session_seconds_to_close": session_seconds_to_close,
        "session_seconds_to_next_open": session_seconds_to_next_open,
        "pipeline_ready_assets": pipeline_ready_assets,
        "pipeline_assets_total": pipeline_assets_total,
        "pipeline_ready_pct": pipeline_ready_pct,
        "pipeline_processed_assets": pipeline_processed_assets,
        "pipeline_skipped_assets": pipeline_skipped_assets,
        "cycle_seq": cycle_seq,
        "cycle_duration_ms": cycle_duration_ms,
        "snapshot_age_seconds": snapshot_age_seconds,
        "connector_state": connector_state,
        "connector_age_seconds": connector_age_seconds,
        "s2_insufficient_assets": s2_insufficient,
        "s2_stale_tail_assets": s2_stale_tail,
        "s2_unknown_assets": s2_unknown,
        "s2_ok_assets": s2_ok,
        "s2_active_symbol": s2_active_symbol,
        "s2_active_state": s2_active_state,
        "s2_active_age_ms": s2_active_age_ms,
        "s3_enabled": s3_enabled,
        "s3_channel": s3_channel,
        "s3_poll_sec": s3_poll_sec,
        "s3_cooldown_sec": s3_cooldown_sec,
        "s3_sent_total": s3_sent_total,
        "s3_active_issues": s3_active_issues,
        "s3_last_type": s3_last_type,
        "s3_last_symbol": s3_last_symbol,
        "s3_last_tf": s3_last_tf,
        "s3_last_reason": s3_last_reason,
        "s3_last_age_seconds": s3_last_age_seconds,
    }


async def run_console_status_bar(
    *,
    redis_conn: Redis,
    snapshot_key: str,
    refresh_per_second: float = 4.0,
    poll_interval_seconds: float | None = None,
    console: Any | None = None,
) -> None:
    """Сумісний no-op.

    Rich status bar прибрано. Функція лишається, щоб не ламати старі імпорти/таски.
    """

    _ = (redis_conn, snapshot_key, refresh_per_second, poll_interval_seconds, console)
    return None


__all__ = (
    "build_status_snapshot",
    "run_console_status_bar",
)
