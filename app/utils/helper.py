"""Хелпери для роботи з UnifiedDataStore та OHLCV фреймами."""

from __future__ import annotations

from typing import Any

import pandas as pd

_OHLCV_COLS = ("timestamp", "open", "high", "low", "close", "volume")


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Повертає DataFrame з гарантованою колонкою timestamp у UTC."""

    if df is None or df.empty:
        return pd.DataFrame(columns=_OHLCV_COLS)

    work = df.copy()
    ts_col: pd.Series | None = None
    if "timestamp" in work.columns:
        ts_col = pd.to_datetime(work["timestamp"], utc=True, errors="coerce")
    elif "open_time" in work.columns:
        ts_col = pd.to_datetime(work["open_time"], unit="ms", utc=True, errors="coerce")
    if ts_col is None:
        raise ValueError("OHLCV frame does not contain timestamp/open_time")

    work["timestamp"] = ts_col
    work = work.dropna(subset=["timestamp"])
    work = work.sort_values("timestamp").reset_index(drop=True)

    for col in _OHLCV_COLS:
        if col not in work.columns:
            work[col] = pd.NA
    return work[list(_OHLCV_COLS)]


async def store_to_dataframe(
    store: Any,
    symbol: str,
    *,
    interval: str = "1m",
    limit: int | None = None,
) -> pd.DataFrame | None:
    """Читає бари з UnifiedDataStore та повертає нормалізований DataFrame."""

    if store is None:
        return None
    getter = getattr(store, "get_df", None)
    if getter is None:
        return None
    df = await getter(symbol, interval, limit=limit)
    if df is None or len(df) == 0:
        return None
    if not isinstance(df, pd.DataFrame):
        df = pd.DataFrame(df)
    normalized = _normalize_ohlcv(df)
    if limit:
        normalized = normalized.tail(limit).reset_index(drop=True)
    return normalized


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Ресемплить OHLCV DataFrame на заданий інтервал."""

    if df is None or df.empty:
        return pd.DataFrame(columns=_OHLCV_COLS)
    work = df.copy()
    work = work.dropna(subset=["timestamp"])
    if work.empty:
        return pd.DataFrame(columns=_OHLCV_COLS)
    work = work.set_index(pd.to_datetime(work["timestamp"], utc=True))
    agg = work.resample(rule).agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    agg = agg.dropna(subset=["open", "high", "low", "close"], how="any")
    agg.reset_index(inplace=True)
    agg.rename(columns={"index": "timestamp"}, inplace=True)
    return agg[list(_OHLCV_COLS)]


def resample_5m(df: pd.DataFrame) -> pd.DataFrame:
    """Швидкий ресемпл 1m→5m."""

    return resample_ohlcv(df, "5min")


def resample_1h(df: pd.DataFrame) -> pd.DataFrame:
    """Швидкий ресемпл 1m→1h."""

    return resample_ohlcv(df, "1h")


def estimate_atr_pct(df: pd.DataFrame, period: int = 14) -> float:
    """Оцінює ATR у відсотках від ціни для останнього бару."""

    if df is None or df.empty or period <= 1:
        return 0.0
    work = df.tail(period + 1).copy()
    work = work.dropna(subset=["high", "low", "close"])
    if len(work) <= 1:
        return 0.0
    high = work["high"].astype(float)
    low = work["low"].astype(float)
    close = work["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
    last_close = float(close.iloc[-1]) if len(close) else 0.0
    if last_close <= 0:
        return 0.0
    return float(atr / last_close)
