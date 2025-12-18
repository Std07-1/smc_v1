"""SSOT для форматування чисел/часу в UI та логах.

Цей модуль НЕ містить бізнес-логіки (тик-сайзи, символи, FX/crypto правила).
Лише універсальні форматтери для читабельних рядків.

Принципи:
- однаковий формат у всіх місцях;
- контрольоване округлення (явні `digits`);
- без прив'язки до домену/стратегії.
"""

from __future__ import annotations

# ── Imports ───────────────────────────────────────────────────────────────
from decimal import Decimal
from typing import Final

from core.serialization import utc_ms_to_iso_z

# ── Helpers ───────────────────────────────────────────────────────────────

_DEFAULT_FLOAT_DIGITS: Final[int] = 10


def _to_decimal(value: float | Decimal) -> Decimal:
    # Використовуємо Decimal для стабільнішого форматування без scientific.
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _strip_trailing_zeros(text: str) -> str:
    if "." not in text:
        return text
    return text.rstrip("0").rstrip(".")


# ── Numbers ───────────────────────────────────────────────────────────────


def fmt_price(value: float | Decimal, *, digits: int | None = None) -> str:
    """Форматує ціну без доменних правил.

    Якщо `digits` не задано, повертає компактний десятковий рядок без
    наукової нотації та без зайвих нулів.
    """

    dec = _to_decimal(value)
    if digits is None:
        # Консервативно: обмежуємо кількість знаків, потім прибираємо нулі.
        text = f"{dec:.{_DEFAULT_FLOAT_DIGITS}f}"
        return _strip_trailing_zeros(text)
    return f"{dec:.{digits}f}"


def fmt_price_stage1(price: float, symbol: str) -> str:
    """Compat-форматування ціни для UI (історично зі Stage1), з комою.

    Важливо:
    - це presentation-формат (UX), не бізнес-логіка;
    - не визначає tick_size і не робить "auto-fix" по свічках;
    - поведінка сумісна з legacy `utils.utils.format_price`.
    """

    _ = symbol  # legacy сигнатура: символ потрібен для майбутніх евристик
    try:
        p = float(price)
    except (TypeError, ValueError):
        return "-"
    abs_p = abs(p)
    if abs_p >= 1:
        decimals = 2
    elif abs_p >= 0.01:
        decimals = 4
    else:
        decimals = 6
    formatted = f"{p:.{decimals}f}"
    return formatted.replace(",", "").replace(".", ",")


def fmt_qty(value: float | Decimal, *, digits: int | None = None) -> str:
    """Форматує кількість/обсяг (quantity) без доменних правил."""

    dec = _to_decimal(value)
    if digits is None:
        text = f"{dec:.{_DEFAULT_FLOAT_DIGITS}f}"
        return _strip_trailing_zeros(text)
    return f"{dec:.{digits}f}"


def fmt_pct(value: float, *, digits: int = 2) -> str:
    """Форматує частку (ratio) у відсотки.

    Очікування: `value` — це частка (наприклад 0.1234), на виході 12.34%.
    """

    return f"{value * 100.0:.{digits}f}%"


def fmt_volume_usd(volume: float | str) -> str:
    """Форматує оборот у USD (K/M/G/T).

    Механічно перенесено з `utils/utils.py` (D1) для канонізації форматування.
    """

    if isinstance(volume, str):
        return volume
    try:
        v = float(volume)
    except (TypeError, ValueError):
        return "-"
    if v >= 1e12:
        return f"{v / 1e12:.2f}T USD"
    if v >= 1e9:
        return f"{v / 1e9:.2f}G USD"
    if v >= 1e6:
        return f"{v / 1e6:.2f}M USD"
    if v >= 1e3:
        return f"{v / 1e3:.2f}K USD"
    return f"{v:.2f} USD"


def fmt_open_interest(oi: float | str) -> str:
    """Форматує Open Interest у коротку форму (K/M/B).

    Механічно перенесено з `utils/utils.py` (D1) для канонізації форматування.
    """

    try:
        val = float(oi)
    except (ValueError, TypeError):
        return "-"
    if val >= 1e9:
        return f"{val / 1e9:.2f}B"
    if val >= 1e6:
        return f"{val / 1e6:.2f}M"
    if val >= 1e3:
        return f"{val / 1e3:.2f}K"
    return f"{val:.2f} USD"


# ── Time ──────────────────────────────────────────────────────────────────


def fmt_ms(ms: int) -> str:
    """Форматує тривалість у мс у читабельний вигляд."""

    if ms < 0:
        return "-"

    if ms < 1000:
        return f"{ms}ms"

    total_seconds = ms / 1000.0
    if total_seconds < 60.0:
        return f"{total_seconds:.2f}s"

    minutes = int(total_seconds // 60)
    seconds = int(round(total_seconds - minutes * 60))
    if seconds == 60:
        minutes += 1
        seconds = 0
    return f"{minutes}m{seconds:02d}s"


def fmt_ts_ms(ts_ms: int) -> str:
    """Форматує UTC timestamp (мс) у RFC3339 `...Z` рядок."""

    return utc_ms_to_iso_z(ts_ms)
