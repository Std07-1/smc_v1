"""Часові утиліти Levels‑V1 (Крок 3.2.2a).

Мета:
- 1 раз формально визначити «торговий день» для DAILY кандидатів (PDH/PDL/EDH/EDL),
  щоб рівні були детерміновані та без «магії».

Визначення:
- day_start_hour_utc → вікно дня: [D@start, D+1@start) в UTC.

Одиниці:
- `ts` — Unix timestamp у секундах (float/int).
- Повертаємо (start_ts, end_ts) також у секундах.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta


def _normalize_day_start_hour(day_start_hour_utc: int) -> int:
    # Дозволяємо будь-яке int, нормалізуємо у 0..23
    try:
        h = int(day_start_hour_utc)
    except (TypeError, ValueError):
        h = 0
    return h % 24


def get_day_window_utc(ts: float, *, day_start_hour_utc: int) -> tuple[float, float]:
    """Повертає (start_ts, end_ts) «торгового дня», що містить `ts`.

    День визначається як [D@start, D+1@start) у UTC.
    """

    h = _normalize_day_start_hour(day_start_hour_utc)
    dt = datetime.fromtimestamp(float(ts), tz=UTC)

    # Зсуваємо час так, щоб «початок дня» відповідав 00:00, тоді беремо floor(date).
    shifted = dt - timedelta(hours=h)
    shifted_start = shifted.replace(hour=0, minute=0, second=0, microsecond=0)

    start = shifted_start + timedelta(hours=h)
    end = start + timedelta(days=1)

    # На всяк випадок стабілізуємо до цілих секунд.
    start_ts = float(int(start.timestamp()))
    end_ts = float(int(end.timestamp()))
    return start_ts, end_ts


def get_prev_day_window_utc(
    ts: float, *, day_start_hour_utc: int
) -> tuple[float, float]:
    """Повертає (start_ts, end_ts) попереднього «торгового дня» відносно `ts`."""

    start, _end = get_day_window_utc(ts, day_start_hour_utc=day_start_hour_utc)
    prev_end = start
    prev_start = prev_end - 24 * 60 * 60
    return float(prev_start), float(prev_end)


def _normalize_hour_utc(hour_utc: int) -> int:
    try:
        h = int(hour_utc)
    except (TypeError, ValueError):
        h = 0
    return h % 24


def _session_duration_hours(start_hour_utc: int, end_hour_utc: int) -> int:
    s = _normalize_hour_utc(start_hour_utc)
    e = _normalize_hour_utc(end_hour_utc)
    if e >= s:
        return e - s
    return (24 - s) + e


def get_session_window_utc(
    ts: float, *, session_start_hour_utc: int, session_end_hour_utc: int
) -> tuple[float, float]:
    """Повертає (start_ts, end_ts) вікна сесії (UTC), релевантного для `ts`.

    Важливе правило (для QA/кандидатів): ми повертаємо сесію, яка
    **останньою стартувала** відносно `ts` (тобто найближчий зліва start).

    Це дає очікувану поведінку:
    - якщо сесія ще не почалась сьогодні (напр. LONDON о 06:00), повернеться
      вікно вчорашньої LONDON;
    - якщо сесія активна, `ts` потрапляє у [start, end);
    - якщо сесія вже завершилась, повернеться завершене сьогоднішнє вікно.
    """

    s = _normalize_hour_utc(session_start_hour_utc)
    duration_h = _session_duration_hours(session_start_hour_utc, session_end_hour_utc)
    if duration_h <= 0:
        # Дегенеративний випадок (start==end) трактуємо як 24h.
        duration_h = 24

    dt = datetime.fromtimestamp(float(ts), tz=UTC)

    # Анкеримо «день сесії» через зсув на start_hour, аналогічно day-window.
    shifted = dt - timedelta(hours=s)
    shifted_start = shifted.replace(hour=0, minute=0, second=0, microsecond=0)

    start = shifted_start + timedelta(hours=s)
    end = start + timedelta(hours=duration_h)

    start_ts = float(int(start.timestamp()))
    end_ts = float(int(end.timestamp()))
    return start_ts, end_ts


def find_active_session_tag_utc(
    ts: float, *, session_windows_utc: Mapping[str, tuple[int, int]]
) -> str | None:
    """Повертає тег активної сесії (ASIA/LONDON/NY) для `ts`, або None.

    Вікна задаються в UTC як {TAG: (start_hour_utc, end_hour_utc)}.
    Межі інтервалу: [start, end) (end ексклюзивний).
    """

    t = float(ts)
    for tag, (start_h, end_h) in session_windows_utc.items():
        start_ts, end_ts = get_session_window_utc(
            t, session_start_hour_utc=start_h, session_end_hour_utc=end_h
        )
        if start_ts <= t < end_ts:
            return tag
    return None
