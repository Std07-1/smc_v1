"""S2: діагностика історії в UnifiedDataStore (insufficient/stale_tail).

Мета цього модуля:
- уніфікувати перевірку, чи можна працювати з даними по (symbol, tf);
- відокремити pure-логіку класифікації від воркерів (SMC/S3 requester).

Тут немає команд до конектора — лише локальні критерії S2.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

from data.unified_store import UnifiedDataStore


@dataclass(frozen=True, slots=True)
class FxcmHistoryState:
    """Pure-результат класифікації історії для (symbol, tf).

    Пороги (S2):
    - insufficient_history: bars_count < min_history_bars
    - stale_tail: now_ms - last_open_time_ms > stale_k * tf_ms
    """

    state: str  # ok|insufficient|stale_tail|unknown
    needs_warmup: bool
    needs_backfill: bool
    age_ms: int | None


@dataclass(frozen=True, slots=True)
class HistoryStatus:
    """Результат S2-перевірки історії для (symbol, tf)."""

    symbol: str
    timeframe: str
    bars_count: int
    last_open_time_ms: int | None
    age_ms: int | None
    state: str  # ok|insufficient|stale_tail|unknown
    needs_warmup: bool
    needs_backfill: bool
    gaps_count: int
    max_gap_ms: int | None
    non_monotonic_count: int


def _series_epoch_to_ms(values: list[Any]) -> list[int]:
    """Нормалізує список epoch-значень у мілісекунди.

    Оптимізовано під короткі tail-вікна (типово 300). Якщо значення вже в мс,
    залишаємо як є; якщо у секундах — множимо на 1000.
    """

    out: list[int] = []
    for v in values:
        ms = _epoch_to_ms(v)
        if ms is None:
            continue
        out.append(int(ms))
    return out


def timeframe_to_ms(timeframe: str) -> int | None:
    """Парсить таймфрейм у мілісекунди (1m/5m/15m/1h/4h/1d).

    Повертає None для невідомих форматів.
    """

    tf = (timeframe or "").strip().lower()
    if not tf:
        return None

    # Найпростіший парсер під узгоджені значення.
    unit = tf[-1]
    value_raw = tf[:-1]
    try:
        value = int(value_raw)
    except ValueError:
        return None
    if value <= 0:
        return None

    if unit == "m":
        return value * 60_000
    if unit == "h":
        return value * 3_600_000
    if unit == "d":
        return value * 86_400_000

    return None


def _epoch_to_ms(value: Any) -> int | None:
    """Нормалізує epoch (seconds/ms/datetime/ISO) в мілісекунди."""

    if value is None:
        return None

    if isinstance(value, (int, float)):
        num = float(value)
        if not math.isfinite(num):
            return None
        # Heuristic: якщо вже схоже на ms.
        if num > 1e12:
            return int(num)
        return int(num * 1000.0)

    if isinstance(value, pd.Timestamp):
        return int(value.timestamp() * 1000.0)

    if isinstance(value, datetime):
        return int(value.timestamp() * 1000.0)

    if isinstance(value, str) and value.strip():
        txt = value.strip()
        # ISO
        try:
            dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000.0)
        except ValueError:
            pass
        # numeric
        try:
            return _epoch_to_ms(float(txt))
        except ValueError:
            return None

    return None


def classify_history(
    now_ms: int,
    bars_count: int,
    last_open_time_ms: int | None,
    min_history_bars: int,
    tf_ms: int,
    stale_k: float,
) -> FxcmHistoryState:
    """Класифікує стан історії (S2) без I/O.

    Пороги (S2):
    - insufficient_history: bars_count < min_history_bars
    - stale_tail: now_ms - last_open_time_ms > stale_k * tf_ms

    Якщо last_open_time_ms невідомий, але bars_count >= min_history_bars — повертаємо state=unknown.
    """

    safe_bars = max(0, int(bars_count))
    min_bars = max(1, int(min_history_bars))
    tf_ms_safe = max(1, int(tf_ms))

    if safe_bars < min_bars:
        return FxcmHistoryState(
            state="insufficient",
            needs_warmup=True,
            needs_backfill=False,
            age_ms=None,
        )

    if last_open_time_ms is None:
        return FxcmHistoryState(
            state="unknown",
            needs_warmup=False,
            needs_backfill=False,
            age_ms=None,
        )

    age_ms = max(0, int(int(now_ms) - int(last_open_time_ms)))
    threshold_ms = int(float(stale_k) * float(tf_ms_safe))
    if age_ms > threshold_ms:
        return FxcmHistoryState(
            state="stale_tail",
            needs_warmup=False,
            needs_backfill=True,
            age_ms=age_ms,
        )

    return FxcmHistoryState(
        state="ok",
        needs_warmup=False,
        needs_backfill=False,
        age_ms=age_ms,
    )


def classify_history_status(
    *,
    bars_count: int,
    last_open_ms: int | None,
    now_ms: int,
    min_history_bars: int,
    tf_ms: int,
    stale_k: float = 3.0,
) -> tuple[str, bool, bool, int | None]:
    """Legacy wrapper для старих місць.

    Повертає tuple: (state, needs_warmup, needs_backfill, age_ms).
    Новий код має використовувати `classify_history()`.
    """

    out = classify_history(
        now_ms=int(now_ms),
        bars_count=int(bars_count),
        last_open_time_ms=last_open_ms,
        min_history_bars=int(min_history_bars),
        tf_ms=int(tf_ms),
        stale_k=float(stale_k),
    )
    return out.state, out.needs_warmup, out.needs_backfill, out.age_ms


async def compute_history_status(
    *,
    store: UnifiedDataStore,
    symbol: str,
    timeframe: str,
    min_history_bars: int,
    stale_k: float = 3.0,
    now_ms: int | None = None,
) -> HistoryStatus:
    """Зчитує tail з UDS та повертає HistoryStatus для (symbol, tf)."""

    sym = (symbol or "").strip().lower()
    tf = (timeframe or "").strip().lower()
    now_ms_val = int(now_ms if now_ms is not None else time.time() * 1000.0)

    tf_ms = timeframe_to_ms(tf) or 60_000

    # Важливо: get_df(limit=N) повертає останні N барів (якщо вони є).
    # Нам достатньо знати "чи є >=min_history_bars" та last_open_time.
    limit = max(1, int(min_history_bars))
    df = await store.get_df(sym, tf, limit=limit)
    bars_count = int(len(df)) if df is not None else 0

    last_open_time_ms = None
    if df is not None and not df.empty:
        row = df.iloc[-1]
        last_open_time_ms = _epoch_to_ms(row.get("open_time") or row.get("close_time"))

    gaps_count = 0
    max_gap_ms: int | None = None
    non_monotonic_count = 0
    if (
        df is not None
        and not df.empty
        and "open_time" in df.columns
        and bars_count >= 2
    ):
        try:
            # Працюємо по tail-вікну, яке вже обмежене limit=min_history_bars.
            tail_values = list(df["open_time"].tolist())
            open_times_ms = _series_epoch_to_ms(tail_values)
            if len(open_times_ms) >= 2:
                # Вважаємо gap-ом будь-який крок, що суттєво більший за tf.
                # 1.5x — щоб ігнорувати дрібні дрейфи/неточності.
                gap_threshold = int(float(tf_ms) * 1.5)
                prev = int(open_times_ms[0])
                for cur in open_times_ms[1:]:
                    cur_i = int(cur)
                    delta = cur_i - prev

                    # "Бар позаду" / не-монотонність: лише коли час іде назад.
                    # Дублікати (delta==0) тут не рахуємо, щоб не фолс-позитивити
                    # на тестових/штучних серіях або при повторній видачі одного бару.
                    if delta < 0:
                        non_monotonic_count += 1
                    # Gap рахуємо лише для позитивних кроків.
                    elif delta > gap_threshold:
                        gaps_count += 1
                        if max_gap_ms is None or delta > max_gap_ms:
                            max_gap_ms = int(delta)

                    prev = cur_i
        except Exception:
            # S2 — діагностика, не повинна ламати пайплайн.
            gaps_count = 0
            max_gap_ms = None
            non_monotonic_count = 0

    s2 = classify_history(
        now_ms=now_ms_val,
        bars_count=bars_count,
        last_open_time_ms=last_open_time_ms,
        min_history_bars=min_history_bars,
        tf_ms=tf_ms,
        stale_k=stale_k,
    )

    state = s2.state
    needs_warmup = s2.needs_warmup
    needs_backfill = s2.needs_backfill

    # Якщо хвіст свіжий, але tail має не-монотонність або пропуски — це теж проблема.
    # Пріоритети: insufficient/stale_tail > non_monotonic_tail > gappy_tail.
    if state == "ok" and int(non_monotonic_count) > 0:
        state = "non_monotonic_tail"
        needs_warmup = False
        needs_backfill = True
    elif state == "ok" and int(gaps_count) > 0:
        state = "gappy_tail"
        needs_warmup = False
        needs_backfill = True

    return HistoryStatus(
        symbol=sym,
        timeframe=tf,
        bars_count=bars_count,
        last_open_time_ms=last_open_time_ms,
        age_ms=s2.age_ms,
        state=state,
        needs_warmup=needs_warmup,
        needs_backfill=needs_backfill,
        gaps_count=int(gaps_count),
        max_gap_ms=max_gap_ms,
        non_monotonic_count=int(non_monotonic_count),
    )


__all__ = [
    "FxcmHistoryState",
    "HistoryStatus",
    "timeframe_to_ms",
    "classify_history",
    "classify_history_status",
    "compute_history_status",
]
