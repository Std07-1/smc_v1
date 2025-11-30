"""Розширений рендерер для Experimental SMC Viewer."""

from __future__ import annotations

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

        top_row = Columns([summary, session_block], expand=True)
        fxcm_panel = self._build_fxcm_panel(viewer_state)
        top_panels = [summary, session_block]
        if fxcm_panel is not None:
            top_panels.append(fxcm_panel)
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
        for label, value in self._compose_fxcm_rows(viewer_state.get("fxcm")):
            table.add_row(label, value)
        return Panel(table, border_style="green")

    def _compose_fxcm_rows(
        self, fxcm_block: dict[str, Any] | None
    ) -> list[tuple[str, str]]:
        if not isinstance(fxcm_block, dict):
            return [("Статус", "Немає даних"), ("Лаг", "-")]

        market_state = str(fxcm_block.get("market_state") or "unknown").lower()
        process_state = str(fxcm_block.get("process_state") or "unknown").upper()
        icon = {"open": "🟢", "closed": "🔴"}.get(market_state, "⚪")
        market_label = f"{icon} {market_state.upper()}"

        lag_value = fxcm_block.get("lag_seconds")
        if isinstance(lag_value, (int, float)):
            lag_color = (
                "green" if lag_value < 5 else ("yellow" if lag_value < 20 else "red")
            )
            lag_label = f"[{lag_color}]{lag_value:.1f}s[/]"
        else:
            lag_label = "-"

        last_close = fxcm_block.get("last_bar_close_utc") or fxcm_block.get(
            "last_bar_close_ms"
        )
        last_close_label = "-"
        if isinstance(last_close, str):
            last_close_label = self._format_ts(last_close)
        elif isinstance(last_close, (int, float)):
            iso_ts = self._format_utc_from_ms(last_close)
            if iso_ts:
                last_close_label = self._format_ts(iso_ts)
            else:
                last_close_label = str(last_close)

        next_open_raw = fxcm_block.get("next_open_utc")
        next_open_label = self._format_ts(next_open_raw) if next_open_raw else "-"

        return [
            ("Market", market_label),
            ("Process", process_state),
            ("Лаг", lag_label),
            ("Останній close", last_close_label),
            ("Наступне відкриття", next_open_label),
        ]
