"""Контрактні визначення для Levels-V1 (підготовчий крок 3.1).

Цей модуль НЕ містить алгоритмів відбору (caps/distance/merge).
Тут лише:
- джерела рівнів (LevelSource),
- канонічні labels (whitelist),
- мапінг pool_type -> label для EQH/EQL (band),
- правило стабільного id з округленням до tick/decimals.

Примітка:
- За вимогою поточного scope Levels-V1 ми НЕ просуваємо TF=1m у `levels_v1`.
  1m лишається в shadow/as-is до cutover.
"""

from __future__ import annotations

from typing import Literal

# ── TF (Levels-V1) ─────────────────────────────────────────────────────────

# 1m навмисно НЕ включаємо (див. scope Кроку 3.1).
LevelTfV1 = Literal["5m", "1h", "4h"]


# ── Sources ────────────────────────────────────────────────────────────────

LevelSource = Literal[
    "DAILY",  # PDH/PDL, EDH/EDL
    "SESSION",  # ASH/ASL, LSH/LSL, NYH/NYL
    "RANGE",  # RANGE_H/RANGE_L
    "POOL_DERIVED",  # EQH/EQL як band з liquidity pools
]


# ── Labels (whitelist) ─────────────────────────────────────────────────────

LevelLabelLineV1 = Literal[
    "PDH",
    "PDL",
    "EDH",
    "EDL",
    "ASH",
    "ASL",
    "LSH",
    "LSL",
    "NYH",
    "NYL",
    "RANGE_H",
    "RANGE_L",
]

LevelLabelBandV1 = Literal["EQH", "EQL"]

LevelLabelV1 = LevelLabelLineV1 | LevelLabelBandV1

LevelKindV1 = Literal["line", "band"]

LEVEL_LABELS_LINE_V1: frozenset[str] = frozenset(
    {
        "PDH",
        "PDL",
        "EDH",
        "EDL",
        "ASH",
        "ASL",
        "LSH",
        "LSL",
        "NYH",
        "NYL",
        "RANGE_H",
        "RANGE_L",
    }
)

LEVEL_LABELS_BAND_V1: frozenset[str] = frozenset({"EQH", "EQL"})

LEVEL_LABELS_V1: frozenset[str] = frozenset(
    set(LEVEL_LABELS_LINE_V1) | set(LEVEL_LABELS_BAND_V1)
)


def normalize_pool_type_to_level_label_v1(pool_type: object) -> LevelLabelBandV1 | None:
    """Мапить `pool_type` у канонічний label рівня (тільки EQH/EQL bands).

    Все інше (WICK_CLUSTER, SLQ, RANGE_EXTREME, SESSION_*) не є levels_v1.
    """

    if pool_type is None:
        return None

    t = str(pool_type).strip().upper()
    if not t:
        return None

    # Часто буває суфікс _P (preview) або інші варіації.
    if t.endswith("_P"):
        t = t[:-2]

    if t.startswith("EQH"):
        return "EQH"
    if t.startswith("EQL"):
        return "EQL"

    return None


def is_allowed_level_label_v1(label: object) -> bool:
    if label is None:
        return False
    return str(label).strip().upper() in LEVEL_LABELS_V1


def _infer_default_decimals_for_symbol(symbol: str | None) -> int:
    """Best-effort fallback decimals, якщо tick_size невідомий.

    Ми тут не робимо "магії" — це лише стабілізація id.
    """

    sym = str(symbol or "").strip().upper()
    if sym in {"XAUUSD", "XAGUSD"}:
        return 2
    # FX majors (best-effort)
    return 5


def round_price_for_level_id(
    value: float,
    *,
    tick_size: float | None = None,
    symbol: str | None = None,
) -> float:
    """Округлює ціну для стабільного `id`.

    Пріоритет:
    1) tick_size (якщо відомий і валідний)
    2) fallback decimals по символу
    """

    v = float(value)
    if tick_size is not None:
        ts = float(tick_size)
        if ts > 0 and ts == ts:
            steps = round(v / ts)
            return float(steps) * ts

    decimals = _infer_default_decimals_for_symbol(symbol)
    return round(v, int(decimals))


def make_level_id_line_v1(
    *,
    tf: LevelTfV1,
    label: LevelLabelLineV1,
    price: float,
    tick_size: float | None = None,
    symbol: str | None = None,
) -> str:
    p = round_price_for_level_id(price, tick_size=tick_size, symbol=symbol)
    return f"lvl:{tf}:{label}:{p:.10f}".rstrip("0").rstrip(".")


def make_level_id_band_v1(
    *,
    tf: LevelTfV1,
    label: LevelLabelBandV1,
    bot: float,
    top: float,
    tick_size: float | None = None,
    symbol: str | None = None,
) -> str:
    b = round_price_for_level_id(bot, tick_size=tick_size, symbol=symbol)
    t = round_price_for_level_id(top, tick_size=tick_size, symbol=symbol)
    # Нормалізуємо порядок, щоб id не залежав від перестановки bot/top.
    lo = min(b, t)
    hi = max(b, t)
    a = f"{lo:.10f}".rstrip("0").rstrip(".")
    c = f"{hi:.10f}".rstrip("0").rstrip(".")
    return f"lvl:{tf}:{label}:{a}:{c}"
