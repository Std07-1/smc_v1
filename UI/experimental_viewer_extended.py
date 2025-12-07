"""Розширений рендерер для Experimental SMC Viewer."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from rich.align import Align
from rich.columns import Columns
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from UI.experimental_viewer import SmcExperimentalViewer


class SmcExperimentalViewerExtended(SmcExperimentalViewer):
    """Додає сесійні блоки та таймлайн подій поверх базового viewer-state."""

    def render_panel(self, viewer_state: dict[str, Any]) -> Panel:
        summary = self._build_summary_table(viewer_state)
        session_block = self._build_session_block(viewer_state)
        timeline = self._build_timeline_panel(viewer_state)
        events = self._build_events_table(viewer_state["structure"]["events"])
        ote = self._build_ote_table(viewer_state["structure"]["ote_zones"])
        pools = self._build_pools_table(viewer_state["liquidity"]["pools"])

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
        bottom_row = Columns([events, ote, pools], expand=True)

        layout = Table.grid(expand=True)
        layout.add_row(top_row)
        layout.add_row(timeline)
        layout.add_row(bottom_row)

        title = Text(
            f"SMC Viewer · Extended · {viewer_state.get('symbol', '').upper()}",
            style="bold magenta",
        )
        return Panel(
            layout,
            title=title,
            subtitle="Session+Event timeline",
            border_style="magenta",
        )

    # ── Додаткові блоки -----------------------------------------------------
    def _build_session_block(self, viewer_state: dict[str, Any]) -> Panel:
        table = Table(title="Сесія / ціна", expand=True)
        table.add_column("Параметр", justify="right", style="bold cyan")
        table.add_column("Значення", justify="left")
        table.add_row("Символ", str(viewer_state.get("symbol", "-")).upper())
        table.add_row("Session", str(viewer_state.get("session") or "-").upper())
        table.add_row("Ціна", self._format_price(viewer_state.get("price")))
        table.add_row("Payload", self._format_ts(viewer_state.get("payload_ts")))
        table.add_row("Schema", str(viewer_state.get("schema") or "-"))
        return Panel(table, border_style="cyan", title="Session Block")

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
