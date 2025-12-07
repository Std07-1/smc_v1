"""Допоміжні функції для cold-start аудиту й статусів."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.history_qa_runner import HistoryQaReport
from data.unified_store import ColdStartCacheEntry, UnifiedDataStore


@dataclass(slots=True)
class ColdstartHistoryReport:
    """Результат перевірки історії в UnifiedDataStore."""

    symbols_total: int
    symbols_ready: int
    symbols_pending: list[str]
    required_bars: int
    status: str
    report_ts: float


def history_report_to_summary(
    report: ColdstartHistoryReport | None,
) -> dict[str, object] | None:
    """Конвертує звіт ensure_min_history у словник для UI/Redis."""

    if report is None:
        return None
    return {
        "status": report.status,
        "symbols_total": report.symbols_total,
        "symbols_ready": report.symbols_ready,
        "symbols_pending": report.symbols_pending,
        "required_bars": report.required_bars,
        "report_ts": report.report_ts,
    }


def qa_report_to_summary(report: HistoryQaReport | None) -> dict[str, object] | None:
    """Конвертує History QA звіт у словник для UI/Redis."""

    if report is None:
        return None
    return report.to_summary()


def summarize_cold_report(
    report: Iterable[ColdStartCacheEntry],
    *,
    stale_threshold: int,
    min_rows: int,
) -> dict[str, object]:
    """Агрегує cold-start звіт у компактну метрику для UI/health."""

    entries = list(report)
    stale = [
        entry.symbol
        for entry in entries
        if entry.age_seconds is not None and entry.age_seconds > stale_threshold
    ]
    insufficient = [
        entry.symbol
        for entry in entries
        if max(entry.rows_in_ram, entry.rows_on_disk) < min_rows
    ]
    max_age = max((entry.age_seconds or 0.0) for entry in entries) if entries else 0.0
    return {
        "total": len(entries),
        "stale_symbols": stale,
        "insufficient_symbols": insufficient,
        "max_age_seconds": round(max_age, 3),
        "min_rows_required": int(min_rows),
        "stale_threshold": int(stale_threshold),
    }


def format_cold_report_table(report: Sequence[ColdStartCacheEntry]) -> str:
    """Форматує cold-start звіт у табличний рядок для CLI."""

    headers = ("symbol", "RAM", "Disk", "TTL", "last_ts", "age_s")
    line = f"{headers[0]:<10} {headers[1]:>6} {headers[2]:>6} {headers[3]:>6} {headers[4]:>20} {headers[5]:>10}"
    sep = "-" * len(line)
    rows = [line, sep]
    for entry in report:
        ttl = entry.redis_ttl if entry.redis_ttl is not None else "-"
        last_ts = (
            datetime.fromtimestamp(entry.last_open_time, tz=UTC).isoformat()
            if entry.last_open_time is not None
            else "-"
        )
        age = f"{entry.age_seconds:.1f}" if entry.age_seconds is not None else "-"
        rows.append(
            f"{entry.symbol:<10} {entry.rows_in_ram:>6} {entry.rows_on_disk:>6} "
            f"{str(ttl):>6} {last_ts:>20} {age:>10}"
        )
    return "\n".join(rows)


def build_status_payload(
    *,
    phase: str,
    history: dict[str, object] | None = None,
    qa: dict[str, object] | None = None,
    summary: dict[str, object] | None = None,
    entries: Sequence[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Формує Redis-пейлоад cold-start статусу."""

    history_block = history if history is not None else summary
    payload: dict[str, object] = {
        "state": phase,
        "phase": phase,
        "history": history_block,
        "summary": history_block,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if qa is not None:
        payload["qa"] = qa
    if entries is not None:
        payload["entries"] = list(entries)
    return payload


def _normalize_symbols(symbols: Sequence[str]) -> list[str]:
    uniq: list[str] = []
    seen: set[str] = set()
    for sym in symbols:
        if not sym:
            continue
        key = str(sym).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        uniq.append(key)
    return uniq


async def build_cold_start_report_payload(
    store: UnifiedDataStore,
    symbols: Sequence[str],
    *,
    interval: str,
    min_rows: int,
    stale_threshold: int,
) -> tuple[dict[str, object], list[ColdStartCacheEntry]]:
    """Готує payload cold-start репорту, що узгоджений із CLI."""

    normalized = _normalize_symbols(symbols)
    if not normalized:
        raise ValueError("Список символів для cold-start порожній")
    entries = await store.build_cold_start_report(normalized, interval)
    summary = summarize_cold_report(
        entries,
        stale_threshold=max(1, int(stale_threshold)),
        min_rows=max(1, int(min_rows)),
    )
    payload: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "interval": interval,
        "symbols": normalized,
        "summary": summary,
        "entries": [entry.to_dict() for entry in entries],
    }
    return payload, entries


def persist_cold_start_report(path: Path | str, payload: dict[str, object]) -> Path:
    """Зберігає cold-start payload у JSON-файл."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target


async def ensure_min_history(
    store: UnifiedDataStore,
    symbols: list[str],
    *,
    interval: str,
    required_bars: int,
    timeout_sec: int,
    sleep_sec: float = 1.0,
) -> ColdstartHistoryReport:
    """Перевіряє наявність мінімальної кількості барів у UnifiedDataStore."""

    deadline = time.monotonic() + max(0, timeout_sec)
    required_bars = max(1, int(required_bars))
    pending = {sym.lower() for sym in symbols if sym}
    ready: set[str] = set()

    while pending and time.monotonic() < deadline:
        to_remove: set[str] = set()
        for sym in list(pending):
            try:
                df = await store.get_df(sym, interval, limit=required_bars)
            except Exception:
                continue
            if df is None:
                continue
            try:
                length = len(df)
            except Exception:
                length = 0
            if length >= required_bars:
                ready.add(sym)
                to_remove.add(sym)
        pending -= to_remove
        if pending and time.monotonic() < deadline:
            await asyncio.sleep(max(0.1, float(sleep_sec)))

    if not pending:
        status = "success"
    elif ready:
        status = "degraded"
    else:
        status = "timeout"
    report = ColdstartHistoryReport(
        symbols_total=len(symbols),
        symbols_ready=len(ready),
        symbols_pending=sorted(pending),
        required_bars=required_bars,
        status=status,
        report_ts=time.time(),
    )
    return report
