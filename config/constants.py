"""Константи (K_* ключі, статуси, загальні мапи).

Мета:
- Зібрати канонічні ключі та допоміжні константи в одному місці.
- Полегшити навігацію і ревʼю. Бізнес-логіки немає.
- Усі значення реекспортуються через __all__.

Зауваження:
- Значення дублюють налаштування з ``config/config.py``.
- Модуль НЕ імпортує нічого з ``config/config.py``, щоб уникнути циклів.
"""

from __future__ import annotations

# Канонічні стани активу (UI/state machine)
ASSET_STATE = {
    "INIT": "init",
    "ALERT": "alert",
    "NORMAL": "normal",
    "NO_TRADE": "no_trade",
    "NO_DATA": "no_data",
    "SYNCING": "syncing",
    "ERROR": "error",
}

# Узгоджені ключі Stage1→UI
K_SYMBOL: str = "symbol"
K_SIGNAL: str = "signal"
K_TRIGGER_REASONS: str = "trigger_reasons"
K_RAW_TRIGGER_REASONS: str = "raw_trigger_reasons"
K_STATS: str = "stats"
K_THRESHOLDS: str = "thresholds"

# Розширені поля сигналів (метадані рекомендації)
K_MARKET_CONTEXT: str = "market_context"
K_RECOMMENDATION: str = "recommendation"
K_CONFIDENCE_METRICS: str = "confidence_metrics"
K_ANOMALY_DETECTION: str = "anomaly_detection"
K_RISK_PARAMETERS: str = "risk_parameters"

# Directional метрики
K_DIRECTIONAL_VOLUME_RATIO: str = "directional_volume_ratio"
K_CUMULATIVE_DELTA: str = "cumulative_delta"
K_PRICE_SLOPE_ATR: str = "price_slope_atr"

# Confidence extras
K_DIRECTIONAL_CONFLICT: str = "directional_conflict"
K_DIRECTIONAL_CONFLICT_STRENGTH: str = "directional_conflict_strength"

# Діагностичні/допоміжні ключі
K_ATR_VS_LOW_GATE_RATIO: str = "atr_vs_low_gate_ratio"
K_LOW_VOLATILITY_FLAG: str = "low_volatility_flag"

__all__ = [
    # Стани
    "ASSET_STATE",
    # Ключі
    "K_SYMBOL",
    "K_SIGNAL",
    "K_TRIGGER_REASONS",
    "K_RAW_TRIGGER_REASONS",
    "K_STATS",
    "K_THRESHOLDS",
    "K_MARKET_CONTEXT",
    "K_RECOMMENDATION",
    "K_CONFIDENCE_METRICS",
    "K_ANOMALY_DETECTION",
    "K_RISK_PARAMETERS",
    "K_DIRECTIONAL_VOLUME_RATIO",
    "K_CUMULATIVE_DELTA",
    "K_PRICE_SLOPE_ATR",
    "K_DIRECTIONAL_CONFLICT",
    "K_DIRECTIONAL_CONFLICT_STRENGTH",
    "K_ATR_VS_LOW_GATE_RATIO",
    "K_LOW_VOLATILITY_FLAG",
]
