"""Консольний status bar для пайплайна через Rich Live.

Ціль: показувати "живий" короткий статус (SMC/FXCM/Redis) в одному рядку,
щоб він не конфліктував з логами RichHandler у PowerShell/VS Code.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from config.config import SMC_CONSOLE_STATUS_BAR_ENABLED
from data.fxcm_status_listener import FxcmFeedState, get_fxcm_feed_state
from utils.rich_console import get_rich_console

try:  # pragma: no cover - S3 requester може бути вимкнений/не імпортуватись у тестах
    from app.fxcm_warmup_requester import get_s3_runtime_snapshot
except Exception:  # pragma: no cover

    def get_s3_runtime_snapshot() -> dict[str, Any]:  # type: ignore[override]
        return {}


logger = logging.getLogger("app.console_status_bar")


def _stderr_is_tty() -> bool:
    """Перевіряє TTY саме по stderr.

    Це важливо, бо і RichHandler (логи), і Live (status bar) пишуть у stderr.
    """

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


class ConsoleStatusBar:
    """Живий status bar у консолі через Rich Live."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        refresh_per_second: float = 4.0,
        console: Console | None = None,
    ) -> None:
        # Вимикаємо bar, якщо stderr не TTY.
        if console is None:
            console = get_rich_console()

        # Важливо: орієнтуємось саме на stderr.
        self._enabled = bool(enabled) and _stderr_is_tty() and console is not None
        self._console = console
        self._live: Live | None = None
        self._refresh_per_second = max(1.0, float(refresh_per_second))
        self._spinner: Spinner | None = None

    def start(self) -> None:
        if not self._enabled or self._console is None:
            return
        if self._live is not None:
            return
        self._spinner = Spinner("dots")
        self._live = Live(
            self._render_panel({}),
            console=self._console,
            refresh_per_second=self._refresh_per_second,
            transient=True,
            redirect_stderr=True,
            redirect_stdout=False,
        )
        self._live.__enter__()

    def stop(self) -> None:
        if self._live is None:
            return
        try:
            self._live.__exit__(None, None, None)
        finally:
            self._live = None
            self._spinner = None

    def update(self, snapshot: Mapping[str, Any]) -> None:
        if self._live is None:
            return
        self._live.update(self._render_panel(snapshot))

    def _render_panel(self, snapshot: Mapping[str, Any]) -> Panel:
        mode = str(snapshot.get("mode") or "?")
        market_open = bool(snapshot.get("market_open"))
        ticks_alive = bool(snapshot.get("ticks_alive"))
        redis_ok = bool(snapshot.get("redis_connected"))

        market_style = "green" if market_open else "yellow"
        redis_style = "green" if redis_ok else "red"
        ticks_style = "green" if ticks_alive else "red"

        # Додаткове фарбування по станам FXCM.
        price_state = str(snapshot.get("fxcm_price_state") or "unknown").lower()
        if price_state not in {"ok", "unknown"}:
            ticks_style = "yellow" if price_state in {"stale", "lag"} else "red"

        headline = Text.assemble(
            ("mode=", "dim"),
            (mode, "bold"),
            ("  |  market=", "dim"),
            ("open" if market_open else "closed", market_style),
            ("  |  ticks=", "dim"),
            ("alive" if ticks_alive else "down", ticks_style),
            ("  |  redis=", "dim"),
            ("ok" if redis_ok else "down", redis_style),
        )

        rows: list[tuple[Text, Text]] = []

        def _state_style(value: str) -> str:
            normalized = (value or "").strip().lower()
            if normalized in {"ok", "open", "opened", "active", "up", "ready"}:
                return "green"
            if normalized in {"stale", "lag", "degraded", "slow"}:
                return "yellow"
            if normalized in {"down", "error", "fail", "failed", "dead"}:
                return "red"
            if normalized in {"unknown", "?", ""}:
                return "dim"
            return "dim"

        smc_state = str(snapshot.get("smc_state") or "?").upper()
        smc_reason = _coerce_str(snapshot.get("smc_reason"))
        smc_style = "dim"
        if smc_state == "RUN":
            smc_style = "green"
        elif smc_state == "IDLE":
            smc_style = "yellow"
        elif smc_state == "WARMUP":
            smc_style = "cyan"

        smc_text = Text.assemble((smc_state, smc_style))
        if smc_reason:
            reason_style = "dim"
            if smc_reason == "fxcm_market_closed":
                reason_style = "yellow"
            smc_text.append("  ", style="dim")
            smc_text.append(smc_reason, style=reason_style)
        rows.append((Text("smc", style="dim"), smc_text))

        # Конект з конектором: свіжість fxcm:status.
        conn_state = str(snapshot.get("connector_state") or "unknown").lower()
        conn_age = _coerce_float(snapshot.get("connector_age_seconds"))
        if conn_state != "unknown" or conn_age is not None:
            age_label = (
                _format_short_duration(conn_age) if conn_age is not None else None
            )
            conn_text = Text.assemble(
                (conn_state, _state_style(conn_state)),
            )
            if age_label:
                conn_text.append("  age=", style="dim")
                conn_text.append(age_label, style=_state_style(conn_state))
            rows.append((Text("conn", style="dim"), conn_text))

        # S2 summary: що зараз «болить» по історії UDS.
        s2_ins = _coerce_int(snapshot.get("s2_insufficient_assets")) or 0
        s2_stale = _coerce_int(snapshot.get("s2_stale_tail_assets")) or 0
        s2_unk = _coerce_int(snapshot.get("s2_unknown_assets")) or 0
        s2_ok = _coerce_int(snapshot.get("s2_ok_assets"))
        s2_sym = _coerce_str(snapshot.get("s2_active_symbol"))
        s2_state = _coerce_str(snapshot.get("s2_active_state"))
        s2_age_ms = _coerce_int(snapshot.get("s2_active_age_ms"))

        s2_total_issues = int(s2_ins + s2_stale + s2_unk)
        if s2_total_issues > 0 or s2_ok is not None:
            style = (
                "green"
                if s2_total_issues == 0
                else ("red" if s2_stale > 0 else "yellow")
            )
            s2_text = Text.assemble(
                ("issues=", "dim"),
                (str(s2_total_issues), style),
                ("  ins=", "dim"),
                (str(s2_ins), "yellow" if s2_ins > 0 else "dim"),
                ("  stale=", "dim"),
                (str(s2_stale), "red" if s2_stale > 0 else "dim"),
                ("  unk=", "dim"),
                (str(s2_unk), "dim"),
            )
            if s2_sym and s2_state:
                s2_text.append("  act=", style="dim")
                s2_text.append(str(s2_sym).upper(), style="cyan")
                s2_text.append(":", style="dim")
                s2_text.append(str(s2_state), style=_state_style(str(s2_state)))
                if s2_age_ms is not None:
                    s2_text.append("  age=", style="dim")
                    s2_text.append(
                        _format_short_duration(s2_age_ms / 1000.0) or "?", style="cyan"
                    )
            rows.append((Text("s2", style="dim"), s2_text))

        # S3 (команди): остання команда + стан requester-а.
        s3_enabled = bool(snapshot.get("s3_enabled"))
        s3_channel = _coerce_str(snapshot.get("s3_channel"))
        s3_sent_total = _coerce_int(snapshot.get("s3_sent_total"))
        s3_active = _coerce_int(snapshot.get("s3_active_issues"))
        s3_last_type = _coerce_str(snapshot.get("s3_last_type"))
        s3_last_symbol = _coerce_str(snapshot.get("s3_last_symbol"))
        s3_last_tf = _coerce_str(snapshot.get("s3_last_tf"))
        s3_last_reason = _coerce_str(snapshot.get("s3_last_reason"))
        s3_last_age = _coerce_float(snapshot.get("s3_last_age_seconds"))

        if s3_enabled or s3_sent_total is not None or s3_last_type:
            base = Text.assemble(
                ("on" if s3_enabled else "off", "green" if s3_enabled else "dim"),
            )
            if s3_channel:
                base.append("  ch=", style="dim")
                base.append(s3_channel, style="cyan")
            if s3_sent_total is not None or s3_active is not None:
                base.append("  sent=", style="dim")
                base.append(
                    str(s3_sent_total if s3_sent_total is not None else "?"),
                    style="cyan",
                )
                base.append("  act=", style="dim")
                base.append(
                    str(s3_active if s3_active is not None else "?"), style="cyan"
                )
            if s3_last_type and s3_last_symbol and s3_last_tf:
                base.append("  last=", style="dim")
                base.append(s3_last_type, style="cyan")
                base.append(" ", style="dim")
                base.append(s3_last_symbol, style="cyan")
                base.append(" ", style="dim")
                base.append(s3_last_tf, style="cyan")
                if s3_last_reason:
                    base.append("  reason=", style="dim")
                    base.append(s3_last_reason, style="dim")
                if s3_last_age is not None:
                    base.append("  age=", style="dim")
                    base.append(
                        _format_short_duration(s3_last_age) or f"{s3_last_age:.0f}s",
                        style="cyan",
                    )
            rows.append((Text("s3", style="dim"), base))

        age_seconds = _coerce_float(snapshot.get("snapshot_age_seconds"))
        if age_seconds is not None and age_seconds >= 0:
            age_label = _format_short_duration(age_seconds) or f"{age_seconds:.1f}s"
            rows.append((Text("age", style="dim"), Text(age_label, style="cyan")))

        ready_assets = _coerce_int(snapshot.get("pipeline_ready_assets"))
        assets_total = _coerce_int(snapshot.get("pipeline_assets_total"))
        ready_pct = _coerce_float(snapshot.get("pipeline_ready_pct"))
        if ready_assets is not None or assets_total is not None:
            left = str(ready_assets) if ready_assets is not None else "?"
            right = str(assets_total) if assets_total is not None else "?"
            pct_label = (
                f" ({int(round(max(0.0, min(1.0, ready_pct)) * 100.0))}%)"
                if ready_pct is not None
                else ""
            )
            rows.append(
                (
                    Text("pipe", style="dim"),
                    Text(f"{left}/{right}{pct_label}", style="cyan"),
                )
            )

        processed = _coerce_int(snapshot.get("pipeline_processed_assets"))
        skipped = _coerce_int(snapshot.get("pipeline_skipped_assets"))
        if processed is not None or skipped is not None:
            proc_label = str(processed) if processed is not None else "?"
            skip_label = str(skipped) if skipped is not None else "?"
            rows.append(
                (
                    Text("cap", style="dim"),
                    Text(f"proc={proc_label} skip={skip_label}", style="dim"),
                )
            )

        cycle_ms = _coerce_float(snapshot.get("cycle_duration_ms"))
        cycle_seq = _coerce_int(snapshot.get("cycle_seq"))
        if cycle_ms is not None or cycle_seq is not None:
            parts: list[str] = []
            if cycle_seq is not None:
                parts.append(f"#{cycle_seq}")
            if cycle_ms is not None:
                parts.append(f"{cycle_ms:.0f}ms")
            cycle_text = Text(" ".join(parts), style="dim")
            uptime_label = _format_uptime(_coerce_float(snapshot.get("uptime_seconds")))
            if uptime_label:
                cycle_text.append("   ", style="dim")
                cycle_text.append("up=", style="dim")
                cycle_text.append(uptime_label, style="cyan")

            rows.append((Text("cycle", style="dim"), cycle_text))

        fxcm_proc = str(snapshot.get("fxcm_process_state") or "unknown").lower()
        fxcm_price = str(snapshot.get("fxcm_price_state") or "unknown").lower()
        fxcm_ohlcv = str(snapshot.get("fxcm_ohlcv_state") or "unknown").lower()
        if fxcm_proc != "unknown" or fxcm_price != "unknown" or fxcm_ohlcv != "unknown":
            fxcm_text = Text.assemble(
                ("proc=", "dim"),
                (fxcm_proc, _state_style(fxcm_proc)),
                ("  price=", "dim"),
                (fxcm_price, _state_style(fxcm_price)),
                ("  ohlcv=", "dim"),
                (fxcm_ohlcv, _state_style(fxcm_ohlcv)),
            )
            rows.append(
                (
                    Text("fxcm", style="dim"),
                    fxcm_text,
                )
            )

        session_name = _coerce_str(snapshot.get("session_name"))
        session_state = _coerce_str(snapshot.get("session_state"))
        to_close = _format_short_duration(
            _coerce_float(snapshot.get("session_seconds_to_close"))
        )
        to_open = _format_short_duration(
            _coerce_float(snapshot.get("session_seconds_to_next_open"))
        )
        if session_name or session_state or to_close or to_open:
            left = session_name or "?"
            right = (session_state or "?").lower()

            state_style = "dim"
            if right in {"open", "opened", "ok", "active"}:
                state_style = "green"
            elif right in {"closed", "closing", "holiday"}:
                state_style = "yellow"
            elif right in {"error", "down", "fail"}:
                state_style = "red"

            sess_text = Text.assemble(
                (left, "cyan"), (":", "dim"), (right, state_style)
            )
            if to_close:
                sess_text.append("  to_close=", style="dim")
                sess_text.append(to_close, style="cyan")
            if to_open:
                sess_text.append("  to_open=", style="dim")
                sess_text.append(to_open, style="cyan")

            rows.append((Text("sess", style="dim"), sess_text))

        lag = _coerce_float(snapshot.get("lag_seconds"))
        if lag is not None and lag > 0:
            lag_label = _format_short_duration(lag) or f"{lag:.1f}s"
            rows.append((Text("lag", style="dim"), Text(lag_label, style="cyan")))

        next_open = _format_next_open(snapshot.get("next_open"))
        if next_open:
            rows.append(
                (Text("next_open", style="dim"), Text(f"≥ {next_open}", style="cyan"))
            )

        poll_for = _coerce_float(snapshot.get("sleep_for"))
        poll_label = _format_poll_interval(poll_for)
        if poll_label:
            rows.append((Text("poll", style="dim"), Text(poll_label, style="dim")))

        if not rows:
            rows.append(
                (
                    Text("status", style="dim"),
                    Text("очікуємо оновлення стану…", style="dim"),
                )
            )

        table = Table.grid(expand=True)
        table.add_column(width=2)
        table.add_column(width=22)
        table.add_column(ratio=1)
        spinner = self._spinner or Text(" ")
        table.add_row(
            spinner,
            Text("SMC pipeline", style="bold cyan"),
            Text("працюємо", style="bold"),
        )
        table.add_row(Text(""), Text(""), headline)
        for key, value in rows[:10]:
            table.add_row(Text(""), key, value)

        border_style = "green" if market_open else "yellow"
        return Panel.fit(
            table,
            title="Стан",
            title_align="center",
            padding=(0, 1),
            border_style=border_style,
        )


