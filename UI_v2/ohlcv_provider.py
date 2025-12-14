"""Постачальники OHLCV-барів для HTTP /smc-viewer/ohlcv."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from data.unified_store import UnifiedDataStore
from UI_v2.schemas import OhlcvBar


class OhlcvNotFoundError(Exception):
    """Піднімається, коли OHLCV-даних для symbol/tf немає."""


class OhlcvProvider(Protocol):
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
    ) -> Sequence[OhlcvBar]:
        """Повертає останні ``limit`` барів або кидає виняток."""
        raise NotImplementedError


def _to_millis(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value > 1e12:
            return int(value)
        return int(float(value) * 1000)
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    if isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except ValueError:
            try:
                return int(float(value) * 1000)
            except ValueError:
                return None
    return None


def _safe_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


@dataclass
class UnifiedStoreOhlcvProvider:
    """OhlcvProvider на базі UnifiedDataStore."""

    store: UnifiedDataStore

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
    ) -> list[OhlcvBar]:
        limit = max(1, limit)
        df = await self.store.get_df(symbol, timeframe, limit=limit)
        if df is None or df.empty:
            raise OhlcvNotFoundError(f"OHLCV порожній для {symbol} {timeframe}")

        sort_col = None
        for candidate in ("close_time", "open_time"):
            if candidate in df.columns:
                sort_col = candidate
                break
        if sort_col:
            df = df.sort_values(sort_col)

        trimmed = df.tail(limit)
        records = trimmed.to_dict("records")
        bars: list[OhlcvBar] = []
        for record in records:
            time_ms = _to_millis(record.get("close_time") or record.get("open_time"))
            open_v = _safe_float(record.get("open"))
            high_v = _safe_float(record.get("high"))
            low_v = _safe_float(record.get("low"))
            close_v = _safe_float(record.get("close"))
            volume_v = _safe_float(record.get("volume")) or 0.0
            if (
                time_ms is None
                or open_v is None
                or high_v is None
                or low_v is None
                or close_v is None
            ):
                continue
            bars.append(
                {
                    "time": int(time_ms),
                    "open": float(open_v),
                    "high": float(high_v),
                    "low": float(low_v),
                    "close": float(close_v),
                    "volume": float(volume_v),
                }
            )

        if not bars:
            raise OhlcvNotFoundError(f"OHLCV недоступний для {symbol} {timeframe}")

        return bars
