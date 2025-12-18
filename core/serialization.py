"""SSOT для серіалізації (JSON) та часу.

Мета: дати консервативний, сумісний зі stdlib `json` набір функцій,
щоб зменшувати дублювання `json.dumps/json.loads`, ISO-форматування часу
та ручні `default=str` по всьому репо.

Принципи:
- без "магії" та прихованих перетворень;
- максимально сумісно зі stdlib `json` (allow_nan=True, без жорстких заборон);
- fallback у `str(obj)` тільки коли інакше не можна.
"""

from __future__ import annotations

# ── Imports ───────────────────────────────────────────────────────────────
import json
import math
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

# ── Time ──────────────────────────────────────────────────────────────────


def utc_now_ms() -> int:
    """Повертає поточний UTC timestamp у мілісекундах."""

    return int(datetime.now(tz=UTC).timestamp() * 1000)


def utc_now_iso_z() -> str:
    """Повертає поточний UTC час у RFC3339 рядку з суфіксом `Z`.

    Це SSOT-хелпер для випадків, коли у payload потрібен саме строковий
    UTC timestamp у форматі `...Z`.
    """

    return dt_to_iso_z(datetime.now(tz=UTC))


def dt_to_iso_z(dt: datetime) -> str:
    """Конвертує datetime у RFC3339 рядок із суфіксом `Z` (UTC).

    - Якщо `dt` naive (tzinfo=None), трактуємо як UTC (консервативно).
    - Якщо `dt` має tzinfo, переводимо у UTC.
    """

    if dt.tzinfo is None:
        dt_utc = dt.replace(tzinfo=UTC)
    else:
        dt_utc = dt.astimezone(UTC)

    # Важливо: залишаємо стандартну поведінку isoformat() без примусової
    # зміни точності (мікросекунди/секунди), щоб не ламати очікування.
    return dt_utc.isoformat().replace("+00:00", "Z")


def utc_ms_to_iso_offset(millis: int) -> str:
    """Конвертує UTC timestamp (ms) у ISO рядок з `+00:00`.

    Це SSOT-хелпер для "людського" формату часу у UI, де історично
    використовувався `datetime(..., tz=UTC).isoformat()` (тобто саме `+00:00`,
    а не суфікс `Z`).

    Важливо:
    - `millis` очікується як int (мілісекунди).
    - точність зберігаємо на рівні мілісекунд (microsecond кратний 1000).
    """

    seconds, remainder = divmod(int(millis), 1000)
    dt = datetime.fromtimestamp(seconds, tz=UTC)
    dt = dt.replace(microsecond=remainder * 1000)
    return dt.isoformat()


def utc_seconds_to_iso_offset(seconds: float) -> str:
    """Конвертує UTC timestamp (секунди) у ISO рядок з `+00:00`.

    Це SSOT для UI-відображення, де потрібен стандартний `datetime.isoformat()`
    саме з offset `+00:00`.
    """

    dt = datetime.fromtimestamp(float(seconds), tz=UTC)
    return dt.isoformat()


def utc_ms_to_iso_z(ts_ms: int) -> str:
    """Конвертує UTC timestamp (мс) у RFC3339 рядок з суфіксом `Z`."""

    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC)
    return dt_to_iso_z(dt)


def utc_seconds_to_human_utc(seconds: float) -> str:
    """Конвертує UTC timestamp (секунди) у людиночитний формат без `Z`.

    Формат: `YYYY-MM-DD HH:MM:SS`.

    Важливо:
    - це НЕ RFC3339 (навмисно немає `T` і немає суфікса `Z`);
    - тримаємо як SSOT для UI/логів, щоб не дублювати ручні `strftime(...)`.
    """

    dt = datetime.fromtimestamp(float(seconds), tz=UTC)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def utc_seconds_to_human_utc_z(seconds: float) -> str:
    """Compat-аліас: історично функція мала суфікс `_z`.

    Тепер SSOT повертає формат БЕЗ `Z` (див. `utc_seconds_to_human_utc`).
    """

    return utc_seconds_to_human_utc(seconds)


def utc_ms_to_human_utc(millis: int) -> str:
    """Конвертує UTC timestamp (мс) у людиночитний формат без `Z`.

    Формат: `YYYY-MM-DD HH:MM:SS`.
    """

    return utc_seconds_to_human_utc(int(millis) / 1000.0)


def utc_ms_to_human_utc_z(millis: int) -> str:
    """Compat-аліас: історично функція мала суфікс `_z`.

    Тепер SSOT повертає формат БЕЗ `Z` (див. `utc_ms_to_human_utc`).
    """

    return utc_ms_to_human_utc(millis)


def utc_now_human_utc() -> str:
    """Повертає поточний UTC час у форматі `YYYY-MM-DD HH:MM:SS` (без `Z`)."""

    return utc_seconds_to_human_utc(datetime.now(tz=UTC).timestamp())