async def run_console_status_bar(
    *,
    redis_conn: Redis,
    snapshot_key: str,
    refresh_per_second: float = 4.0,
    poll_interval_seconds: float | None = None,
    console: Console | None = None,
) -> None:
    """Фоново оновлює ConsoleStatusBar, читаючи snapshot з Redis."""

    enabled = bool(SMC_CONSOLE_STATUS_BAR_ENABLED)
    if not enabled or not _stderr_is_tty():
        return

    status_bar = ConsoleStatusBar(
        enabled=enabled,
        refresh_per_second=refresh_per_second,
        console=console,
    )
    status_bar.start()

    start_monotonic = time.monotonic()

    poll = (
        0.5 if poll_interval_seconds is None else max(0.2, float(poll_interval_seconds))
    )
    redis_ok = True
    last_ping_monotonic = 0.0

    try:
        while True:
            now = time.monotonic()
            if now - last_ping_monotonic >= 2.0:
                last_ping_monotonic = now
                try:
                    await redis_conn.ping()
                    redis_ok = True
                except Exception:
                    redis_ok = False

            smc_payload: dict[str, Any] | None = None
            if redis_ok:
                try:
                    raw = await redis_conn.get(snapshot_key)
                except Exception:
                    raw = None
                    redis_ok = False
                if isinstance(raw, str) and raw:
                    try:
                        parsed = json.loads(raw)
                    except Exception:
                        parsed = None
                    if isinstance(parsed, dict):
                        smc_payload = parsed

            fxcm_state = None
            try:
                fxcm_state = get_fxcm_feed_state()
            except Exception:
                fxcm_state = None

            snapshot = build_status_snapshot(
                smc_payload=smc_payload,
                fxcm_state=fxcm_state,
                redis_connected=redis_ok,
                sleep_for=poll,
                uptime_seconds=(now - start_monotonic),
                now_utc=datetime.now(tz=UTC),
            )
            status_bar.update(snapshot)
            await asyncio.sleep(poll)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # pragma: no cover - не валимо пайплайн
        logger.debug("[StatusBar] Помилка status bar: %s", exc, exc_info=True)
    finally:
        status_bar.stop()


__all__ = (
    "ConsoleStatusBar",
    "build_status_snapshot",
    "run_console_status_bar",
)
