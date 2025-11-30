"""Експериментальний переглядач SMC-блоку (plain JSON → UI state)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.align import Align
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


class SmcExperimentalViewer:
    """Створює легковаговий стан для майбутнього SMC viewer та рендерить його."""

    MAX_EVENTS: int = 20

    def __init__(self, symbol: str, snapshot_dir: str = "tmp") -> None:
        self.symbol = symbol.lower()
        snapshot_root = Path(snapshot_dir)
        snapshot_root.mkdir(parents=True, exist_ok=True)
        self.snapshot_path = snapshot_root / f"smc_viewer_{self.symbol}.json"

    # ── Публічні методи -----------------------------------------------------
    def build_state(
        self,
        asset: dict[str, Any],
        payload_meta: dict[str, Any],
        fxcm_block: Any | None = None,
    ) -> dict[str, Any]:
        """Повертає агрегований стан для рендера/експорту."""

        smc_block = self._get_smc_block(asset)
        structure = self._as_dict(smc_block.get("structure"))
        liquidity = self._as_dict(smc_block.get("liquidity"))
        zones = self._as_dict(smc_block.get("zones"))

        stats = self._as_dict(asset.get("stats"))
        viewer_state = {
            "symbol": asset.get("symbol"),
            "payload_ts": payload_meta.get("ts"),
            "payload_seq": payload_meta.get("seq"),
            "schema": payload_meta.get("schema") or payload_meta.get("version"),
            "price": stats.get("current_price"),
            "session": stats.get("session_tag"),
            "structure": {
                "trend": structure.get("trend"),
                "bias": structure.get("bias"),
                "range_state": structure.get("range_state"),
                "legs": self._simplify_legs(structure.get("legs")),
                "swings": self._simplify_swings(structure.get("swings")),
                "ranges": self._simplify_ranges(structure.get("ranges")),
                "events": self._simplify_events(structure.get("events")),
                "ote_zones": self._simplify_otes(structure.get("ote_zones")),
            },
            "liquidity": {
                "amd_phase": liquidity.get("amd_phase"),
                "pools": self._simplify_pools(liquidity.get("pools")),
                "magnets": self._simplify_magnets(liquidity.get("magnets")),
            },
            "zones": {
                "raw": zones,
            },
            "fxcm": self._normalize_fxcm_block(fxcm_block),
        }
        return viewer_state

    def dump_snapshot(self, viewer_state: dict[str, Any]) -> None:
        """Зберігає останній стан у JSON для офлайн-аналізу/QA."""

        try:
            self.snapshot_path.write_text(
                json.dumps(viewer_state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            # Збереження снапшоту — best effort
            pass

    def render_panel(self, viewer_state: dict[str, Any]) -> Panel:
        """Повертає Rich-панель з ключовими даними (текстовий прототип)."""

        summary = self._build_summary_table(viewer_state)
        events = self._build_events_table(viewer_state["structure"]["events"])
        ote = self._build_ote_table(viewer_state["structure"]["ote_zones"])
        pools = self._build_pools_table(viewer_state["liquidity"]["pools"])

        grid = Table.grid(expand=True)
        grid.add_row(summary)
        grid.add_row(events)
        grid.add_row(ote)
        grid.add_row(pools)
        title = Text(
            f"SMC Experimental Viewer · {viewer_state.get('symbol', '').upper()}",
            style="bold cyan",
        )
        return Panel(grid, title=title, subtitle="Prototype", border_style="green")

    def render_placeholder(self) -> Panel:
        return Panel(
            Align.center(
                Text("Очікування SMC payload…", style="cyan"), vertical="middle"
            ),
            border_style="yellow",
        )

    # ── Внутрішні хелпери ---------------------------------------------------
    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        try:
            return int(float(value))
        except Exception:
            return None

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _format_utc_from_ms(value: Any) -> str | None:
        try:
            if value is None:
                return None
            ts = int(float(value)) / 1000.0
            return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") + "Z"
        except Exception:
            return None

    def _get_smc_block(self, asset: dict[str, Any]) -> dict[str, Any]:
        payload = asset.get("smc") or asset.get("smc_hint")
        return payload if isinstance(payload, dict) else {}

    # Simplifiers ------------------------------------------------------------
    def _simplify_events(self, events: Any) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if not isinstance(events, list):
            return result
        # показуємо найсвіжіші події зверху, обмежуючи довжину
        for event in list(reversed(events))[: self.MAX_EVENTS]:
            if not isinstance(event, dict):
                continue
            result.append(
                {
                    "type": event.get("event_type"),
                    "direction": event.get("direction"),
                    "price": self._safe_float(event.get("price_level")),
                    "time": event.get("time"),
                }
            )
        return result

    def _simplify_legs(self, legs: Any) -> list[dict[str, Any]]:
        simplified: list[dict[str, Any]] = []
        if not isinstance(legs, list):
            return simplified
        for leg in legs[-12:]:
            if not isinstance(leg, dict):
                continue
            from_swing = leg.get("from_swing") or {}
            to_swing = leg.get("to_swing") or {}
            simplified.append(
                {
                    "label": leg.get("label"),
                    "from_time": from_swing.get("time"),
                    "to_time": to_swing.get("time"),
                    "from_price": self._safe_float(from_swing.get("price")),
                    "to_price": self._safe_float(to_swing.get("price")),
                }
            )
        return simplified

    def _simplify_swings(self, swings: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not isinstance(swings, list):
            return out
        for swing in swings[-30:]:
            if not isinstance(swing, dict):
                continue
            out.append(
                {
                    "kind": swing.get("kind"),
                    "price": self._safe_float(swing.get("price")),
                    "time": swing.get("time"),
                }
            )
        return out

    def _simplify_ranges(self, ranges: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not isinstance(ranges, list):
            return out
        for rng in ranges[-5:]:
            if not isinstance(rng, dict):
                continue
            out.append(
                {
                    "high": self._safe_float(rng.get("high")),
                    "low": self._safe_float(rng.get("low")),
                    "state": rng.get("state"),
                    "start": rng.get("start_time"),
                    "end": rng.get("end_time"),
                }
            )
        return out

    def _simplify_otes(self, otes: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not isinstance(otes, list):
            return out
        for zone in otes[-6:]:
            if not isinstance(zone, dict):
                continue
            out.append(
                {
                    "direction": zone.get("direction"),
                    "role": zone.get("role"),
                    "ote_min": self._safe_float(zone.get("ote_min")),
                    "ote_max": self._safe_float(zone.get("ote_max")),
                }
            )
        return out

    def _simplify_pools(self, pools: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not isinstance(pools, list):
            return out
        for pool in pools[:8]:
            if not isinstance(pool, dict):
                continue
            out.append(
                {
                    "level": self._safe_float(pool.get("level")),
                    "liq_type": pool.get("liq_type"),
                    "role": pool.get("role"),
                    "strength": self._safe_float(pool.get("strength")),
                }
            )
        return out

    def _simplify_magnets(self, magnets: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if not isinstance(magnets, list):
            return out
        for magnet in magnets[:5]:
            if not isinstance(magnet, dict):
                continue
            out.append(
                {
                    "price_min": self._safe_float(magnet.get("price_min")),
                    "price_max": self._safe_float(magnet.get("price_max")),
                    "role": magnet.get("role"),
                }
            )
        return out

    # Tables ----------------------------------------------------------------
    def _build_summary_table(self, viewer_state: dict[str, Any]) -> Table:
        table = Table(title="Структура", expand=True)
        table.add_column("Поле", justify="right", style="bold")
        table.add_column("Значення", justify="left")
        structure = viewer_state["structure"]
        liquidity = viewer_state["liquidity"]
        table.add_row("Trend", str(structure.get("trend")))
        table.add_row("Bias", str(structure.get("bias")))
        table.add_row("Range", str(structure.get("range_state")))
        table.add_row("AMD", str(liquidity.get("amd_phase")))
        table.add_row("Session", str(viewer_state.get("session")))
        table.add_row("Price", self._format_price(viewer_state.get("price")))
        table.add_row("Payload TS", self._format_ts(viewer_state.get("payload_ts")))
        return table

    def _build_events_table(self, events: list[dict[str, Any]]) -> Table:
        table = Table(title="BOS / CHOCH", expand=True)
        table.add_column("Тип", style="cyan")
        table.add_column("Dir")
        table.add_column("Ціна")
        table.add_column("Час")
        if not events:
            table.add_row("-", "-", "-", "-")
            return table
        for event in events:
            table.add_row(
                str(event.get("type")),
                str(event.get("direction")),
                self._format_price(event.get("price")),
                str(event.get("time")),
            )
        return table

    def _build_ote_table(self, otes: list[dict[str, Any]]) -> Table:
        table = Table(title="OTE зони", expand=True)
        table.add_column("Dir")
        table.add_column("Role")
        table.add_column("Min")
        table.add_column("Max")
        if not otes:
            table.add_row("-", "-", "-", "-")
            return table
        for zone in otes:
            table.add_row(
                str(zone.get("direction")),
                str(zone.get("role")),
                self._format_price(zone.get("ote_min")),
                self._format_price(zone.get("ote_max")),
            )
        return table

    def _build_pools_table(self, pools: list[dict[str, Any]]) -> Table:
        table = Table(title="Пули ліквідності", expand=True)
        table.add_column("Тип")
        table.add_column("Роль")
        table.add_column("Рівень")
        table.add_column("Сила")
        if not pools:
            table.add_row("-", "-", "-", "-")
            return table
        for pool in pools:
            table.add_row(
                str(pool.get("liq_type")),
                str(pool.get("role")),
                self._format_price(pool.get("level")),
                self._format_price(pool.get("strength")),
            )
        return table

    def _format_price(self, value: Any) -> str:
        number = self._safe_float(value)
        if number is None:
            return "-"
        return f"{number:,.2f}".replace(",", " ")

    def _format_ts(self, value: Any) -> str:
        if not value:
            return "-"
        try:
            text = str(value).replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception:
            return str(value)

    def _normalize_fxcm_block(self, fxcm_payload: Any) -> dict[str, Any] | None:
        if not isinstance(fxcm_payload, dict):
            return None
        market_state = str(fxcm_payload.get("market_state") or "unknown").lower()
        process_state = str(fxcm_payload.get("process_state") or "unknown").lower()
        lag_seconds = self._safe_float(fxcm_payload.get("lag_seconds"))
        last_close_ms = self._safe_int(fxcm_payload.get("last_bar_close_ms"))
        next_open_raw = fxcm_payload.get("next_open_utc")
        next_open_utc = str(next_open_raw).strip() if next_open_raw else None
        last_close_iso = self._format_utc_from_ms(last_close_ms)
        return {
            "market_state": market_state,
            "process_state": process_state,
            "lag_seconds": lag_seconds,
            "last_bar_close_ms": last_close_ms,
            "last_bar_close_utc": last_close_iso,
            "next_open_utc": next_open_utc,
        }
