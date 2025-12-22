"""Постачальники OHLCV-барів для HTTP /smc-viewer/ohlcv."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import pandas as pd

from core.contracts.viewer_state import OhlcvBar
from core.serialization import safe_float
from data.unified_store import UnifiedDataStore


class OhlcvNotFoundError(Exception):
    """Піднімається, коли OHLCV-даних для symbol/tf немає."""


class OhlcvProvider(Protocol):
    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        *,
        to_ms: int | None = None,
    ) -> Sequence[OhlcvBar]:
        """Повертає останні ``limit`` барів або кидає виняток."""
        raise NotImplementedError


def _tf_ms(tf: str) -> int:
    tf_norm = str(tf).strip().lower()
    if tf_norm.endswith("m"):
        return int(tf_norm[:-1]) * 60_000
    if tf_norm.endswith("h"):
        return int(tf_norm[:-1]) * 60 * 60_000
    if tf_norm.endswith("d"):
        return int(tf_norm[:-1]) * 24 * 60 * 60_000
    raise ValueError(f"Непідтримуваний TF: {tf}")


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


@dataclass
class UnifiedStoreOhlcvProvider:
    """OhlcvProvider на базі UnifiedDataStore."""

    store: UnifiedDataStore

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        *,
        to_ms: int | None = None,
    ) -> list[OhlcvBar]:
        limit = max(1, limit)
        # Важливо для replay: якщо задано to_ms (курсор часу), нам потрібно
        # робити tail(limit) ПОСЛІ фільтрації (інакше на ранніх кроках отримаємо порожній графік).
        df = await self.store.get_df(symbol, timeframe, limit=None if to_ms else limit)
        if df is None or df.empty:
            raise OhlcvNotFoundError(f"OHLCV порожній для {symbol} {timeframe}")

        if to_ms is not None:
            # Фільтруємо лише бари, які "відомі" на момент to_ms.
            # У відповіді UI ми кодуємо time як close_time (якщо є), інакше як open_time.
            to_ms_int = int(to_ms)
            if "close_time" in df.columns:
                close_time = pd.to_numeric(df["close_time"], errors="coerce")
                df = df[close_time <= to_ms_int]
            elif "open_time" in df.columns:
                open_time = pd.to_numeric(df["open_time"], errors="coerce")
                df = df[(open_time + _tf_ms(timeframe)) <= to_ms_int]
            elif "timestamp" in df.columns:
                ts = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
                open_ns = ts.astype("int64")
                open_ms = (open_ns // 1_000_000).where(open_ns > 0)
                df = df[(open_ms + _tf_ms(timeframe)) <= to_ms_int]

            if df is None or df.empty:
                return []

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
            open_v = safe_float(record.get("open"), finite=True)
            high_v = safe_float(record.get("high"), finite=True)
            low_v = safe_float(record.get("low"), finite=True)
            close_v = safe_float(record.get("close"), finite=True)
            volume_v = safe_float(record.get("volume"), finite=True) or 0.0
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
