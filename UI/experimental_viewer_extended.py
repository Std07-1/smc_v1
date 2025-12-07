"""Розширений рендерер для Experimental SMC Viewer."""

from __future__ import annotations

import json
import math
from collections import deque
from datetime import UTC, datetime
from typing import Any

from rich.align import Align
from rich.columns import Columns
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from config.config import UI_VIEWER_DEFAULT_MODE, UI_VIEWER_SHOW_RAW
from UI.experimental_viewer import SmcExperimentalViewer


class SmcExperimentalViewerExtended(SmcExperimentalViewer):
    """Додає сесійні блоки та таймлайн подій поверх базового viewer-state."""

    WEEKEND_GUESS_THRESHOLD_SECONDS = 30 * 3600

    def __init__(
        self,
        symbol: str,
        snapshot_dir: str = "tmp",
        *,
        show_raw: bool | None = None,
        view_mode: int | None = None,
    ) -> None:
        default_raw_flag = bool(UI_VIEWER_SHOW_RAW)
        self._show_raw_panel = show_raw if show_raw is not None else default_raw_flag
        initial_mode = view_mode if view_mode is not None else UI_VIEWER_DEFAULT_MODE
        self._view_mode = self._normalize_mode(initial_mode)
        self._history_buffer: deque[dict[str, Any]] = deque(maxlen=36)
        super().__init__(symbol, snapshot_dir)

    def set_view_mode(self, mode: int) -> None:
        """Динамічно перемикає макет viewer (режими 1/2)."""

        self._view_mode = self._normalize_mode(mode)

    @staticmethod
    def _normalize_mode(value: int | None) -> int:
        return 2 if value == 2 else 1

    def render_panel(self, viewer_state: dict[str, Any]) -> Panel:
        self._update_history_buffer(viewer_state)
        if self._view_mode == 2:
            return self._render_history_mode(viewer_state)
        return self._render_primary_mode(viewer_state)

    def _render_primary_mode(self, viewer_state: dict[str, Any]) -> Panel:
        summary = self._build_summary_table(viewer_state)
        session_block = self._build_session_block(viewer_state)
        timeline = self._build_timeline_panel(viewer_state)
        swings_panel = self._build_swings_panel(viewer_state)
        zones_panel = self._build_zone_inspector(viewer_state)
        events = self._build_events_with_delta(viewer_state)
        ote = self._build_ote_with_delta(viewer_state)
        pools = self._build_liquidity_heatmap(viewer_state)

        fxcm_panel = self._build_fxcm_panel(viewer_state)
        top_panels = [summary, session_block]
        if fxcm_panel is not None:
            top_panels.append(fxcm_panel)
        meta_block = viewer_state.get("meta")
        cold_meta = None
        if isinstance(meta_block, dict):
            cold_meta = meta_block.get("cold_start") or meta_block.get(
                "cold_start_status"
            )
        cold_panel = self._build_cold_start_panel(cold_meta)
        if cold_panel is not None:
            top_panels.append(cold_panel)
        top_row = Columns(top_panels, expand=True)

        left_stack = Table.grid(expand=True)
        left_stack.add_row(swings_panel)
        left_stack.add_row(events)
        left_stack.add_row(ote)

        right_stack = Table.grid(expand=True)
        right_stack.add_row(pools)
        right_stack.add_row(zones_panel)

        body_row = Columns([left_stack, right_stack], expand=True)

        layout = Table.grid(expand=True)
        layout.add_row(top_row)
        layout.add_row(timeline)
        layout.add_row(body_row)

        title = Text(
            f"SMC Viewer · Extended · {viewer_state.get('symbol', '').upper()}",
            style="bold magenta",
        )
        return Panel(
            layout,
            title=title,
            subtitle="Режим 1 · Session view",
            border_style="magenta",
        )

    def _render_history_mode(self, viewer_state: dict[str, Any]) -> Panel:
        history_panel = self._build_history_panel()
        raw_panel = self._build_raw_panel(viewer_state)

        layout = Table.grid(expand=True)
        if raw_panel is not None:
            layout.add_row(Columns([history_panel, raw_panel], expand=True))
        else:
            layout.add_row(history_panel)

        title = Text(
            f"SMC Viewer · Extended · {viewer_state.get('symbol', '').upper()}",
            style="bold green",
        )
        return Panel(
            layout,
            title=title,
            subtitle="Режим 2 · Історія / QA",
            border_style="green",
        )

    def _update_history_buffer(self, viewer_state: dict[str, Any]) -> None:
        raw_ts = viewer_state.get("payload_ts")
        meta_block = viewer_state.get("meta")
        if not raw_ts and isinstance(meta_block, dict):
            raw_ts = meta_block.get("payload_ts") or meta_block.get("ts")
        ts_label = self._format_ts(raw_ts) if raw_ts else "-"
        structure_label, structure_warn = self._structure_health_badge(
            viewer_state.get("structure")
        )
        liquidity_label, liquidity_warn = self._liquidity_health_badge(
            viewer_state.get("liquidity")
        )
        if self._history_buffer and self._history_buffer[-1].get("raw_ts") == raw_ts:
            self._history_buffer[-1].update(
                {
                    "ts": ts_label,
                    "structure": structure_label,
                    "structure_warn": structure_warn,
                    "liquidity": liquidity_label,
                    "liquidity_warn": liquidity_warn,
                }
            )
            return
        self._history_buffer.append(
            {
                "raw_ts": raw_ts,
                "ts": ts_label,
                "structure": structure_label,
                "structure_warn": structure_warn,
                "liquidity": liquidity_label,
                "liquidity_warn": liquidity_warn,
            }
        )

    # ── Додаткові блоки -----------------------------------------------------
    def _build_summary_table(self, viewer_state: dict[str, Any]) -> Table:
        table = Table(title="Структура", expand=True)
        table.add_column("Поле", justify="right", style="bold")
        table.add_column("Значення", justify="left")
        structure = viewer_state.get("structure") or {}
        liquidity = viewer_state.get("liquidity") or {}
        table.add_row("Trend", str(structure.get("trend")))
        table.add_row("Bias", str(structure.get("bias")))
        table.add_row("Range", str(structure.get("range_state")))
        table.add_row("AMD", str(liquidity.get("amd_phase")))
        table.add_row("Session", str(viewer_state.get("session") or "-"))
        table.add_row("Price", self._format_price(viewer_state.get("price")))
        table.add_row("Payload TS", self._format_ts(viewer_state.get("payload_ts")))
        schema_value = viewer_state.get("schema")
        if schema_value:
            table.add_row("Schema", str(schema_value))
        return table

    def _build_session_block(self, viewer_state: dict[str, Any]) -> Panel:
        table = Table(title="Сесія / ціна", expand=True)
        table.add_column("Параметр", justify="right", style="bold cyan")
        table.add_column("Значення", justify="left")
        table.add_row("Символ", str(viewer_state.get("symbol", "-")).upper())
        table.add_row("Session", str(viewer_state.get("session") or "-").upper())
        table.add_row("Ціна", self._format_price(viewer_state.get("price")))
        table.add_row("Payload", self._format_ts(viewer_state.get("payload_ts")))
        table.add_row("Schema", str(viewer_state.get("schema") or "-"))
        next_hint = self._session_next_open_hint(viewer_state)
        if next_hint:
            table.add_row("Наступне відкриття", next_hint)
        return Panel(table, border_style="cyan", title="Session Block")

    def _session_next_open_hint(self, viewer_state: dict[str, Any]) -> str | None:
        fxcm_state = viewer_state.get("fxcm")
        if not isinstance(fxcm_state, dict):
            return None
        market_state = str(fxcm_state.get("market_state") or "?").upper()
        next_open = fxcm_state.get("countdown") or fxcm_state.get("next_open_utc")
        if not next_open:
            session_block = fxcm_state.get("session")
            if isinstance(session_block, dict):
                next_open = session_block.get(
                    "next_open_countdown"
                ) or session_block.get("next_open_utc")
        next_label = str(next_open or "-")
        if self._is_probably_weekend(viewer_state):
            status_hint = "вихідні"
        elif market_state == "CLOSED":
            status_hint = "пауза"
        else:
            status_hint = "робочий режим"
        if next_label == "-" and status_hint == "робочий режим":
            return None
        return f"{next_label} · {status_hint}"

    def _build_timeline_panel(self, viewer_state: dict[str, Any]) -> Panel:
        events = viewer_state.get("structure", {}).get("events", []) or []
        rows = []
        for event in events[-15:]:
            rows.append(self._format_timeline_item(event))
        if not rows:
            body = Align.center(
                Text("Події відсутні", style="yellow"), vertical="middle"
            )
        else:
            timeline_table = Table(expand=True)
            timeline_table.add_column("Час", style="bold")
            timeline_table.add_column("Подія")
            timeline_table.add_column("Ціна", justify="right")
            for row in rows:
                timeline_table.add_row(row["time"], row["label"], row["price"])
            body = timeline_table
        return Panel(body, title="Таймлайн подій", border_style="blue")

    def _build_swings_panel(self, viewer_state: dict[str, Any]) -> Panel:
        structure = viewer_state.get("structure") or {}
        swings = structure.get("swings") or []
        swing_lines: list[str] = []
        for swing in swings[-6:]:
            arrow = "↑" if str(swing.get("kind")).upper() == "HIGH" else "↓"
            ts_label = self._format_ts(swing.get("time"))
            swing_lines.append(
                f"{arrow} {self._format_price(swing.get('price'))} @ {ts_label}"
            )
        table = Table(title="Свінги", expand=True)
        table.add_column("Свінги", style="cyan")
        table.add_row("\n".join(swing_lines) or "-")
        return Panel(table, border_style="cyan")

    def _build_events_with_delta(self, viewer_state: dict[str, Any]) -> Panel:
        events = viewer_state.get("structure", {}).get("events") or []
        price_ref = self._safe_float(viewer_state.get("price"))
        table = Table(title="BOS / CHOCH", expand=True)
        table.add_column("Тип", style="cyan")
        table.add_column("Dir")
        table.add_column("Ціна", justify="right")
        table.add_column("Δ", justify="right")
        if not events:
            table.add_row("-", "-", "-", "-")
            return Panel(table, border_style="blue")
        for event in events[-15:]:
            price_val = self._format_price(event.get("price"))
            delta = self._format_delta(event.get("price"), price_ref, prefix=False)
            table.add_row(
                str(event.get("type")),
                str(event.get("direction")),
                price_val,
                delta,
            )
        return Panel(table, border_style="blue")

    def _build_ote_with_delta(self, viewer_state: dict[str, Any]) -> Panel:
        zones = viewer_state.get("structure", {}).get("ote_zones") or []
        price_ref = self._safe_float(viewer_state.get("price"))
        table = Table(title="OTE зони", expand=True)
        table.add_column("Dir")
        table.add_column("Role")
        table.add_column("Min")
        table.add_column("Max")
        table.add_column("Δ", justify="right")
        if not zones:
            table.add_row("-", "-", "-", "-", "-")
            return Panel(table, border_style="magenta")
        for zone in zones[-6:]:
            ote_min = self._safe_float(zone.get("ote_min"))
            ote_max = self._safe_float(zone.get("ote_max"))
            highlight = (
                price_ref is not None
                and ote_min is not None
                and ote_max is not None
                and ote_min <= price_ref <= ote_max
            )
            delta = self._format_delta(ote_min, price_ref, prefix=False)
            table.add_row(
                str(zone.get("direction")),
                str(zone.get("role")),
                self._format_price(ote_min),
                self._format_price(ote_max),
                delta,
                style="bold magenta" if highlight else None,
            )
        return Panel(table, border_style="magenta")

    def _build_liquidity_heatmap(self, viewer_state: dict[str, Any]) -> Panel:
        pools = viewer_state.get("liquidity", {}).get("pools") or []
        price_ref = self._safe_float(viewer_state.get("price"))
        table = Table(title="Ліквідність (heatmap)", expand=True)
        table.add_column("Рівень")
        table.add_column("Δ", justify="right")
        table.add_column("Role")
        table.add_column("Сила")
        if not pools:
            table.add_row("-", "-", "-", "-")
            return Panel(table, border_style="green")
        for pool in pools[:8]:
            level = pool.get("level")
            strength_value = self._safe_float(pool.get("strength"))
            strength_label = (
                f"{strength_value:.2f}" if strength_value is not None else "-"
            )
            table.add_row(
                self._format_price(level),
                self._format_delta(level, price_ref, prefix=False),
                str(pool.get("role")),
                strength_label,
            )
        return Panel(table, border_style="green")

    def _build_zone_inspector(self, viewer_state: dict[str, Any]) -> Panel:
        zones_block = viewer_state.get("zones", {})
        raw_block = zones_block.get("raw") if isinstance(zones_block, dict) else None
        zones = []
        if isinstance(raw_block, dict):
            payload = raw_block.get("zones")
            if isinstance(payload, list):
                zones = payload
        price_ref = self._safe_float(viewer_state.get("price"))
        table = Table(title="Zones / POI", expand=True)
        table.add_column("Тип")
        table.add_column("Role")
        table.add_column("Entry")
        table.add_column("Δ")
        table.add_column("Quality")
        if not zones:
            table.add_row("-", "-", "-", "-", "-")
            return Panel(table, border_style="magenta")
        ranked = sorted(
            zones,
            key=lambda item: self._safe_float(item.get("strength")) or 0.0,
            reverse=True,
        )
        for zone in ranked[:3]:
            entry = zone.get("entry_hint") or zone.get("price_min")
            table.add_row(
                str(zone.get("zone_type")),
                str(zone.get("role")),
                self._format_price(entry),
                self._format_delta(entry, price_ref, prefix=False),
                str(zone.get("quality") or zone.get("bias_at_creation") or "-"),
            )
        return Panel(table, border_style="magenta")

    def _build_history_panel(self) -> Panel:
        table = Table(title="Останні payload-и", expand=True)
        table.add_column("TS", style="bold")
        table.add_column("Структура")
        table.add_column("Ліквідність")
        if not self._history_buffer:
            table.add_row("-", "даних поки немає", "-")
        else:
            for entry in reversed(self._history_buffer):
                struct_style = "red" if entry.get("structure_warn") else "green"
                liq_style = "red" if entry.get("liquidity_warn") else "green"
                table.add_row(
                    entry.get("ts", "-"),
                    Text(entry.get("structure", "-"), style=struct_style),
                    Text(entry.get("liquidity", "-"), style=liq_style),
                )

        latest = self._history_buffer[-1] if self._history_buffer else None
        degraded = bool(
            latest and (latest.get("structure_warn") or latest.get("liquidity_warn"))
        )
        status_text = (
            Text("SMC degraded — перевірте дані", style="bold red")
            if degraded
            else Text("SMC метрики у нормі", style="bold green")
        )
        grid = Table.grid(expand=True)
        grid.add_row(status_text)
        grid.add_row(table)
        border_style = "red" if degraded else "green"
        return Panel(grid, border_style=border_style, title="Історія / QA")

    def _build_raw_panel(self, viewer_state: dict[str, Any]) -> Panel | None:
        if not self._show_raw_panel:
            return None
        snapshot = {
            "structure": viewer_state.get("structure"),
            "liquidity": viewer_state.get("liquidity"),
            "zones": viewer_state.get("zones"),
        }
        try:
            dumped = json.dumps(snapshot, ensure_ascii=False, indent=2, default=str)
        except Exception:
            dumped = str(snapshot)
        preview_lines = dumped.splitlines()[:40]
        body = Text("\n".join(preview_lines))
        return Panel(body, border_style="white", title="RAW JSON (QA)")

    def _structure_health_badge(self, structure: Any) -> tuple[str, bool]:
        if not isinstance(structure, dict):
            return ("немає структури", True)
        fields = ("trend", "bias", "range_state")
        missing = [
            name
            for name in fields
            if self._is_placeholder_value(structure.get(name))
            or self._is_nan(structure.get(name))
        ]
        if missing:
            return (f"прогалини: {', '.join(missing)}", True)
        return (str(structure.get("trend") or "OK"), False)

    def _liquidity_health_badge(self, liquidity: Any) -> tuple[str, bool]:
        if not isinstance(liquidity, dict):
            return ("немає ліквідності", True)
        pools = liquidity.get("pools")
        if not isinstance(pools, list) or not pools:
            return ("pool=0", True)
        strength_values = [self._safe_float(pool.get("strength")) for pool in pools]
        if any(value is None or self._is_nan(value) for value in strength_values):
            return ("NaN strength", True)
        return (f"{len(pools)} pools", False)

    @staticmethod
    def _is_nan(value: Any) -> bool:
        try:
            return isinstance(value, float) and math.isnan(value)
        except Exception:
            return False

    def _format_delta(
        self, target: Any, price_ref: float | None, *, prefix: bool = False
    ) -> str:
        value = self._safe_float(target)
        if value is None or price_ref is None:
            return "-"
        delta = value - price_ref
        formatted = f"{delta:+.2f}"
        return f"Δ={formatted}" if prefix else formatted

    def _format_timeline_item(self, event: dict[str, Any]) -> dict[str, str]:
        label = f"{event.get('type','?')} → {event.get('direction','?')}"
        price = self._format_price(event.get("price"))
        time_value = str(event.get("time") or "-")
        return {"label": label, "price": price, "time": time_value}

    def _build_fxcm_panel(self, viewer_state: dict[str, Any]) -> Panel | None:
        table = Table(title="FXCM телеметрія", expand=True)
        table.add_column("Поле", justify="right", style="bold green")
        table.add_column("Значення", justify="left")
        for label, value in self._compose_fxcm_rows(viewer_state):
            table.add_row(label, value)
        return Panel(table, border_style="green")

    def _compose_fxcm_rows(
        self, viewer_state: dict[str, Any] | None
    ) -> list[tuple[str, str]]:
        if not isinstance(viewer_state, dict):
            return [("Статус", "Немає даних"), ("Лаг", "-")]

        fxcm_block: dict[str, Any] | None = None
        meta_block = viewer_state.get("meta")
        if isinstance(meta_block, dict):
            fxcm_meta = meta_block.get("fxcm")
            if isinstance(fxcm_meta, dict):
                fxcm_block = fxcm_meta
        if fxcm_block is None:
            fxcm_state = viewer_state.get("fxcm")
            if isinstance(fxcm_state, dict):
                fxcm_block = fxcm_state
        if fxcm_block is None:
            return [("Статус", "Немає даних"), ("Лаг", "-")]

        market_raw = fxcm_block.get("market") or fxcm_block.get("market_state")
        market_state = str(market_raw or "unknown").lower()
        process_raw = fxcm_block.get("process") or fxcm_block.get("process_state")
        process_state = str(process_raw or "unknown").upper()
        price_state = str(fxcm_block.get("price_state") or "-").upper()
        ohlcv_state = str(fxcm_block.get("ohlcv_state") or "-").upper()
        icon = {"open": "🟢", "closed": "🔴"}.get(market_state, "⚪")
        market_label = f"{icon} {market_state.upper()}"

        lag_label = self._format_fxcm_lag(
            fxcm_block.get("lag_seconds"),
            human_hint=fxcm_block.get("lag_human"),
        )

        last_close = (
            fxcm_block.get("last_close_utc")
            or fxcm_block.get("last_bar_close_utc")
            or fxcm_block.get("last_bar_close_ms")
        )
        last_close_label = "-"
        if isinstance(last_close, str):
            last_close_label = self._format_ts(last_close)
        elif isinstance(last_close, (int, float)):
            iso_ts = self._format_utc_from_ms(last_close)
            last_close_label = self._format_ts(iso_ts) if iso_ts else str(last_close)

        session_raw = fxcm_block.get("session")
        session_block = session_raw if isinstance(session_raw, dict) else None
        session_next = None
        if session_block:
            session_next = session_block.get("next_open_utc") or session_block.get(
                "next_open_ms"
            )
        next_open_raw = fxcm_block.get("next_open_utc")
        next_open_value = None
        if not self._is_placeholder_value(next_open_raw):
            next_open_value = next_open_raw
        elif not self._is_placeholder_value(session_next):
            next_open_value = session_next
        if isinstance(next_open_value, (int, float)):
            iso_next = self._format_utc_from_ms(next_open_value)
            next_open_label = (
                self._format_ts(iso_next) if iso_next else str(next_open_value)
            )
        elif next_open_value:
            next_open_label = self._format_ts(next_open_value)
        else:
            next_open_label = "-"

        if market_state == "open":
            next_open_label = "-"

        close_seconds = self._safe_float(fxcm_block.get("seconds_to_close"))
        if close_seconds is None:
            session_block = fxcm_block.get("session")
            if isinstance(session_block, dict):
                close_seconds = self._safe_float(session_block.get("seconds_to_close"))
        close_countdown = fxcm_block.get("countdown_to_close")
        if not close_countdown:
            close_countdown = self._format_countdown(close_seconds)
        if market_state != "open":
            close_countdown = "-"

        rows = [
            ("Market", market_label),
            ("Process", process_state),
            ("Price", price_state),
            ("OHLCV", ohlcv_state),
            ("Лаг", lag_label),
            ("Останній close", last_close_label),
            ("Наступне відкриття", next_open_label),
            ("До закриття", close_countdown or "-"),
        ]

        status_note = fxcm_block.get("status_note")
        if status_note:
            rows.append(("Note", str(status_note)))

        return rows

    def _build_cold_start_panel(self, cold_meta: Any) -> Panel | None:
        """Повертає панель cold-start статусу, якщо він ще не READY."""

        if not isinstance(cold_meta, dict):
            return None
        state = str(cold_meta.get("state") or cold_meta.get("phase") or "").lower()
        status = str(cold_meta.get("status") or "").lower()
        if not state:
            return None
        if state == "ready" and status in {"", "success", "ok"}:
            return None

        def _is_error(value: str) -> bool:
            return value in {"error", "failed", "timeout"}

        if _is_error(state) or _is_error(status):
            border_style = "red"
        else:
            border_style = "yellow"

        ready = int(cold_meta.get("symbols_ready") or 0)
        total = int(cold_meta.get("symbols_total") or 0)
        ratio = f"{ready}/{total}" if total else str(ready)
        lines = [
            f"Cold-start: {state.upper()} ({status or 'pending'})",
            f"Символів готово: {ratio}",
        ]

        pending_raw = cold_meta.get("symbols_pending")
        if isinstance(pending_raw, (list, tuple)):
            normalized = [str(sym).upper() for sym in pending_raw if sym]
            if normalized:
                limit = 5
                visible = normalized[:limit]
                remainder = len(normalized) - len(visible)
                preview = ", ".join(visible)
                if remainder > 0:
                    preview += f" +{remainder}"
                lines.append(f"Очікують: {preview}")

        required_bars = cold_meta.get("required_bars")
        if isinstance(required_bars, (int, float)):
            lines.append(f"Мінімум барів: {int(required_bars)}")

        report_ts = cold_meta.get("report_ts")
        if isinstance(report_ts, (int, float)):
            iso_value = datetime.fromtimestamp(float(report_ts), tz=UTC).isoformat()
            lines.append(f"Оновлено: {self._format_ts(iso_value)}")

        body = Text("\n".join(lines))
        return Panel(body, border_style=border_style, title="Cold-start стан")

    def _is_probably_weekend(self, viewer_state: dict[str, Any]) -> bool:
        fxcm_state = viewer_state.get("fxcm")
        if not isinstance(fxcm_state, dict):
            return False
        session_block = fxcm_state.get("session")
        if isinstance(session_block, dict):
            session_state = str(session_block.get("state") or "").upper()
            if "WEEKEND" in session_state:
                return True
        payload_dt = self._viewer_payload_datetime(viewer_state)
        if payload_dt and payload_dt.weekday() >= 5:
            return True
        seconds_to_open = self._safe_float(fxcm_state.get("seconds_to_open"))
        if seconds_to_open is not None:
            return seconds_to_open >= self.WEEKEND_GUESS_THRESHOLD_SECONDS
        if isinstance(session_block, dict):
            next_seconds = self._safe_float(
                session_block.get("next_open_seconds")
                or session_block.get("seconds_to_next_open")
            )
            if next_seconds is not None:
                return next_seconds >= self.WEEKEND_GUESS_THRESHOLD_SECONDS
        return False

    def _viewer_payload_datetime(self, viewer_state: dict[str, Any]) -> datetime | None:
        ts_value: Any = viewer_state.get("payload_ts")
        if not ts_value:
            meta_block = viewer_state.get("meta")
            if isinstance(meta_block, dict):
                ts_value = meta_block.get("ts") or meta_block.get("payload_ts")
        if not ts_value:
            return None
        try:
            normalized = str(ts_value).replace("Z", "+00:00")
            return datetime.fromisoformat(normalized)
        except Exception:
            return None
