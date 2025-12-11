"""Розширений Rich-рендерер для SmcViewerState.

Показує детальні списки подій, OTE-зон і пулів ліквідності поверх
базового контракту viewer_state (UI_v2).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from rich.columns import Columns
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from UI_v2.schemas import FxcmMeta, SmcViewerState


class SmcRichViewerExtended:
    """Розширений рендерер `SmcViewerState` у вигляді Rich-панелі.

    Приймає вже побудований `SmcViewerState` і показує:
    - агреговане резюме структури/ліквідності;
    - FXCM-стан (якщо доступний);
    - таблички останніх подій, OTE-зон і пулів ліквідності.
    """

    def __init__(
        self,
        *,
        title: str | None = None,
        max_events: int = 20,
        max_pools: int = 12,
        max_ote_zones: int = 8,
    ) -> None:
        self._title = title or "SMC viewer extended"
        self._max_events = max_events
        self._max_pools = max_pools
        self._max_ote_zones = max_ote_zones

    # -- Публічний інтерфейс -------------------------------------------------

    def render_panel(self, state: SmcViewerState) -> Panel:
        """Рендерить розширений вигляд одного активу."""

        summary_panel = self._build_summary_panel(state)
        fxcm_panel = self._build_fxcm_panel(state)
        events_panel = self._build_events_panel(state)
        ote_panel = self._build_ote_panel(state)
        pools_panel = self._build_pools_panel(state)
        magnets_panel = self._build_magnets_panel(state)

        top_panels: list[Panel] = [summary_panel]
        if fxcm_panel is not None:
            top_panels.append(fxcm_panel)

        top_row = Columns(top_panels, expand=True)

        bottom_row = Columns(
            [events_panel, ote_panel, pools_panel, magnets_panel],
            expand=True,
        )

        layout = Table.grid(expand=True)
        layout.add_row(top_row)
        layout.add_row(bottom_row)

        title_text = Text(
            f"SMC Viewer | Extended | {str(state.get('symbol') or '').upper()}",
            style="bold magenta",
        )
        return Panel(
            layout,
            title=title_text,
            border_style="magenta",
        )

    # -- Блоки верхнього рівня -----------------------------------------------

    def _build_summary_panel(self, state: SmcViewerState) -> Panel:
        structure = state.get("structure") or {}
        liquidity = state.get("liquidity") or {}

        trend = structure.get("trend") or "-"
        bias = structure.get("bias") or "-"
        range_state = structure.get("range_state") or "-"

        legs = structure.get("legs") or []
        swings = structure.get("swings") or []
        events = structure.get("events") or []
        ote_zones = structure.get("ote_zones") or []

        pools = liquidity.get("pools") or []
        magnets = liquidity.get("magnets") or []

        table = Table(expand=True)
        table.add_column("Поле", justify="right", style="bold")
        table.add_column("Значення", justify="left")

        table.add_row("Trend", str(trend))
        table.add_row("Bias", str(bias))
        table.add_row("Range", str(range_state))
        table.add_row("AMD phase", str(liquidity.get("amd_phase") or "-"))
        table.add_row("Session", str(state.get("session") or "-"))
        table.add_row("Price", self._format_price(state.get("price")))
        table.add_row("Pipeline", self._pipeline_label(state))
        table.add_row("Legs", str(len(legs)))
        table.add_row("Swings", str(len(swings)))
        table.add_row("Events", str(len(events)))
        table.add_row("OTE zones", str(len(ote_zones)))
        table.add_row("Pools", str(len(pools)))
        table.add_row("Magnets", str(len(magnets)))

        meta = state.get("meta") or {}
        payload_ts = meta.get("ts") or state.get("payload_ts") or "-"
        payload_seq = meta.get("seq") or state.get("payload_seq") or "-"
        table.add_row("Payload TS", str(payload_ts))
        table.add_row("Seq", str(payload_seq))

        return Panel(table, title="Summary", border_style="cyan")

    def _build_fxcm_panel(self, state: SmcViewerState) -> Panel | None:
        fxcm: FxcmMeta | None = state.get("fxcm")  # type: ignore[assignment]
        if not fxcm:
            return None

        table = Table(expand=True)
        table.add_column("Поле", justify="right", style="bold")
        table.add_column("Значення", justify="left")

        table.add_row("Market", str(fxcm.get("market_state") or "-"))
        table.add_row("Process", str(fxcm.get("process_state") or "-"))
        table.add_row("Price", str(fxcm.get("price_state") or "-"))
        table.add_row("OHLCV", str(fxcm.get("ohlcv_state") or "-"))

        lag = fxcm.get("lag_seconds")
        lag_str = f"{lag:.2f} s" if isinstance(lag, (int, float)) else "-"
        table.add_row("Lag", lag_str)

        session = fxcm.get("session") or {}
        session_tag = session.get("tag") or session.get("name") or "-"
        seconds_to_close = session.get("seconds_to_close")
        stc_str = (
            f"{int(seconds_to_close)} s"
            if isinstance(seconds_to_close, (int, float))
            else "-"
        )
        table.add_row("Session", str(session_tag))
        table.add_row("To close", stc_str)

        next_open = (
            fxcm.get("next_open_utc")
            or session.get("next_open_utc")
            or session.get("next_open_countdown")
        )
        table.add_row("Next open", str(next_open or "-"))

        return Panel(table, title="FXCM", border_style="yellow")

    # -- Події, OTE, пули, магніти -------------------------------------------

    def _build_events_panel(self, state: SmcViewerState) -> Panel:
        structure = state.get("structure") or {}
        events: Iterable[dict[str, Any]] = structure.get("events") or []

        table = Table(expand=True, title="Events")
        table.add_column("t", justify="left", style="dim")
        table.add_column("type", justify="left")
        table.add_column("dir", justify="center")
        table.add_column("price", justify="right")
        table.add_column("status", justify="left")

        rows = list(events)[-self._max_events :]
        if not rows:
            table.add_row("-", "-", "-", "-", "-")
        else:
            for event in rows:
                table.add_row(
                    str(event.get("time") or "-"),
                    str(event.get("type") or event.get("event_type") or "-"),
                    str(event.get("direction") or "-"),
                    self._format_price(event.get("price")),
                    str(event.get("status") or "-"),
                )

        return Panel(table, border_style="blue")

    def _build_ote_panel(self, state: SmcViewerState) -> Panel:
        structure = state.get("structure") or {}
        ote_zones: Iterable[dict[str, Any]] = structure.get("ote_zones") or []
        price_ref = state.get("price")

        table = Table(expand=True, title="OTE")
        table.add_column("dir", justify="center")
        table.add_column("role", justify="left")
        table.add_column("ote_min", justify="right")
        table.add_column("ote_max", justify="right")
        table.add_column("Δ(ote_min)", justify="right")

        rows = list(ote_zones)[-self._max_ote_zones :]
        if not rows:
            table.add_row("-", "-", "-", "-", "-")
        else:
            for zone in rows:
                ote_min = zone.get("ote_min")
                ote_max = zone.get("ote_max")
                delta = self._format_delta(ote_min, price_ref)
                table.add_row(
                    str(zone.get("direction") or "-"),
                    str(zone.get("role") or "-"),
                    self._format_price(ote_min),
                    self._format_price(ote_max),
                    delta,
                )

        return Panel(table, border_style="magenta")

    def _build_pools_panel(self, state: SmcViewerState) -> Panel:
        liquidity = state.get("liquidity") or {}
        pools: Iterable[dict[str, Any]] = liquidity.get("pools") or []

        table = Table(expand=True, title="Pools")
        table.add_column("level", justify="right")
        table.add_column("type", justify="left")
        table.add_column("role", justify="left")
        table.add_column("str", justify="right")

        rows = list(pools)[: self._max_pools]
        if not rows:
            table.add_row("-", "-", "-", "-")
        else:
            for pool in rows:
                table.add_row(
                    self._format_price(pool.get("level")),
                    str(pool.get("liq_type") or "-"),
                    str(pool.get("role") or "-"),
                    self._format_float(pool.get("strength")),
                )

        return Panel(table, border_style="green")

    def _build_magnets_panel(self, state: SmcViewerState) -> Panel:
        liquidity = state.get("liquidity") or {}
        magnets: Iterable[dict[str, Any]] = liquidity.get("magnets") or []

        table = Table(expand=True, title="Magnets")
        table.add_column("kind", justify="left")
        table.add_column("level", justify="right")

        rows = list(magnets)
        if not rows:
            table.add_row("-", "-")
        else:
            for magnet in rows:
                table.add_row(
                    str(magnet.get("kind") or magnet.get("type") or "-"),
                    self._format_price(magnet.get("level")),
                )

        return Panel(table, border_style="red")

    # -- Низькорівневі форматери --------------------------------------------

    @staticmethod
    def _format_price(value: Any) -> str:
        if value is None:
            return "-"
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _format_float(value: Any) -> str:
        if value is None:
            return "-"
        try:
            return f"{float(value):.2f}"
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _format_delta(ote_min: Any, price_ref: Any) -> str:
        """Проста оцінка відстані ціни до нижньої межі OTE."""
        try:
            if ote_min is None or price_ref is None:
                return "-"
            delta = float(price_ref) - float(ote_min)
            sign = "+" if delta >= 0 else "-"
            return f"{sign}{abs(delta):.2f}"
        except (TypeError, ValueError):
            return "-"

    def _pipeline_label(self, state: SmcViewerState) -> str:
        meta = state.get("meta") or {}
        if not isinstance(meta, dict):
            return "-"

        state_label = str(meta.get("pipeline_state") or "-").upper()

        def _as_int(value: Any) -> int | None:
            return int(value) if isinstance(value, (int, float)) else None

        ready = _as_int(meta.get("pipeline_ready_assets"))
        total = _as_int(meta.get("pipeline_assets_total"))
        minimum = _as_int(meta.get("pipeline_min_ready"))

        parts = [state_label]
        if ready is not None and total is not None:
            parts.append(f"{ready}/{total}")
        if minimum is not None:
            parts.append(f"min {minimum}")

        return " ".join(parts).strip()
