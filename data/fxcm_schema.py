"""TypedDict-схеми та легка валідація для Redis-каналів FXCM.

Цей модуль фіксує мінімальний контракт повідомлень, які SMC-сторона читає з Redis:
- ``fxcm:ohlcv``: бари OHLCV (із опціональними complete/synthetic та microstructure полями);
- ``fxcm:price_tik``: тики bid/ask/mid з часовими мітками;
- ``fxcm:status``: агрегований статус конектора/ринку для гейтінгу інжесту.

Примітка:
- Валідація тут навмисно легка (без pydantic), щоб її можна було застосовувати у гарячому шляху.
- Додаткові поля в payload дозволені (forward-compatible) і просто ігноруються.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, TypedDict


class FxcmOhlcvBar(TypedDict):
    """Базовий OHLCV-бар.

    У потоці ``fxcm:ohlcv`` можуть бути додаткові ключі (microstructure),
    але для SMC критичні лише базові OHLCV поля.
    """

    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class FxcmOhlcvBarOptional(TypedDict, total=False):
    """Опціональні поля OHLCV-бару."""

    complete: bool
    synthetic: bool
    source: str


class FxcmOhlcvMessage(TypedDict):
    """Повідомлення з каналу ``fxcm:ohlcv``."""

    symbol: str
    tf: str
    bars: list[Mapping[str, Any]]


class FxcmOhlcvMessageOptional(TypedDict, total=False):
    sig: str


class FxcmPriceTickMessage(TypedDict):
    """Повідомлення з каналу ``fxcm:price_tik``."""

    symbol: str
    bid: float
    ask: float
    mid: float
    tick_ts: float
    snap_ts: float


class FxcmAggregatedStatusMessage(TypedDict, total=False):
    """Повідомлення з каналу ``fxcm:status`` (агрегований статус)."""

    ts: float
    process: str
    market: str
    price: str
    ohlcv: str
    note: str
    session: dict[str, Any]


def _coerce_json_object(raw: Any) -> Mapping[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        return raw
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", errors="replace")
    else:
        text = str(raw)
    text = text.strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
    except Exception:
        return None
    return obj if isinstance(obj, Mapping) else None


def validate_fxcm_ohlcv_message(raw: Any) -> FxcmOhlcvMessage | None:
    """Валідує мінімальний контракт ``fxcm:ohlcv``.

    Правила:
    - додаткові поля допускаються (forward-compatible);
    - відсутність ``complete`` / ``synthetic`` не є помилкою;
    - якщо конкретний бар має некоректну структуру/типи — пропускаємо лише його;
    - якщо після фільтрації не лишилось жодного валідного бару — повертаємо None.

    Повертає dict з ключами ``symbol/tf/bars`` або None.
    """

    obj = _coerce_json_object(raw)
    if obj is None:
        return None

    symbol = str(obj.get("symbol") or "").strip()
    tf = str(obj.get("tf") or obj.get("timeframe") or "").strip()
    bars = obj.get("bars")
    if not symbol or not tf or not isinstance(bars, list):
        return None

    def _coerce_int(value: Any) -> int | None:
        try:
            if value is None:
                return None
            if isinstance(value, bool):
                return None
            if isinstance(value, int):
                return int(value)
            if isinstance(value, float):
                if value != value:  # NaN
                    return None
                return int(value)
            text = str(value).strip()
            if not text:
                return None
            return int(float(text))
        except Exception:
            return None

    def _coerce_float(value: Any) -> float | None:
        try:
            if value is None:
                return None
            if isinstance(value, bool):
                return None
            if isinstance(value, (int, float)):
                num = float(value)
            else:
                text = str(value).strip()
                if not text:
                    return None
                num = float(text)
        except Exception:
            return None
        if num != num:  # NaN
            return None
        if num in (float("inf"), float("-inf")):
            return None
        return num

    safe_bars: list[Mapping[str, Any]] = []
    for bar in bars:
        if not isinstance(bar, Mapping):
            continue
        open_time = _coerce_int(bar.get("open_time"))
        close_time = _coerce_int(bar.get("close_time"))
        open_ = _coerce_float(bar.get("open"))
        high = _coerce_float(bar.get("high"))
        low = _coerce_float(bar.get("low"))
        close = _coerce_float(bar.get("close"))
        volume = _coerce_float(bar.get("volume"))
        if (
            open_time is None
            or close_time is None
            or open_ is None
            or high is None
            or low is None
            or close is None
            or volume is None
        ):
            continue

        normalized: dict[str, Any] = {
            "open_time": open_time,
            "close_time": close_time,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
        if "complete" in bar:
            normalized["complete"] = bool(bar.get("complete"))
        if "synthetic" in bar:
            normalized["synthetic"] = bool(bar.get("synthetic"))
        if "source" in bar:
            src = str(bar.get("source") or "").strip()
            if src:
                normalized["source"] = src

        safe_bars.append(normalized)
    if not safe_bars:
        return None

    return {"symbol": symbol, "tf": tf, "bars": safe_bars}


def validate_fxcm_price_tick_message(raw: Any) -> FxcmPriceTickMessage | None:
    """Валідує мінімальний контракт ``fxcm:price_tik``."""

    obj = _coerce_json_object(raw)
    if obj is None:
        return None

    symbol = str(obj.get("symbol") or "").strip()
    if not symbol:
        return None

    try:
        bid = float(obj["bid"])
        ask = float(obj["ask"])
        mid = float(obj["mid"])
        tick_ts = float(obj["tick_ts"])
        snap_ts = float(obj["snap_ts"])
    except Exception:
        return None

    return {
        "symbol": symbol,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "tick_ts": tick_ts,
        "snap_ts": snap_ts,
    }


def validate_fxcm_status_message(raw: Any) -> FxcmAggregatedStatusMessage | None:
    """Валідує мінімальний контракт ``fxcm:status``.

    Дозволяємо частково заповнені payload-и (forward-compatible).
    """

    obj = _coerce_json_object(raw)
    if obj is None:
        return None

    out: FxcmAggregatedStatusMessage = {}

    ts = obj.get("ts")
    if ts is not None:
        try:
            out["ts"] = float(ts)
        except Exception:
            return None

    for key in ("process", "market", "price", "ohlcv", "note"):
        val = obj.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if text:
            out[key] = text  # type: ignore[literal-required]

    session = obj.get("session")
    if session is not None:
        if isinstance(session, Mapping):
            out["session"] = dict(session)
        else:
            return None

    return out


__all__ = [
    "FxcmOhlcvBar",
    "FxcmOhlcvBarOptional",
    "FxcmOhlcvMessage",
    "FxcmOhlcvMessageOptional",
    "FxcmPriceTickMessage",
    "FxcmAggregatedStatusMessage",
    "validate_fxcm_ohlcv_message",
    "validate_fxcm_price_tick_message",
    "validate_fxcm_status_message",
]
