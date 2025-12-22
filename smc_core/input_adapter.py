"""Адаптер, який будує SmcInput з UnifiedDataStore."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

import pandas as pd

from data.unified_store import UnifiedDataStore
from smc_core.smc_types import SmcInput


def build_smc_input_from_frames(
    *,
    symbol: str,
    tf_primary: str,
    ohlc_by_tf: dict[str, pd.DataFrame],
    context: dict[str, Any] | None = None,
) -> SmcInput:
    """Будує SmcInput з уже підготовлених DataFrame (без UnifiedDataStore).

    Використовує ті самі правила, що й `build_smc_input_from_store`:
    - нормалізує фрейми (timestamp з open_time у мс, stable sort);
    - best-effort додає контекст торгових сесій (Asia/London/NY).

    Це потрібно для QA/реплеїв та інших офлайн-пайплайнів, щоб не роз'їжджались
    поведінка та контекст із продовим адаптером.
    """

    normalized: dict[str, pd.DataFrame] = {}
    try:
        for tf, frame in (ohlc_by_tf or {}).items():
            normalized[str(tf)] = _normalize_frame(frame)
    except Exception:
        normalized = {}

    merged_context: dict[str, Any] = dict(context or {})
    try:
        merged_context.update(
            _build_sessions_context(ohlc_by_tf=normalized, tf_primary=tf_primary)
        )
    except Exception:
        # best-effort: сесійні ключі не повинні ламати hot-path/QA
        pass

    return SmcInput(
        symbol=symbol,
        tf_primary=tf_primary,
        ohlc_by_tf=normalized,
        context=merged_context,
    )


async def build_smc_input_from_store(
    store: UnifiedDataStore,
    symbol: str,
    tf_primary: str,
    *,
    tfs_extra: Sequence[str] = ("1m", "1h", "4h"),
    limit: int | None = 500,
    context: dict[str, Any] | None = None,
) -> SmcInput:
    """Читає OHLCV по кількох ТF та формує SmcInput."""

    timeframes = _unique_timeframes(tf_primary, tfs_extra)
    tasks = [store.get_df(symbol, tf, limit=limit) for tf in timeframes]
    frames = await asyncio.gather(*tasks)
    raw_frames: dict[str, pd.DataFrame] = {}
    for tf, frame in zip(timeframes, frames, strict=True):
        raw_frames[str(tf)] = frame

    return build_smc_input_from_frames(
        symbol=symbol,
        tf_primary=tf_primary,
        ohlc_by_tf=raw_frames,
        context=context,
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
    if "open_time" not in df.columns:
        return pd.DataFrame()

    open_time = pd.to_numeric(df["open_time"], errors="coerce")
    df["timestamp"] = pd.to_datetime(open_time, unit="ms", errors="coerce", utc=True)
    df = df.dropna(subset=["timestamp"]).copy()
    if df.empty:
        return pd.DataFrame()
    if "open_time" in df.columns:
        df = df.sort_values("open_time", kind="stable")
    return df.reset_index(drop=True)


# smc_core/input_adapter.py (оновлені функції)


def _pick_session_frame(
    *, ohlc_by_tf: dict[str, pd.DataFrame], tf_primary: str
) -> tuple[pd.DataFrame, str] | None:
    """Вибирає найкращий TF для сесійних екстремумів.

    Переваги: 1m → 5m → tf_primary, але тільки якщо є валідні timestamp/high/low.
    Це підвищує точність (більше granular, менше пропусків).
    """
    preferred = ["1m", "5m", str(tf_primary)]
    for tf in preferred:
        frame = ohlc_by_tf.get(tf)
        if frame is None or frame.empty:
            continue
        if {"timestamp", "high", "low"}.issubset(frame.columns):
            ts = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
            if not ts.dropna().empty:
                return frame, str(tf)

    # fallback: будь-який валідний фрейм (на випадок нетипових TF)
    for tf, frame in (ohlc_by_tf or {}).items():
        if frame is None or frame.empty:
            continue
        if {"timestamp", "high", "low"}.issubset(frame.columns):
            ts = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
            if not ts.dropna().empty:
                return frame, str(tf)
    return None


def _build_sessions_context(
    *, ohlc_by_tf: dict[str, pd.DataFrame], tf_primary: str
) -> dict[str, Any]:
    """Будує власний контекст торгових сесій (Asia/London/NY) на базі OHLCV.

    Правила (UTC, без overlap):
    - ASIA: 22:00–07:00 (перетинає добу)
    - LONDON: 07:00–13:00
    - NY: 13:00–22:00

    Повертає стабільні ключі:
    - session_tag: str (ASIA/LONDON/NY)
    - smc_session_tag, smc_session_start_ms, smc_session_end_ms, smc_session_high, smc_session_low
    - smc_sessions: dict з трьома сесіями (start/end/high/low/bars/is_active/tf)
    """
    picked = _pick_session_frame(ohlc_by_tf=ohlc_by_tf, tf_primary=tf_primary)
    if picked is None:
        return {}
    frame, tf_used = picked
    if frame.empty:
        return {}
    if not {"timestamp", "high", "low"}.issubset(frame.columns):
        return {}

    ts = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce")
    ts_valid = ts.dropna()
    if ts_valid.empty:
        return {}
    last_ts = ts_valid.iloc[-1]
    if not isinstance(last_ts, pd.Timestamp) or last_ts.tzinfo is None:
        try:
            last_ts = pd.Timestamp(last_ts, tz="UTC")
        except Exception:
            return {}

    # Визначаємо trading_day так, щоб ASIA 22:00–07:00 коректно покривав
    # години 22:00..23:59 та 00:00..06:59 (shift під 22:00).
    if int(last_ts.hour) >= 22:
        trading_day = (last_ts + pd.Timedelta(hours=2)).normalize()
    else:
        trading_day = last_ts.normalize()

    # Вікна сесій відносно trading_day (00:00 UTC).
    asia_start = trading_day - pd.Timedelta(hours=2)  # 22:00 prev/this day
    london_start = trading_day + pd.Timedelta(hours=7)  # 07:00
    ny_start = trading_day + pd.Timedelta(hours=13)  # 13:00
    ny_end = trading_day + pd.Timedelta(hours=22)  # 22:00
    asia_end = london_start
    london_end = ny_start

    if last_ts < london_start:
        session_tag = "ASIA"
    elif last_ts < ny_start:
        session_tag = "LONDON"
    else:
        session_tag = "NY"

    sessions: dict[str, Any] = {}
    sessions["ASIA"] = _calc_session_hilo(
        frame=frame,
        ts=ts,
        tag="ASIA",
        start=asia_start,
        end=asia_end,
        last_ts=last_ts,
        tf_used=tf_used,
    )
    sessions["LONDON"] = _calc_session_hilo(
        frame=frame,
        ts=ts,
        tag="LONDON",
        start=london_start,
        end=london_end,
        last_ts=last_ts,
        tf_used=tf_used,
    )
    sessions["NY"] = _calc_session_hilo(
        frame=frame,
        ts=ts,
        tag="NY",
        start=ny_start,
        end=ny_end,
        last_ts=last_ts,
        tf_used=tf_used,
    )

    active = sessions.get(session_tag)
    out: dict[str, Any] = {
        "session_tag": session_tag,
        "smc_session_tag": session_tag,
        "smc_sessions": sessions,
        "smc_session_tf": (
            str(active.get("tf")) if isinstance(active, dict) else tf_used
        ),
    }
    if isinstance(active, dict):
        out.update(
            {
                "smc_session_start_ms": active.get("start_ms"),
                "smc_session_end_ms": active.get("end_ms"),
                "smc_session_high": active.get("high"),
                "smc_session_low": active.get("low"),
            }
        )
    return {k: v for k, v in out.items() if v is not None}


def _calc_session_hilo(
    *,
    frame: pd.DataFrame,
    ts: pd.Series,
    tag: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
    last_ts: pd.Timestamp,
    tf_used: str,
) -> dict[str, Any]:
    """Рахує high/low в межах сесії, але не виходить за last_ts."""

    start_utc = pd.Timestamp(start, tz="UTC") if start.tzinfo is None else start
    end_utc = pd.Timestamp(end, tz="UTC") if end.tzinfo is None else end
    last_utc = pd.Timestamp(last_ts, tz="UTC") if last_ts.tzinfo is None else last_ts

    mask = (ts >= start_utc) & (ts < end_utc) & (ts <= last_utc)
    bars = int(mask.sum()) if hasattr(mask, "sum") else 0

    high_val = None
    low_val = None
    if bars > 0:
        try:
            high_series = pd.to_numeric(frame.loc[mask, "high"], errors="coerce")
            low_series = pd.to_numeric(frame.loc[mask, "low"], errors="coerce")
            if not high_series.dropna().empty:
                high_val = float(high_series.max())
            if not low_series.dropna().empty:
                low_val = float(low_series.min())
        except Exception:
            high_val = None
            low_val = None

    range_val = None
    mid_val = None
    if high_val is not None and low_val is not None:
        try:
            range_val = float(high_val) - float(low_val)
            mid_val = (float(high_val) + float(low_val)) / 2.0
        except Exception:
            range_val = None
            mid_val = None

    is_active = bool(start_utc <= last_utc < end_utc)
    return {
        "tag": tag,
        "start_ms": int(start_utc.value // 1_000_000),
        "end_ms": int(end_utc.value // 1_000_000),
        "high": high_val,
        "low": low_val,
        "range": range_val,
        "mid": mid_val,
        "bars": bars,
        "is_active": is_active,
        "tf": str(tf_used),
    }
