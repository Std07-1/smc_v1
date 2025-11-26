"""Адаптер, який будує SmcInput з UnifiedDataStore."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import pandas as pd

from data.unified_store import UnifiedDataStore
from smc_core.smc_types import SmcInput
from utils.utils import ensure_timestamp_column


async def build_smc_input_from_store(
    store: UnifiedDataStore,
    symbol: str,
    tf_primary: str,
    *,
    tfs_extra: Sequence[str] = ("5m", "15m", "1h"),
    limit: int | None = 500,
    context: dict[str, Any] | None = None,
) -> SmcInput:
    """Читає OHLCV по кількох ТF та формує SmcInput."""

    timeframes = _unique_timeframes(tf_primary, tfs_extra)
    tasks = [store.get_df(symbol, tf, limit=limit) for tf in timeframes]
    frames = await asyncio.gather(*tasks)
    normalized: dict[str, pd.DataFrame] = {}
    for tf, frame in zip(timeframes, frames, strict=True):
        normalized[tf] = _normalize_frame(frame)
    return SmcInput(
        symbol=symbol,
        tf_primary=tf_primary,
        ohlc_by_tf=normalized,
        context=context or {},
    )


def _unique_timeframes(tf_primary: str, tfs_extra: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for tf in (tf_primary, *tfs_extra):
        if tf not in seen:
            seen.add(tf)
            ordered.append(tf)
    return ordered


def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    df = frame.copy()
    if "timestamp" not in df.columns:
        for fallback in ("open_time", "time", "close_time"):
            if fallback in df.columns:
                df["timestamp"] = df[fallback]
                break
    df = ensure_timestamp_column(
        df,
        drop_duplicates=False,
        sort=False,
        min_rows=1,
        log_prefix="smc_input:",
    )
    if df.empty:
        return pd.DataFrame()
    if "timestamp" not in df.columns:
        return df.reset_index(drop=True)
    ts = df["timestamp"]
    if not pd.api.types.is_datetime64_any_dtype(ts):
        df["timestamp"] = pd.to_datetime(ts, errors="coerce", utc=True)
    df = df.dropna(subset=["timestamp"]).reset_index(drop=True)
    return df
