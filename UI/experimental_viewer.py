"""Експериментальний переглядач SMC-блоку (plain JSON → UI state)."""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.align import Align
from rich.console import RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


class SmcExperimentalViewer:
    """Створює легковаговий стан для майбутнього SMC viewer та рендерить його."""

    def __init__(self, symbol: str, snapshot_dir: str = "tmp") -> None:
        self.symbol = symbol.lower()
        snapshot_root = Path(snapshot_dir)
        snapshot_root.mkdir(parents=True, exist_ok=True)
        self.snapshot_path = snapshot_root / f"smc_viewer_{self.symbol}.json"

    # ── Публічні методи -----------------------------------------------------
    def build_state(
        self, asset: dict[str, Any], payload_meta: dict[str, Any]
    ) -> dict[str, Any]:
        """Повертає агрегований стан для рендера/експорту."""

        smc_block = self._get_smc_block(asset)
        structure = self._as_dict(smc_block.get("structure"))
        liquidity = self._as_dict(smc_block.get("liquidity"))
        zones = self._as_dict(smc_block.get("zones"))

        stats = self._as_dict(asset.get("stats"))
        payload_ts = payload_meta.get("ts")
        viewer_state = {
            "symbol": asset.get("symbol"),
            "payload_ts": payload_ts,
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
        }
        structure_meta = self._simplify_structure_meta(
            structure.get("meta"), stats, payload_ts
        )
        if structure_meta:
            viewer_state["structure"]["meta"] = structure_meta
        viewer_state["sessions"] = self._build_session_markers(
            stats, structure_meta, payload_ts
        )
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

        structure_block = viewer_state["structure"]
        liquidity_block = viewer_state["liquidity"]

        summary = self._build_summary_table(viewer_state)
        sessions_table = self._build_session_table(viewer_state.get("sessions", []))
        ranges = self._build_ranges_table(structure_block["ranges"])
        swings = self._build_swings_table(structure_block["swings"])
        events_highlights = self._build_event_highlights_table(
            structure_block["events"]
        )
        events = self._build_events_table(structure_block["events"])
        ote = self._build_ote_table(structure_block["ote_zones"])
        legs = self._build_legs_table(structure_block["legs"])
        pools = self._build_pools_table(liquidity_block["pools"])
        magnets = self._build_magnets_table(liquidity_block["magnets"])
        timeline = self._build_timeline_panel(
            events=structure_block["events"],
            ranges=structure_block["ranges"],
            sessions=viewer_state.get("sessions", []),
            meta=structure_block.get("meta") or {},
        )

        grid = Table.grid(expand=True)
        grid.add_row(
            self._two_column(summary, sessions_table, left_ratio=3, right_ratio=2)
        )
        grid.add_row(self._two_column(ranges, swings, left_ratio=3, right_ratio=2))
        grid.add_row(
            self._two_column(events_highlights, events, left_ratio=2, right_ratio=3)
        )
        grid.add_row(self._two_column(ote, legs, left_ratio=2, right_ratio=3))
        grid.add_row(self._two_column(pools, magnets, left_ratio=3, right_ratio=2))
        grid.add_row(timeline)
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
    def _as_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _get_smc_block(self, asset: dict[str, Any]) -> dict[str, Any]:
        payload = asset.get("smc") or asset.get("smc_hint")
        return payload if isinstance(payload, dict) else {}

    # Simplifiers ------------------------------------------------------------
    def _simplify_events(self, events: Any) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if not isinstance(events, list):
            return result
        for event in events[:12]:
            if not isinstance(event, dict):
                continue
            time_value = self._normalize_time_value(
                event.get("ts"), event.get("timestamp"), event.get("time")
            )
            result.append(
                {
                    "type": event.get("event_type"),
                    "direction": event.get("direction"),
                    "price": self._safe_float(event.get("price_level")),
                    "time": time_value,
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
            from_time = self._normalize_time_value(
                from_swing.get("ts"), from_swing.get("time")
            )
            to_time = self._normalize_time_value(
                to_swing.get("ts"), to_swing.get("time")
            )
            simplified.append(
                {
                    "label": leg.get("label"),
                    "from_time": from_time,
                    "to_time": to_time,
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
            time_value = self._normalize_time_value(
                swing.get("ts"), swing.get("time"), swing.get("timestamp")
            )
            out.append(
                {
                    "kind": swing.get("kind"),
                    "price": self._safe_float(swing.get("price")),
                    "time": time_value,
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
            start = self._normalize_time_value(
                rng.get("start_ts"), rng.get("start_time")
            )
            end = self._normalize_time_value(rng.get("end_ts"), rng.get("end_time"))
            out.append(
                {
                    "high": self._safe_float(rng.get("high")),
                    "low": self._safe_float(rng.get("low")),
                    "state": rng.get("state"),
                    "start": start,
                    "end": end,
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
        table = Table(title="Структура", expand=True, pad_edge=False)
        table.add_column("Поле", justify="right", style="bold", width=10, no_wrap=True)
        table.add_column("Значення", justify="left", ratio=1)
        structure = viewer_state["structure"]
        liquidity = viewer_state["liquidity"]
        table.add_row("Trend", str(structure.get("trend")))
        table.add_row("Bias", str(structure.get("bias")))
        table.add_row("Range", str(structure.get("range_state")))
        table.add_row("AMD", str(liquidity.get("amd_phase")))
        payload_ts = self._normalize_time_value(viewer_state.get("payload_ts"))
        table.add_row("Session", str(viewer_state.get("session")))
        table.add_row("Price", self._format_price(viewer_state.get("price")))
        table.add_row("Payload TS", payload_ts or "-")
        return table

    def _build_events_table(self, events: list[dict[str, Any]]) -> RenderableType:
        if not events:
            return self._empty_panel("BOS / CHOCH", "Немає подій у цьому вікні")
        table = Table(title="BOS / CHOCH", expand=True, pad_edge=False)
        table.add_column("Тип", style="cyan", width=6, no_wrap=True)
        table.add_column("Dir", justify="center", width=5, no_wrap=True)
        table.add_column("Ціна", justify="right", width=11, no_wrap=True)
        table.add_column("Час", justify="left", ratio=2)
        for event in events:
            time_value = event.get("time") or "-"
            table.add_row(
                str(event.get("type")),
                str(event.get("direction")),
                self._format_price(event.get("price")),
                time_value,
            )
        return table

    def _build_event_highlights_table(
        self, events: list[dict[str, Any]]
    ) -> RenderableType:
        if not events:
            return self._empty_panel("Останні BOS/CHOCH", "Немає історії BOS/CHOCH")
        table = Table(title="Останні BOS/CHOCH", expand=True, pad_edge=False)
        table.add_column("Подія", width=6, no_wrap=True)
        table.add_column("Dir", width=5, justify="center", no_wrap=True)
        table.add_column("Ціна", justify="right", width=11, no_wrap=True)
        table.add_column("Час", justify="left", ratio=2)
        highlights: dict[tuple[str | None, str | None], dict[str, Any]] = {}
        for event in events or []:
            key = (event.get("type"), event.get("direction"))
            highlights[key] = event
        targets = [
            ("BOS", "LONG"),
            ("BOS", "SHORT"),
            ("CHOCH", "LONG"),
            ("CHOCH", "SHORT"),
        ]
        if not highlights:
            targets = [(None, None)]
        for etype, direction in targets:
            event = highlights.get((etype, direction))
            if not event:
                table.add_row(str(etype or "-"), str(direction or "-"), "-", "-")
                continue
            table.add_row(
                str(etype or "-"),
                str(direction or "-"),
                self._format_price(event.get("price")),
                event.get("time") or "-",
            )
        return table

    def _build_ote_table(self, otes: list[dict[str, Any]]) -> RenderableType:
        if not otes:
            return self._empty_panel("OTE зони", "Зон не знайдено")
        table = Table(title="OTE зони", expand=True, pad_edge=False)
        table.add_column("Dir", width=5, justify="center", no_wrap=True)
        table.add_column("Role", width=8, justify="center", no_wrap=True)
        table.add_column("Min", justify="right", width=10, no_wrap=True)
        table.add_column("Max", justify="right", width=10, no_wrap=True)
        for zone in otes:
            table.add_row(
                str(zone.get("direction")),
                str(zone.get("role")),
                self._format_price(zone.get("ote_min")),
                self._format_price(zone.get("ote_max")),
            )
        return table

    def _build_session_table(self, sessions: list[dict[str, Any]]) -> RenderableType:
        if not sessions:
            return self._empty_panel("Сесії", "Немає активної сесії")
        table = Table(title="Сесії", expand=True, pad_edge=False)
        table.add_column("ID", width=8, no_wrap=True)
        table.add_column("Label", width=8, no_wrap=True)
        table.add_column("Start", justify="left", ratio=1)
        table.add_column("End", justify="left", ratio=1)
        for session in sessions:
            start_ts = session.get("start_ts") or "-"
            end_ts = session.get("end_ts") or "-"
            table.add_row(
                str(session.get("id") or session.get("label") or "-"),
                str(session.get("label") or "-"),
                start_ts,
                end_ts,
            )
        return table

    def _build_legs_table(self, legs: list[dict[str, Any]]) -> RenderableType:
        if not legs:
            return self._empty_panel("Ноги", "Структурні ноги відсутні")
        table = Table(title="Ноги", expand=True, pad_edge=False)
        table.add_column("Label", width=8, no_wrap=True)
        table.add_column("Start", justify="left", ratio=1)
        table.add_column("End", justify="left", ratio=1)
        table.add_column("Δ", justify="right", width=8, no_wrap=True)
        for leg in legs:
            start = leg.get("from_time")
            end = leg.get("to_time")
            delta = self._delta_str(leg.get("from_price"), leg.get("to_price"))
            table.add_row(str(leg.get("label")), start or "-", end or "-", delta)
        return table

    def _build_swings_table(self, swings: list[dict[str, Any]]) -> RenderableType:
        if not swings:
            return self._empty_panel("Swings", "Немає свінгів у вибірці")
        table = Table(title="Swings (останні)", expand=True, pad_edge=False)
        table.add_column("Kind", width=6, no_wrap=True)
        table.add_column("Ціна", justify="right", width=11, no_wrap=True)
        table.add_column("Час", justify="left", ratio=2)
        for swing in swings:
            table.add_row(
                str(swing.get("kind")),
                self._format_price(swing.get("price")),
                swing.get("time") or "-",
            )
        return table

    def _build_ranges_table(self, ranges: list[dict[str, Any]]) -> RenderableType:
        if not ranges:
            return self._empty_panel("Ренджі", "Немає активних ренджів")
        table = Table(title="Ренджі", expand=True, pad_edge=False)
        table.add_column("State", width=7, no_wrap=True)
        table.add_column("Low", justify="right", width=11, no_wrap=True)
        table.add_column("High", justify="right", width=11, no_wrap=True)
        table.add_column("Start", justify="left", ratio=1)
        table.add_column("End", justify="left", ratio=1)
        for rng in ranges:
            table.add_row(
                str(rng.get("state")),
                self._format_price(rng.get("low")),
                self._format_price(rng.get("high")),
                rng.get("start") or "-",
                rng.get("end") or "-",
            )
        return table

    def _build_pools_table(self, pools: list[dict[str, Any]]) -> RenderableType:
        if not pools:
            return self._empty_panel("Пули", "Ліквідність не виявлено")
        table = Table(title="Пули ліквідності", expand=True, pad_edge=False)
        table.add_column("Тип", width=6, no_wrap=True)
        table.add_column("Роль", width=8, no_wrap=True)
        table.add_column("Рівень", justify="right", width=11, no_wrap=True)
        table.add_column("Сила", justify="right", width=11, no_wrap=True)
        for pool in pools:
            table.add_row(
                str(pool.get("liq_type")),
                str(pool.get("role")),
                self._format_price(pool.get("level")),
                self._format_price(pool.get("strength")),
            )
        return table

    def _build_magnets_table(self, magnets: list[dict[str, Any]]) -> RenderableType:
        if not magnets:
            return self._empty_panel("Магніти", "Магніти відсутні")
        table = Table(title="Магніти", expand=True, pad_edge=False)
        table.add_column("Ціна min", justify="right", width=11, no_wrap=True)
        table.add_column("Ціна max", justify="right", width=11, no_wrap=True)
        table.add_column("Роль", width=8, no_wrap=True)
        for magnet in magnets:
            table.add_row(
                self._format_price(magnet.get("price_min")),
                self._format_price(magnet.get("price_max")),
                str(magnet.get("role")),
            )
        return table

    def _simplify_structure_meta(
        self, meta: Any, stats: dict[str, Any], payload_ts: Any
    ) -> dict[str, Any]:
        meta_dict = meta if isinstance(meta, dict) else {}
        simplified: dict[str, Any] = {}
        for key in (
            "snapshot_start_ts",
            "snapshot_end_ts",
            "last_choch_ts",
            "tf_input",
            "bias",
        ):
            value = meta_dict.get(key)
            if value is not None:
                simplified[key] = value
        swing_times = meta_dict.get("swing_times")
        if isinstance(swing_times, list) and swing_times:
            simplified["swing_count"] = len(swing_times)
        session_tag = stats.get("session_tag") or meta_dict.get("session_tag")
        if session_tag:
            simplified["session_tag"] = session_tag
        session_seq = stats.get("session_seq") or meta_dict.get("session_seq")
        if session_seq is not None:
            simplified["session_seq"] = session_seq
        session_id = meta_dict.get("session_id") or stats.get("session_id")
        if not session_id:
            session_id = self._derive_session_id(payload_ts, session_tag, session_seq)
        if session_id:
            simplified["session_id"] = session_id
        return simplified

    def _build_session_markers(
        self,
        stats: dict[str, Any],
        structure_meta: dict[str, Any] | None,
        payload_ts: Any,
    ) -> list[dict[str, Any]]:
        sessions: list[dict[str, Any]] = []
        stats_dict = stats if isinstance(stats, dict) else {}
        meta = structure_meta if isinstance(structure_meta, dict) else {}
        label = stats_dict.get("session_tag") or meta.get("session_tag")
        session_id = meta.get("session_id") or stats_dict.get("session_id")
        session_seq = stats_dict.get("session_seq") or meta.get("session_seq")
        if not session_id:
            session_id = self._derive_session_id(payload_ts, label, session_seq)
        start_ts = (
            stats_dict.get("session_start_ts")
            or stats_dict.get("session_open_time")
            or meta.get("snapshot_start_ts")
            or payload_ts
        )
        end_ts = stats_dict.get("session_end_ts") or stats_dict.get(
            "session_close_time"
        )
        if label or session_id:
            sessions.append(
                {
                    "label": label,
                    "id": session_id,
                    "start_ts": self._coerce_iso_ts(start_ts),
                    "end_ts": self._coerce_iso_ts(end_ts),
                }
            )
        return [session for session in sessions if session.get("start_ts")]

    def _derive_session_id(
        self, payload_ts: Any, session_tag: Any, session_seq: Any
    ) -> str | None:
        if session_seq is not None:
            return str(session_seq)
        dt = self._coerce_datetime(payload_ts)
        if dt is None or session_tag is None:
            return None
        date_part = dt.strftime("%Y%m%d")
        return f"{date_part}-{str(session_tag).upper()}"

    def _coerce_iso_ts(self, value: Any) -> str | None:
        dt = self._coerce_datetime(value)
        if dt is not None:
            return dt.strftime("%Y-%m-%d %H:%M")
        return None

    def _normalize_time_value(self, *candidates: Any) -> str | None:
        for candidate in candidates:
            if candidate is None:
                continue
            iso = self._coerce_iso_ts(candidate)
            if iso:
                return iso
        return None

    def _coerce_datetime(self, value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.year >= 2000 else None
        if hasattr(value, "to_pydatetime"):
            try:
                candidate = value.to_pydatetime()
                if isinstance(candidate, datetime):
                    return candidate
            except Exception:
                pass
        if isinstance(value, str) and not value.strip():
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", ""))
            return dt if dt.year >= 2000 else None
        except Exception:
            pass
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(numeric):
            return None
        magnitude = abs(numeric)
        if magnitude < 1e8:
            return None
        if magnitude >= 1e17:  # наносекунди
            seconds = numeric / 1_000_000_000
        elif magnitude >= 1e14:  # мікросекунди
            seconds = numeric / 1_000_000
        elif magnitude >= 1e11:  # мілісекунди
            seconds = numeric / 1_000
        else:  # секунди
            seconds = numeric
        try:
            dt = datetime.utcfromtimestamp(seconds)
            return dt if dt.year >= 2000 else None
        except Exception:
            return None

    def _build_timeline_panel(
        self,
        *,
        events: list[dict[str, Any]],
        ranges: list[dict[str, Any]],
        sessions: list[dict[str, Any]],
        meta: dict[str, Any],
    ) -> Panel:
        text = Text()
        markers: list[str] = []
        for rng in ranges or []:
            raw_start = rng.get("start")
            start = (
                raw_start
                if isinstance(raw_start, str)
                else self._coerce_iso_ts(raw_start)
            )
            state = rng.get("state")
            if start and state:
                markers.append(f"{start} · range {state}")
        for event in events or []:
            ts = self._coerce_iso_ts(event.get("time"))
            etype = event.get("type")
            direction = event.get("direction")
            if ts and etype:
                postfix = (
                    f"{etype}{'↑' if direction == 'LONG' else '↓' if direction else ''}"
                )
                markers.append(f"{ts} · {postfix}")
        for session in sessions or []:
            start = session.get("start_ts")
            label = session.get("label") or session.get("id")
            if start and label:
                markers.append(f"{start} · Session {label}")
            end = session.get("end_ts")
            if end and label:
                markers.append(f"{end} · Session {label} end")
        meta = meta or {}
        for meta_key, suffix in (
            ("last_choch_ts", "last CHOCH"),
            ("snapshot_start_ts", "snapshot start"),
            ("snapshot_end_ts", "snapshot end"),
        ):
            iso = self._coerce_iso_ts(meta.get(meta_key))
            if iso:
                markers.append(f"{iso} · {suffix}")
        if not markers:
            text.append("—")
        else:
            text.append(" \n".join(markers))
        return Panel(text, title="Timeline", border_style="blue")

    def _empty_panel(self, title: str, message: str) -> Panel:
        return Panel(Text(message, style="dim"), title=title, border_style="red")

    def _two_column(
        self, left: Any, right: Any, *, left_ratio: int = 1, right_ratio: int = 1
    ) -> Table:
        grid = Table.grid(expand=True)
        grid.add_column(ratio=left_ratio)
        grid.add_column(ratio=right_ratio)
        grid.add_row(left, right)
        return grid

    def _delta_str(self, from_price: Any, to_price: Any) -> str:
        start = self._safe_float(from_price)
        end = self._safe_float(to_price)
        if start is None or end is None:
            return "-"
        diff = end - start
        sign = "↑" if diff >= 0 else "↓"
        return f"{sign} {abs(diff):.2f}"

    def _format_price(self, value: Any) -> str:
        number = self._safe_float(value)
        if number is None:
            return "-"
        return f"{number:,.2f}".replace(",", " ")
