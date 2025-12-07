"""Експериментальний переглядач SMC-блоку (plain JSON → UI state)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.align import Align
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

FXCM_LAG_COMPACT_THRESHOLD_SECONDS = 5 * 60


class SmcExperimentalViewer:
    """Створює легковаговий стан для майбутнього SMC viewer та рендерить його."""

    MAX_EVENTS: int = 20

    def __init__(self, symbol: str, snapshot_dir: str = "tmp") -> None:
        self.symbol = symbol.lower()
        snapshot_root = Path(snapshot_dir)
        snapshot_root.mkdir(parents=True, exist_ok=True)
        self.snapshot_path = snapshot_root / f"smc_viewer_{self.symbol}.json"
        self._last_price: float | None = None
        self._last_session: str | None = None
        self._last_schema: str | None = None
        self._last_events: list[dict[str, Any]] = []
        self._last_published_bars: int | None = None
        self._last_stream_targets: list[dict[str, Any]] | None = None
        self._last_session_block: dict[str, Any] | None = None

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
        price_value = self._extract_price(asset, stats)
        session_value: str | None = None
        schema_value = self._resolve_schema(payload_meta)
        events = self._persist_events(self._simplify_events(structure.get("events")))
        payload_meta_dict = self._as_dict(payload_meta)
        fxcm_source = fxcm_block if isinstance(fxcm_block, dict) else None
        if fxcm_source is None:
            fxcm_meta_candidate = payload_meta_dict.get("fxcm")
            if isinstance(fxcm_meta_candidate, dict):
                fxcm_source = fxcm_meta_candidate
        normalized_fxcm = self._normalize_fxcm_block(fxcm_source)
        if normalized_fxcm:
            fxcm_session = normalized_fxcm.get("session")
            if isinstance(fxcm_session, dict):
                fxcm_session_tag = fxcm_session.get("tag") or fxcm_session.get("name")
                if fxcm_session_tag:
                    session_value = str(fxcm_session_tag)
        if not session_value:
            session_value = self._resolve_session(asset, stats)
        meta_snapshot = dict(payload_meta_dict)
        if fxcm_source is not None:
            meta_snapshot["fxcm"] = fxcm_source

        viewer_state = {
            "symbol": asset.get("symbol"),
            "payload_ts": payload_meta.get("ts"),
            "payload_seq": payload_meta.get("seq"),
            "schema": schema_value,
            "meta": meta_snapshot,
            "price": price_value,
            "session": session_value,
            "structure": {
                "trend": structure.get("trend"),
                "bias": structure.get("bias"),
                "range_state": structure.get("range_state"),
                "legs": self._simplify_legs(structure.get("legs")),
                "swings": self._simplify_swings(structure.get("swings")),
                "ranges": self._simplify_ranges(structure.get("ranges")),
                "events": events,
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
            "fxcm": normalized_fxcm,
        }
        self._backfill_pool_roles(viewer_state)
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
        fxcm_table = self._build_fxcm_table(viewer_state.get("fxcm"))
        stream_table = self._build_stream_targets_table(viewer_state.get("fxcm"))
        events = self._build_events_table(viewer_state["structure"]["events"])
        ote = self._build_ote_table(viewer_state["structure"]["ote_zones"])
        pools = self._build_pools_table(viewer_state["liquidity"]["pools"])

        grid = Table.grid(expand=True)
        grid.add_row(summary)
        grid.add_row(fxcm_table)
        grid.add_row(stream_table)
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

    def _backfill_pool_roles(self, viewer_state: dict[str, Any]) -> None:
        liquidity = viewer_state.get("liquidity")
        structure = viewer_state.get("structure")
        if not isinstance(liquidity, dict) or not isinstance(structure, dict):
            return
        pools = liquidity.get("pools")
        if not isinstance(pools, list):
            return
        bias = str(structure.get("bias") or "").upper()
        price = self._safe_float(viewer_state.get("price"))
        for pool in pools:
            if not isinstance(pool, dict):
                continue
            current_role = str(pool.get("role") or "").upper()
            if current_role in ("PRIMARY", "COUNTERTREND"):
                continue
            derived = self._derive_pool_role(bias, pool, price)
            if derived:
                pool["role"] = derived

    def _derive_pool_role(
        self,
        bias: str,
        pool: dict[str, Any],
        price: float | None,
    ) -> str | None:
        liq_type = str(pool.get("liq_type") or "").upper()
        level = self._safe_float(pool.get("level"))
        side = None
        meta = pool.get("meta")
        if isinstance(meta, dict):
            side_raw = meta.get("side")
            if side_raw:
                side = str(side_raw).upper()
        if bias in ("LONG", "SHORT"):
            mapped = self._role_from_bias_mapping(bias, liq_type, side)
            if mapped:
                return mapped
        return self._fallback_role_without_bias(liq_type, level, price)

    @staticmethod
    def _role_from_bias_mapping(
        bias: str, liq_type: str, side: str | None
    ) -> str | None:
        if bias == "LONG":
            if liq_type in {"EQL", "TLQ", "SESSION_LOW"}:
                return "PRIMARY"
            if liq_type in {"EQH", "SLQ", "SESSION_HIGH"}:
                return "COUNTERTREND"
            if liq_type == "RANGE_EXTREME":
                if side == "LOW":
                    return "PRIMARY"
                if side == "HIGH":
                    return "COUNTERTREND"
        if bias == "SHORT":
            if liq_type in {"EQH", "SLQ", "SESSION_HIGH"}:
                return "PRIMARY"
            if liq_type in {"EQL", "TLQ", "SESSION_LOW"}:
                return "COUNTERTREND"
            if liq_type == "RANGE_EXTREME":
                if side == "HIGH":
                    return "PRIMARY"
                if side == "LOW":
                    return "COUNTERTREND"
        if liq_type in {"SFP", "WICK_CLUSTER"} and side:
            if bias == "LONG" and side == "LOW":
                return "PRIMARY"
            if bias == "LONG" and side == "HIGH":
                return "COUNTERTREND"
            if bias == "SHORT" and side == "HIGH":
                return "PRIMARY"
            if bias == "SHORT" and side == "LOW":
                return "COUNTERTREND"
        return None

    def _fallback_role_without_bias(
        self,
        liq_type: str,
        level: float | None,
        price: float | None,
    ) -> str | None:
        if liq_type in {"EQL", "TLQ", "SESSION_LOW"}:
            return "PRIMARY"
        if liq_type in {"EQH", "SLQ", "SESSION_HIGH"}:
            return "COUNTERTREND"
        if liq_type == "RANGE_EXTREME" and level is not None and price is not None:
            if level < price:
                return "PRIMARY"
            if level > price:
                return "COUNTERTREND"
        if (
            liq_type in {"SFP", "WICK_CLUSTER"}
            and level is not None
            and price is not None
        ):
            return "PRIMARY" if level < price else "COUNTERTREND"
        return None

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
        table.add_row("Session", str(viewer_state.get("session") or "-"))
        table.add_row("Price", self._format_price(viewer_state.get("price")))
        table.add_row("Payload TS", self._format_ts(viewer_state.get("payload_ts")))
        return table

    def _build_fxcm_table(self, fxcm_block: dict[str, Any] | None) -> Table:
        table = Table(title="FXCM телеметрія", expand=True)
        table.add_column("Метрика", justify="right", style="bold")
        table.add_column("Значення", justify="left")
        if not fxcm_block:
            table.add_row("Статус", "—")
            return table

        def _fmt_bool(value: Any, reason: str | None = None) -> str:
            if value is None:
                return "-"
            prefix = "ON" if bool(value) else "OFF"
            if reason:
                return f"{prefix} ({reason})"
            return prefix

        lag_text = self._format_fxcm_lag(
            fxcm_block.get("lag_seconds"),
            human_hint=fxcm_block.get("lag_human"),
        )
        countdown = fxcm_block.get("countdown") or "-"
        published = fxcm_block.get("published_bars")
        delta = fxcm_block.get("published_bars_delta")
        if published is None:
            published_text = "-"
        elif delta is None:
            published_text = str(published)
        else:
            sign = "+" if delta >= 0 else ""
            published_text = f"{published} ({sign}{delta})"

        market_state = str(fxcm_block.get("market_state", "-")).lower()
        table.add_row("Market", market_state.upper())
        table.add_row(
            "Process",
            str(fxcm_block.get("process_state", "-")).upper(),
        )
        table.add_row(
            "Price feed",
            str(fxcm_block.get("price_state", "-")).upper(),
        )
        table.add_row(
            "OHLCV feed",
            str(fxcm_block.get("ohlcv_state", "-")).upper(),
        )
        table.add_row("Lag", lag_text)
        table.add_row("Опубліковані бари", published_text)

        last_close_value = (
            fxcm_block.get("last_close_utc")
            or fxcm_block.get("last_bar_close_utc")
            or fxcm_block.get("last_bar_close_ms")
        )
        if isinstance(last_close_value, str):
            last_close_text = self._format_ts(last_close_value)
        elif isinstance(last_close_value, (int, float)):
            iso_close = self._format_utc_from_ms(last_close_value)
            last_close_text = (
                self._format_ts(iso_close) if iso_close else str(last_close_value)
            )
        else:
            last_close_text = "-"

        session_block = fxcm_block.get("session")
        if not isinstance(session_block, dict):
            session_block = {}
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
            next_open_text = (
                self._format_ts(iso_next) if iso_next else str(next_open_value)
            )
        elif next_open_value:
            next_open_text = self._format_ts(next_open_value)
        else:
            next_open_text = "-"

        if market_state == "open":
            next_open_text = "-"

        table.add_row("Last close", last_close_text)
        table.add_row("Next open", next_open_text)
        close_countdown = fxcm_block.get("countdown_to_close")
        if not close_countdown:
            close_seconds = fxcm_block.get("seconds_to_close")
            if close_seconds is None and isinstance(session_block, dict):
                close_seconds = session_block.get("seconds_to_close")
            close_countdown = self._format_countdown(close_seconds)
        if market_state != "open":
            close_countdown = "-"
        table.add_row("До закриття", close_countdown or "-")
        table.add_row("До відкриття", countdown)
        table.add_row(
            "Heartbeat",
            self._format_ts(fxcm_block.get("heartbeat_ts")) or "-",
        )
        table.add_row(
            "Market status",
            self._format_ts(fxcm_block.get("market_status_ts")) or "-",
        )
        table.add_row(
            "Pause",
            _fmt_bool(
                fxcm_block.get("market_pause"),
                fxcm_block.get("market_pause_reason"),
            ),
        )
        status_note = fxcm_block.get("status_note")
        if status_note:
            table.add_row("Note", str(status_note))
        if fxcm_block.get("idle_reason"):
            table.add_row("Idle reason", str(fxcm_block.get("idle_reason")))
        if fxcm_block.get("cache_source"):
            table.add_row("Cache", str(fxcm_block.get("cache_source")))
        session = session_block
        if session:
            table.add_row("Session", str(session.get("tag") or "-"))
            table.add_row("Session TZ", str(session.get("timezone") or "-"))
            weekly_open = session.get("weekly_open") or "-"
            weekly_close = session.get("weekly_close") or "-"
            table.add_row("Weekly window", f"{weekly_open} → {weekly_close}")
            daily_breaks = session.get("daily_breaks")
            if isinstance(daily_breaks, list) and daily_breaks:
                breaks = ", ".join(str(entry) for entry in daily_breaks)
                table.add_row("Daily breaks", breaks)
            if session.get("next_open_countdown"):
                table.add_row("Session next open", session["next_open_countdown"])
        return table

    def _build_stream_targets_table(self, fxcm_block: dict[str, Any] | None) -> Table:
        table = Table(title="FXCM stream targets", expand=True)
        table.add_column("Symbol")
        table.add_column("TF")
        table.add_column("Staleness")
        table.add_column("Lag")
        table.add_column("Bars")
        targets = []
        if fxcm_block:
            targets = fxcm_block.get("stream_targets") or []
        if not targets:
            table.add_row("-", "-", "-", "-", "-")
            return table
        for item in targets:
            table.add_row(
                str(item.get("symbol") or "-"),
                str(item.get("tf") or item.get("timeframe") or "-"),
                self._format_seconds(item.get("staleness_seconds")),
                self._format_seconds(item.get("lag_seconds")),
                str(item.get("published_bars") or "-"),
            )
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

    def _format_countdown(self, seconds: Any) -> str:
        secs = self._safe_float(seconds)
        if secs is None or secs < 0:
            return "-"
        total = int(secs)
        hours, remainder = divmod(total, 3600)
        minutes, secs_part = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes:02d}m {secs_part:02d}s"
        if minutes:
            return f"{minutes}m {secs_part:02d}s"
        return f"{secs_part}s"

    def _format_seconds(self, value: Any) -> str:
        secs = self._safe_float(value)
        if secs is None:
            return "-"
        if secs >= 60:
            minutes, rest = divmod(int(secs), 60)
            return f"{minutes}m {rest:02d}s"
        return f"{secs:.1f}s"

    def _format_fxcm_lag(self, lag_seconds: Any, *, human_hint: Any = None) -> str:
        secs = self._safe_float(lag_seconds)
        fallback = str(human_hint).strip() if human_hint else ""
        if secs is None:
            return fallback or "-"
        if secs < FXCM_LAG_COMPACT_THRESHOLD_SECONDS:
            return self._format_lag_compact(secs)
        return self._format_lag_verbose(secs)

    def _format_lag_compact(self, seconds: float) -> str:
        whole = int(seconds)
        fractional = max(0.0, seconds - whole)
        millis = int(round(fractional * 1000))
        if millis >= 1000:
            whole += 1
            millis -= 1000
        if whole and millis:
            return f"{whole}с {millis}мс"
        if whole:
            return f"{whole}с"
        return f"{millis}мс"

    def _format_lag_verbose(self, seconds: float) -> str:
        days, hours, minutes, secs, millis = self._split_duration(seconds)
        time_box = f"{hours:02d}:{minutes:02d}:{secs:02d}"
        if days:
            time_box = f"{days}д {time_box}"
        if millis:
            return f"{time_box} + {millis:03d}мс ({seconds:.1f}с)"
        return f"{time_box} ({seconds:.1f}с)"

    @staticmethod
    def _split_duration(seconds: float) -> tuple[int, int, int, int, int]:
        base_seconds = max(0, int(seconds))
        fractional = seconds - base_seconds
        if fractional < 0:
            fractional = 0.0
        millis = int(round(fractional * 1000))
        if millis >= 1000:
            base_seconds += 1
            millis -= 1000
        days, rem = divmod(base_seconds, 86_400)
        hours, rem = divmod(rem, 3_600)
        minutes, secs = divmod(rem, 60)
        return days, hours, minutes, secs, millis

    @staticmethod
    def _is_placeholder_value(value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            normalized = value.strip().lower()
            return normalized in {"", "-", "_", "none", "null"}
        return False

    def _extract_price(
        self, asset: dict[str, Any], stats: dict[str, Any]
    ) -> float | None:
        numeric_candidates = [
            stats.get("current_price"),
            asset.get("price"),
            asset.get("last_price"),
            stats.get("last_price"),
        ]
        for candidate in numeric_candidates:
            price = self._safe_float(candidate)
            if price is not None:
                self._last_price = price
                return price

        text_candidates: list[Any] = []
        if "price_str" in asset:
            text_candidates.append(asset.get("price_str"))
        if "price_str" in stats:
            text_candidates.append(stats.get("price_str"))

        for candidate in text_candidates:
            parsed = self._parse_numeric_string(candidate)
            if parsed is not None:
                self._last_price = parsed
                return parsed

        return self._last_price

    @staticmethod
    def _parse_numeric_string(value: Any) -> float | None:
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        match = re.search(r"[-+]?\d[\d\s,\.]*", text)
        if not match:
            return None
        token = match.group(0)
        token = token.replace("\u00a0", " ")
        token = token.replace(" ", "")
        if not token:
            return None
        has_dot = "." in token
        has_comma = "," in token
        if has_dot and has_comma:
            token = token.replace(",", "")
        elif has_comma:
            if token.count(",") == 1 and len(token.split(",")[-1]) <= 2:
                token = token.replace(",", ".")
            else:
                token = token.replace(",", "")
        if token.count(".") > 1:
            parts = token.split(".")
            decimal = parts[-1]
            token = "".join(parts[:-1]) + "." + decimal
        try:
            return float(token)
        except ValueError:
            return None

    def _normalize_fxcm_block(self, fxcm_payload: Any) -> dict[str, Any] | None:
        if not isinstance(fxcm_payload, dict):
            return None
        market_state = str(fxcm_payload.get("market_state") or "unknown").lower()
        process_state = str(fxcm_payload.get("process_state") or "unknown").lower()
        price_state = str(fxcm_payload.get("price_state") or "unknown").lower()
        ohlcv_state = str(fxcm_payload.get("ohlcv_state") or "unknown").lower()
        lag_seconds = self._safe_float(fxcm_payload.get("lag_seconds"))
        last_close_ms = self._safe_int(fxcm_payload.get("last_bar_close_ms"))
        next_open_raw = fxcm_payload.get("next_open_utc")
        next_open_utc: Any | None = None
        if not self._is_placeholder_value(next_open_raw):
            if isinstance(next_open_raw, str):
                next_open_utc = next_open_raw.strip()
            else:
                next_open_utc = next_open_raw
        last_close_iso = self._format_utc_from_ms(last_close_ms)
        session_block = self._normalize_session_block(fxcm_payload.get("session"))
        if session_block:
            self._last_session_block = session_block
        elif self._last_session_block:
            session_block = dict(self._last_session_block)
        session_next_seconds = None
        session_close_seconds = None
        if session_block:
            session_next_seconds = self._safe_float(
                session_block.get("next_open_seconds")
                or session_block.get("seconds_to_next_open")
            )
            session_close_seconds = self._safe_float(
                session_block.get("seconds_to_close")
            )
            if next_open_utc is None:
                session_next_open = session_block.get(
                    "next_open_utc"
                ) or session_block.get("next_open_ms")
                if not self._is_placeholder_value(session_next_open):
                    next_open_utc = session_next_open
        seconds_to_open = self._safe_float(fxcm_payload.get("seconds_to_open"))
        if seconds_to_open is None:
            seconds_to_open = self._safe_float(
                fxcm_payload.get("session_seconds_to_next_open")
            )
        if seconds_to_open is None:
            seconds_to_open = session_next_seconds
        seconds_to_close = self._safe_float(fxcm_payload.get("seconds_to_close"))
        if seconds_to_close is None:
            seconds_to_close = self._safe_float(
                fxcm_payload.get("session_seconds_to_close")
            )
        if seconds_to_close is None:
            seconds_to_close = session_close_seconds
        market_pause = fxcm_payload.get("market_pause")
        market_pause_reason = fxcm_payload.get("market_pause_reason")
        idle_reason = fxcm_payload.get("idle_reason")
        cache_source = fxcm_payload.get("cache_source")
        published_bars = self._safe_int(fxcm_payload.get("published_bars"))
        delta: int | None = None
        if published_bars is not None:
            if self._last_published_bars is not None:
                diff = published_bars - self._last_published_bars
                if diff >= 0:
                    delta = diff
            self._last_published_bars = published_bars
        heartbeat_ts = fxcm_payload.get("heartbeat_ts")
        market_status_ts = fxcm_payload.get("market_status_ts")
        stream_targets = self._normalize_stream_targets(
            fxcm_payload.get("stream_targets")
        )
        if stream_targets:
            self._last_stream_targets = stream_targets
        elif self._last_stream_targets:
            stream_targets = list(self._last_stream_targets)
        status_note = fxcm_payload.get("status_note")
        status_ts = fxcm_payload.get("status_ts_iso") or fxcm_payload.get("status_ts")
        return {
            "market_state": market_state,
            "process_state": process_state,
            "price_state": price_state,
            "ohlcv_state": ohlcv_state,
            "lag_seconds": lag_seconds,
            "lag_human": fxcm_payload.get("lag_human"),
            "last_bar_close_ms": last_close_ms,
            "last_bar_close_utc": last_close_iso,
            "next_open_utc": next_open_utc,
            "seconds_to_open": seconds_to_open,
            "countdown": self._format_countdown(seconds_to_open),
            "seconds_to_close": seconds_to_close,
            "countdown_to_close": (
                self._format_countdown(seconds_to_close)
                if seconds_to_close is not None
                else None
            ),
            "market_pause": market_pause,
            "market_pause_reason": market_pause_reason,
            "idle_reason": idle_reason,
            "cache_source": cache_source,
            "published_bars": published_bars,
            "published_bars_delta": delta,
            "heartbeat_ts": heartbeat_ts,
            "market_status_ts": market_status_ts,
            "status_note": status_note,
            "status_ts": status_ts,
            "stream_targets": stream_targets,
            "session": session_block,
        }

    def _normalize_stream_targets(self, targets: Any) -> list[dict[str, Any]] | None:
        normalized: list[dict[str, Any]] = []
        if isinstance(targets, list):
            source_iter = targets
        elif isinstance(targets, dict):
            source_iter = []
            for symbol, meta in targets.items():
                entry = {"symbol": symbol}
                if isinstance(meta, dict):
                    entry.update(meta)
                source_iter.append(entry)
        else:
            source_iter = []
        for item in source_iter:
            if not isinstance(item, dict):
                continue
            normalized.append({k: v for k, v in item.items()})
        return normalized or None

    def _normalize_session_block(self, session: Any) -> dict[str, Any] | None:
        if not isinstance(session, dict):
            return None
        result: dict[str, Any] = {}
        base_keys = (
            "tag",
            "name",
            "state",
            "timezone",
            "weekly_open",
            "weekly_close",
            "next_open_utc",
            "next_open_ms",
            "current_open_utc",
            "current_close_utc",
            "session_open_utc",
            "session_close_utc",
            "session_open_ms",
            "session_close_ms",
            "session_windows",
        )
        for key in base_keys:
            value = session.get(key)
            if value is None:
                continue
            result[key] = value
        seconds_to_close = session.get("seconds_to_close")
        if seconds_to_close is not None:
            result["seconds_to_close"] = seconds_to_close
            result["close_countdown"] = self._format_countdown(seconds_to_close)
        next_seconds = (
            session.get("seconds_to_next_open")
            or session.get("next_open_seconds")
            or session.get("next_open_in_seconds")
        )
        if next_seconds is not None:
            result["next_open_seconds"] = next_seconds
            result["next_open_countdown"] = self._format_countdown(next_seconds)
        daily_breaks = session.get("daily_breaks")
        breaks_normalized: list[str] = []
        if isinstance(daily_breaks, list):
            for entry in daily_breaks:
                if isinstance(entry, str):
                    breaks_normalized.append(entry)
                elif isinstance(entry, dict):
                    start = entry.get("start") or "?"
                    end = entry.get("end") or "?"
                    tz = entry.get("tz") or entry.get("timezone")
                    if tz:
                        breaks_normalized.append(f"{start}-{end}@{tz}")
                    else:
                        breaks_normalized.append(f"{start}-{end}")
        if breaks_normalized:
            result["daily_breaks"] = breaks_normalized
        stats_block = session.get("stats")
        if isinstance(stats_block, dict) and stats_block:
            result["stats"] = stats_block
        return result or None

    def _resolve_session(
        self, asset: dict[str, Any], stats: dict[str, Any]
    ) -> str | None:
        candidates = (
            stats.get("session_tag"),
            stats.get("session"),
            asset.get("session"),
            asset.get("session_tag"),
        )
        for candidate in candidates:
            if candidate:
                session_value = str(candidate)
                self._last_session = session_value
                return session_value
        return self._last_session

    def _resolve_schema(self, payload_meta: dict[str, Any]) -> str | None:
        schema_value: str | None = None
        if isinstance(payload_meta, dict):
            for key in ("schema", "schema_version", "version"):
                value = payload_meta.get(key)
                if value:
                    schema_value = str(value)
                    break
        if schema_value:
            self._last_schema = schema_value
            return schema_value
        return self._last_schema

    def _persist_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if events:
            self._last_events = [dict(event) for event in events]
            return events
        return [dict(event) for event in self._last_events]
