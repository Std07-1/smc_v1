"""Автоматичний QA-прогін smc_core поверх історичної вибірки."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from data.unified_store import UnifiedDataStore
from smc_core.engine import SmcCoreEngine
from smc_core.input_adapter import build_smc_input_from_store
from smc_core.serializers import to_plain_smc_hint
from smc_core.smc_types import SmcInput

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class HistoryQaConfig:
    """Конфіг історичного QA для cold-start."""

    tf_primary: str
    tfs_extra: Sequence[str]
    limit: int
    step: int = 1
    min_bars_per_snapshot: int = 50
    warmup_bars: int = 0


@dataclass(slots=True)
class HistoryQaSymbolReport:
    """Результати QA для одного символу."""

    symbol: str
    status: str = "pending"
    bars_requested: int = 0
    bars_available: int = 0
    bars_processed: int = 0
    snapshots_written: int = 0
    warmup_bars: int = 0
    error: str | None = None

    def to_summary(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "status": self.status,
            "bars_requested": self.bars_requested,
            "bars_available": self.bars_available,
            "bars_processed": self.bars_processed,
            "snapshots_written": self.snapshots_written,
            "warmup_bars": self.warmup_bars,
            "error": self.error,
        }


@dataclass(slots=True)
class HistoryQaReport:
    """Агрегований звіт History QA по набору символів."""

    status: str
    symbols: list[HistoryQaSymbolReport] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    warmup_bars: int = 0
    bars_requested_per_symbol: int = 0

    def to_summary(self) -> dict[str, object]:
        total = len(self.symbols)
        success = sum(1 for item in self.symbols if item.status == "success")
        errors = sum(1 for item in self.symbols if item.status == "error")
        skipped = sum(
            1 for item in self.symbols if item.status not in {"success", "error"}
        )
        bars_processed = sum(item.bars_processed for item in self.symbols)
        bars_requested = sum(item.bars_requested for item in self.symbols)
        finished = self.finished_at or time.time()
        return {
            "status": self.status,
            "symbols_total": total,
            "symbols_success": success,
            "symbols_error": errors,
            "symbols_skipped": skipped,
            "bars_processed": bars_processed,
            "bars_requested_total": bars_requested,
            "bars_requested_per_symbol": self.bars_requested_per_symbol,
            "warmup_bars": self.warmup_bars,
            "started_at": self.started_at,
            "finished_at": finished,
            "duration_sec": round(finished - self.started_at, 3),
            "symbols": [item.to_summary() for item in self.symbols],
        }


def get_smc_history_path(base_dir: Path, symbol: str, tf: str) -> Path:
    """Повертає шлях до JSONL-файлу з plain SMC hints."""

    symbol_norm = symbol.lower()
    tf_norm = tf.lower()
    return base_dir / f"{symbol_norm}_smc_{tf_norm}_history.jsonl"


async def run_history_qa_for_symbols(
    store: UnifiedDataStore,
    symbols: Sequence[str],
    config: HistoryQaConfig,
    *,
    engine: SmcCoreEngine | None = None,
) -> HistoryQaReport:
    """Запускає QA-прохід smc_core для набору символів."""

    normalized_symbols = [str(sym).lower() for sym in symbols if sym]
    report_items: list[HistoryQaSymbolReport] = []
    qa_engine = engine or SmcCoreEngine()
    started = time.time()
    for symbol in normalized_symbols:
        item = HistoryQaSymbolReport(
            symbol=symbol,
            bars_requested=config.limit,
            warmup_bars=max(0, int(config.warmup_bars)),
        )
        report_items.append(item)
        try:
            await _run_single_symbol(store, symbol, config, qa_engine, item)
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.exception("[HistoryQA] Символ %s впав", symbol)
            item.status = "error"
            item.error = str(exc)
    final_status = _derive_report_status(report_items)
    qa_report = HistoryQaReport(
        status=final_status,
        symbols=report_items,
        started_at=started,
        warmup_bars=max(0, int(config.warmup_bars)),
        bars_requested_per_symbol=int(config.limit),
    )
    qa_report.finished_at = time.time()
    logger.info(
        "[HistoryQA] Завершено status=%s symbols=%d duration=%.2fs",
        qa_report.status,
        len(report_items),
        qa_report.finished_at - qa_report.started_at,
    )
    return qa_report


async def _run_single_symbol(
    store: UnifiedDataStore,
    symbol: str,
    config: HistoryQaConfig,
    engine: SmcCoreEngine,
    item: HistoryQaSymbolReport,
) -> None:
    base_dir = Path(getattr(store.cfg, "base_dir", "./datastore"))
    tf_primary = config.tf_primary
    full_input = await build_smc_input_from_store(
        store,
        symbol,
        tf_primary,
        tfs_extra=config.tfs_extra,
        limit=config.limit,
    )
    primary_frame = full_input.ohlc_by_tf.get(tf_primary)
    if primary_frame is None or primary_frame.empty:
        item.status = "error"
        item.error = "empty_primary_frame"
        return
    bars_available = len(primary_frame)
    item.bars_available = bars_available
    limit_val = max(0, int(config.limit))
    item.bars_requested = min(limit_val or bars_available, bars_available)
    min_required = max(
        int(config.min_bars_per_snapshot),
        max(0, int(config.warmup_bars)) + 1,
    )
    if bars_available < min_required:
        item.status = "skipped"
        item.error = f"insufficient_bars:{bars_available}"
        return

    warmup_target = max(0, int(config.warmup_bars))
    start_index = min(
        max(warmup_target, int(config.min_bars_per_snapshot) - 1),
        bars_available - 1,
    )
    item.warmup_bars = start_index
    step = max(1, int(config.step))
    indices = list(range(start_index, bars_available, step))
    if not indices:
        item.status = "skipped"
        item.error = "no_indices"
        return

    lines: list[str] = []
    for idx in indices:
        pivot_ts = _extract_timestamp(primary_frame, idx)
        sliced_input = _slice_input(full_input, pivot_ts, idx)
        hint = engine.process_snapshot(sliced_input)
        payload = _build_history_record(
            symbol=symbol,
            tf=tf_primary,
            bar_index=idx,
            bar_count=bars_available,
            frame=sliced_input.ohlc_by_tf.get(tf_primary),
            hint_dict=to_plain_smc_hint(hint),
        )
        lines.append(json.dumps(payload, ensure_ascii=False))

    path = get_smc_history_path(base_dir=base_dir, symbol=symbol, tf=tf_primary)
    await _write_jsonl(path, lines)
    item.status = "success"
    item.snapshots_written = len(lines)
    item.bars_processed = len(indices)


def _slice_input(
    full_input: SmcInput, pivot_ts: pd.Timestamp | None, pivot_idx: int
) -> SmcInput:
    sliced: dict[str, pd.DataFrame] = {}
    for tf, frame in full_input.ohlc_by_tf.items():
        if frame is None or frame.empty:
            sliced[tf] = pd.DataFrame()
            continue
        if tf == full_input.tf_primary:
            sliced[tf] = frame.iloc[: pivot_idx + 1].copy().reset_index(drop=True)
        else:
            if pivot_ts is None or "timestamp" not in frame.columns:
                sliced[tf] = frame.copy().reset_index(drop=True)
            else:
                mask = frame["timestamp"] <= pivot_ts
                sliced_frame = frame.loc[mask].copy().reset_index(drop=True)
                sliced[tf] = sliced_frame
    return SmcInput(
        symbol=full_input.symbol,
        tf_primary=full_input.tf_primary,
        ohlc_by_tf=sliced,
        context=full_input.context,
    )


def _extract_timestamp(frame: pd.DataFrame, idx: int) -> pd.Timestamp | None:
    try:
        ts_value = frame["timestamp"].iloc[idx]
    except Exception:
        return None
    try:
        ts = pd.to_datetime(ts_value, utc=True)
        return ts
    except Exception:
        return None


def _build_history_record(
    *,
    symbol: str,
    tf: str,
    bar_index: int,
    bar_count: int,
    frame: pd.DataFrame | None,
    hint_dict: dict[str, object] | None,
) -> dict[str, object]:
    close_price = None
    close_ts_iso = None
    if frame is not None and not frame.empty:
        try:
            close_price = float(frame["close"].iloc[-1])
        except Exception:
            close_price = None
        try:
            ts_raw = frame["timestamp"].iloc[-1]
            close_ts_iso = pd.to_datetime(ts_raw, utc=True).isoformat()
        except Exception:
            close_ts_iso = None
    return {
        "symbol": symbol,
        "tf": tf,
        "bar_index": bar_index,
        "bars_total": bar_count,
        "close_ts": close_ts_iso,
        "close_price": close_price,
        "hint": hint_dict,
    }


async def _write_jsonl(path: Path, lines: Sequence[str]) -> None:
    def _write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for line in lines:
                handle.write(line)
                handle.write("\n")

    await asyncio.to_thread(_write)


def _derive_report_status(items: Sequence[HistoryQaSymbolReport]) -> str:
    if not items:
        return "error"
    if all(item.status == "success" for item in items):
        return "success"
    if all(item.status == "error" for item in items):
        return "error"
    return "partial"


__all__ = [
    "HistoryQaConfig",
    "HistoryQaReport",
    "HistoryQaSymbolReport",
    "run_history_qa_for_symbols",
    "get_smc_history_path",
]


if __name__ == "__main__":  # pragma: no cover
    import argparse
    import asyncio as _asyncio

    from app.main import bootstrap
    from config.config import FXCM_FAST_SYMBOLS, SMC_PIPELINE_CFG

    parser = argparse.ArgumentParser(description="History QA runner")
    parser.add_argument("--symbols", nargs="*", default=None, help="Перелік символів")
    parser.add_argument(
        "--limit", type=int, default=None, help="Кількість барів історії"
    )
    parser.add_argument("--step", type=int, default=5, help="Крок між снапшотами")
    args = parser.parse_args()

    async def _cli() -> None:
        store = await bootstrap()
        cfg = HistoryQaConfig(
            tf_primary=str(SMC_PIPELINE_CFG.get("tf_primary", "1m")),
            tfs_extra=tuple(SMC_PIPELINE_CFG.get("tfs_extra", ("5m", "15m", "1h"))),
            limit=int(args.limit or SMC_PIPELINE_CFG.get("limit", 300)),
            step=max(1, int(args.step)),
        )
        symbols = args.symbols or FXCM_FAST_SYMBOLS
        report = await run_history_qa_for_symbols(store, symbols, cfg)
        print(json.dumps(report.to_summary(), ensure_ascii=False, indent=2))

    _asyncio.run(_cli())
