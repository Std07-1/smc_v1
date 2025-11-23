"""Адаптер, який будує SmcInput з UnifiedDataStore."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import pandas as pd

from data.unified_store import UnifiedDataStore
from smc_core.smc_types import SmcInput


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
    if "open_time" in df.columns:
        df = df.sort_values("open_time").reset_index(drop=True)
    return df
