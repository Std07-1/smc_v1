"""Базовий Rich-рендерер для SmcViewerState.

Мета:
- приймати вже побудований SmcViewerState (UI_v2.viewer_state_builder);
- не знати нічого про Redis, snapshot-файли чи SMC core;
- повертати один Panel, який можна вставити в будь-який Rich-консольний UI.
"""

from __future__ import annotations

from typing import Any

from rich.align import Align
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from UI_v2.schemas import FxcmMeta, SmcViewerState


class SmcRichViewer:
    """Простий рендерер `SmcViewerState` у вигляді Rich-панелі.

    Клас не містить прихованого стану й не зберігає кешів. Уся агрегація
    повинна виконуватися у builder-шарі (viewer_state_builder).
    """

    def __init__(self, *, title: str | None = None) -> None:
        self._title = title or "SMC viewer"

    def render_panel(self, state: SmcViewerState) -> Panel:
        """Рендерить повний стан одного активу в Rich Panel."""

        header = self._build_header(state)
        structure = self._build_structure_block(state)
        liquidity = self._build_liquidity_block(state)
        zones = self._build_zones_block(state)

        grid = Table.grid(padding=(0, 1))
        grid.expand = True

        grid.add_row(header)
        grid.add_row(structure, liquidity)
        grid.add_row(zones)

        return Panel.fit(grid, title=self._title, border_style="cyan")

    def _build_header(self, state: SmcViewerState) -> Panel:
        """Заголовок із символом, ціною, сесією та FXCM-станом."""

        symbol = state.get("symbol") or "?"
        price = state.get("price")
        session = state.get("session") or "-"

        price_str = self._format_price(price)

        meta = state.get("meta") or {}
        payload_ts = meta.get("ts") or state.get("payload_ts") or "-"
        payload_seq = meta.get("seq") or state.get("payload_seq") or "-"

        fxcm: FxcmMeta | None = state.get("fxcm")  # type: ignore[assignment]

        table = Table.grid(padding=(0, 1))
        table.add_column(justify="left", ratio=2)
        table.add_column(justify="right", ratio=3)

        left = Text.assemble(
            ("[", "dim"),
            (str(symbol), "bold yellow"),
            ("] ", "dim"),
            (price_str, "bold white"),
        )

        right = Text()
        right.append(f"session: {session}", style="bold magenta")
        right.append("  |  ", style="dim")
        right.append(f"ts: {payload_ts}", style="dim")
        right.append("  |  ", style="dim")
        right.append(f"seq: {payload_seq}", style="dim")

        if fxcm is not None:
            fxcm_summary = self._summarize_fxcm(fxcm)
            if fxcm_summary:
                right.append("\n")
                right.append(fxcm_summary, style="cyan")

        table.add_row(left, right)

        return Panel(Align.left(table), border_style="bright_black")

    def _build_structure_block(self, state: SmcViewerState) -> Panel:
        """Блок структури: тренд, bias, діапазон та агрегації."""

        structure = state.get("structure") or {}
        trend = structure.get("trend") or "-"
        bias = structure.get("bias") or "-"
        range_state = structure.get("range_state") or "-"

        legs = structure.get("legs") or []
        swings = structure.get("swings") or []
        events = structure.get("events") or []
        ote_zones = structure.get("ote_zones") or []

        table = Table.grid(padding=(0, 1))
        table.add_column(justify="left")

        header = Text.assemble(
            ("STRUCT  ", "bold blue"),
            (f"{trend}", "bold"),
            (" / ", "dim"),
            (f"{bias}", "bold"),
            (" / ", "dim"),
            (f"{range_state}", "bold"),
        )
        table.add_row(header)

        summary = Text()
        summary.append(f"legs: {len(legs)}", style="white")
        summary.append("  |  ", style="dim")
        summary.append(f"swings: {len(swings)}", style="white")
        summary.append("  |  ", style="dim")
        summary.append(f"events: {len(events)}", style="white")
        summary.append("  |  ", style="dim")
        summary.append(f"OTE: {len(ote_zones)}", style="white")

        table.add_row(summary)

        return Panel(table, title="Structure", border_style="blue")

    def _build_liquidity_block(self, state: SmcViewerState) -> Panel:
        """Блок ліквідності: AMD-фаза, кількість пулів та магнітів."""

        liquidity = state.get("liquidity") or {}
        amd_phase = liquidity.get("amd_phase") or "-"
        pools = liquidity.get("pools") or []
        magnets = liquidity.get("magnets") or []

        table = Table.grid(padding=(0, 1))
        table.add_column(justify="left")

        header = Text.assemble(
            ("LIQ  ", "bold green"),
            ("AMD: ", "dim"),
            (amd_phase, "bold"),
        )
        table.add_row(header)

        summary = Text()
        summary.append(f"pools: {len(pools)}", style="white")
        summary.append("  |  ", style="dim")
        summary.append(f"magnets: {len(magnets)}", style="white")

        table.add_row(summary)

        return Panel(table, title="Liquidity", border_style="green")

    def _build_zones_block(self, state: SmcViewerState) -> Panel:
        """Блок зон: сирі зони з viewer_state.zones.raw."""

        zones = state.get("zones") or {}
        raw = zones.get("raw") or {}
        raw_zones = raw.get("zones") or []

        table = Table.grid(padding=(0, 1))
        table.add_column(justify="left")

        header = Text("ZONES", style="bold magenta")
        table.add_row(header)

        count = len(raw_zones)
        if count == 0:
            table.add_row(Text("немає активних зон", style="dim"))
        else:
            table.add_row(Text(f"zones: {count}", style="white"))

        return Panel(table, title="Zones", border_style="magenta")

    @staticmethod
    def _format_price(price: Any) -> str:
        """Легка нормалізація ціни для хедеру."""

        if price is None:
            return "-"
        try:
            return f"{float(price):.2f}"
        except (TypeError, ValueError):
            return str(price)

    @staticmethod
    def _summarize_fxcm(fxcm: FxcmMeta) -> str:
        """Компактний опис FXCM-стану для хедеру."""

        market = fxcm.get("market_state") or "-"
        process = fxcm.get("process_state") or "-"
        lag = fxcm.get("lag_seconds")
        session = fxcm.get("session") or {}
        session_tag = session.get("tag") or session.get("name") or "-"

        lag_part = f"{lag:.2f}s" if isinstance(lag, (int, float)) else "-"
        return f"FXCM: {market}/{process} | lag={lag_part} | sess={session_tag}"