def try_iso_to_human_utc(value: str) -> str | None:
    """Пробує перетворити ISO/RFC3339 рядок у `YYYY-MM-DD HH:MM:SS` (без `Z`).

    Підтримує вхідні варіанти:
    - суфікс `Z` (RFC3339);
    - offset `+00:00`;
    - naive ISO (трактується як UTC).

    Якщо рядок не парситься — повертає None.
    """

    text = (value or "").strip()
    if not text:
        return None
    try:
        dt = iso_z_to_dt(text)
    except Exception:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def duration_seconds_to_hms(seconds: float) -> str:
    """Форматує тривалість у `DD HH:MM:SS` або `HH:MM:SS`.

    Правила:
    - негативні/NaN/inf → `-`;
    - якщо >= 1 доби → `DD HH:MM:SS` (DD мінімум 2 цифри);
    - інакше → `HH:MM:SS`.
    """

    if seconds is None:  # type: ignore[truthy-bool]
        return "-"
    try:
        seconds_f = float(seconds)
    except (TypeError, ValueError):
        return "-"
    if not math.isfinite(seconds_f) or seconds_f < 0:
        return "-"

    total = int(seconds_f)
    days, rem = divmod(total, 86_400)
    hours, rem = divmod(rem, 3_600)
    minutes, secs = divmod(rem, 60)
    if days > 0:
        return f"{days:02d} {hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def duration_ms_to_hms(ms: int | float) -> str:
    """Форматує тривалість у мс у `DD HH:MM:SS` або `HH:MM:SS`.

    Використовує підлогу до секунд (без округлення), щоб формат був стабільний.
    """

    millis = safe_int(ms)
    if millis is None:
        return "-"
    if millis < 0:
        return "-"
    return duration_seconds_to_hms(millis / 1000.0)


def safe_int(value: Any) -> int | None:
    """Безпечно приводить значення до int.

    Повертає None, якщо `value` не конвертується.
    """

    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_float(value: Any, *, finite: bool = False) -> float | None:
    """Безпечно приводить значення до float.

    Якщо `finite=True`, відкидає NaN/inf.
    """

    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if finite and not math.isfinite(result):
        return None
    return result


def coerce_dict(payload: Any) -> dict[str, Any]:
    """Повертає payload як dict або порожній dict.

    Це SSOT для типового патерну `payload if isinstance(payload, dict) else {}`.
    """

    return payload if isinstance(payload, dict) else {}


def iso_z_to_dt(value: str) -> datetime:
    """Парсить RFC3339 рядок у datetime (UTC).

    Приймає як суфікс `Z`, так і `+00:00`.
    Якщо tzinfo відсутній, трактуємо як UTC.
    """

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


# ── JSON-friendly conversion ──────────────────────────────────────────────


def to_jsonable(obj: Any) -> Any:
    """Конвертує об'єкт у JSON-friendly значення (консервативно).

    Підтримка (мінімальний SSOT):
    - datetime -> RFC3339 з `Z` (UTC)
    - date -> ISO YYYY-MM-DD
    - Decimal -> str (щоб уникнути втрати точності)
    - Enum -> name
    - Path -> str
    - dataclass -> dict (через asdict) + рекурсія

    Інше:
    - колекції/словники обробляються рекурсивно;
    - якщо об'єкт має `isoformat()`, пробуємо його;
    - крайній fallback: `str(obj)`.
    """

    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj

    if isinstance(obj, datetime):
        return dt_to_iso_z(obj)

    if isinstance(obj, date):
        return obj.isoformat()

    if isinstance(obj, Decimal):
        return str(obj)

    if isinstance(obj, Enum):
        return obj.name

    if isinstance(obj, Path):
        return str(obj)

    if is_dataclass(obj) and not isinstance(obj, type):
        return to_jsonable(asdict(obj))

    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}

    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]

    isoformat_fn = getattr(obj, "isoformat", None)
    if callable(isoformat_fn):
        try:
            return isoformat_fn()
        except Exception:
            # Якщо isoformat() "ламкий" — не блокуємо серіалізацію.
            return str(obj)

    return str(obj)


# ── JSON I/O ──────────────────────────────────────────────────────────────


def json_dumps(obj: Any, *, pretty: bool = False) -> str:
    """Серіалізує об'єкт у JSON-рядок (deterministic, UTF-8 friendly).

    - ensure_ascii=False: українські символи та нормальні строки без escape.
    - sort_keys=True: детермінований порядок ключів.
    - allow_nan=True: сумісно зі stdlib json (без різких заборон NaN/Infinity).
    - default=to_jsonable: мінімальні перетворення для складних типів.

    `pretty=True` додає індентацію (для debug/файлів), не для гарячого I/O.
    """

    if pretty:
        return json.dumps(
            obj,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=True,
            default=to_jsonable,
        )

    return json.dumps(
        obj,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=True,
        separators=(",", ":"),
        default=to_jsonable,
    )


def json_loads(data: str | bytes | bytearray) -> Any:
    """Десеріалізує JSON у Python-об'єкт.

    Підтримує як ``str``, так і ``bytes/bytearray`` (типово з Redis при
    ``decode_responses=False``). Для bytes використовуємо UTF-8 з
    ``errors='replace'`` (консервативно, без винятків на декодуванні).
    """

    if isinstance(data, (bytes, bytearray)):
        text = data.decode("utf-8", errors="replace")
    else:
        text = data
    return json.loads(text)
