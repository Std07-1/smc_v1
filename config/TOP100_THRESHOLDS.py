# ruff: noqa: N999
# Порогові значення підібрано з урахуванням типу активу та його волатильності.
# Мега-коригування (BTC, ETH) мають нижчі пороги (RSI ~70/30,
# невисокий vol_z) через стабільніші обсяги.
# Мем-коїни (DOGE, SHIB, PEPE, FLOKI тощо) отримали ширші межі (RSI ~80/20)
# і вищий vol_z_threshold, оскільки їх обсяги та ціни сильно коливаються.
# Активи середньої капіталізації налаштовано на середні значення
# (RSI ~75/25, vol_z ~2.5σ), а дрібні та схильні до маніпуляцій токени –
# на найбільші пороги для фільтрації шумів.


from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_top100_threshold(symbol: str) -> dict[str, Any] | None:
    """Повертає мапу дефолтних порогів для конкретного символу (Top100),
    сумісну з Thresholds.from_mapping.

    Примітки:
      - В цьому модулі конфіг має ключі типу vol_z_threshold, rsi_overbought/oversold,
        додатково atr_pct_min, vwap_deviation (які не обов'язкові для Thresholds).
      - Ми приводимо до очікуваних ключів: volume_z_threshold, rsi_*; додаємо базові
        low_gate/high_gate/atr_target, якщо не задані.
    """
    try:
        if not symbol or not isinstance(symbol, str):
            return None
        sym_l = symbol.lower()
        cfg = TOP100_THRESHOLDS.get(sym_l)
        if not cfg:
            return None
        # Базові загальні значення (можете скоригувати під актив при потребі)
        mapping = {
            "symbol": symbol.upper(),
            "low_gate": 0.0035,
            "high_gate": 0.012,
            "atr_target": 0.35,
        }
        for optional_key in ("low_gate", "high_gate", "atr_target"):
            if optional_key in cfg:
                mapping[optional_key] = cfg.get(optional_key)
        # Перенесення ключів у канонічний формат
        if "vol_z_threshold" in cfg:
            mapping["volume_z_threshold"] = cfg.get("vol_z_threshold")
        if "rsi_overbought" in cfg:
            mapping["rsi_overbought"] = cfg.get("rsi_overbought")
        if "rsi_oversold" in cfg:
            mapping["rsi_oversold"] = cfg.get("rsi_oversold")
        # Додаткові розширені ключі (пас-тру) для сучасної логіки порогів
        #  • atr_pct_min → min_atr_percent (бек-сов сумісна назва)
        #  • vwap_deviation, signal_thresholds, state_overrides, meta — без змін
        if "atr_pct_min" in cfg:
            mapping["min_atr_percent"] = cfg.get("atr_pct_min")
        if "vwap_deviation" in cfg:
            mapping["vwap_deviation"] = cfg.get("vwap_deviation")
        st = cfg.get("signal_thresholds")
        if isinstance(st, dict):
            mapping["signal_thresholds"] = st
            # Синхронізація: якщо верхній vwap_deviation не заданий, але є
            # signal_thresholds.vwap_deviation.threshold — підтягуємо його як базовий.
            try:
                if "vwap_deviation" not in mapping:
                    vwap_thr = st.get("vwap_deviation", {}).get("threshold")
                    if isinstance(vwap_thr, (int, float)):
                        mapping["vwap_deviation"] = float(vwap_thr)
            except Exception:
                # не критично: пропускаємо, логи нижче покажуть включені ключі
                pass
            # Аналогічно: якщо верхній volume_z_threshold відсутній —
            # візьмемо його зі signal_thresholds.volume_spike.z_score (як дефолт).
            try:
                if "volume_z_threshold" not in mapping:
                    z_thr = st.get("volume_spike", {}).get("z_score")
                    if isinstance(z_thr, (int, float)):
                        mapping["volume_z_threshold"] = float(z_thr)
            except Exception:
                pass
        so = cfg.get("state_overrides")
        if isinstance(so, dict):
            mapping["state_overrides"] = so
        meta = cfg.get("meta")
        if isinstance(meta, dict):
            mapping["meta"] = meta

        logger.debug(
            "get_top100_threshold: застосовано мапінг",
            extra={
                "symbol": symbol.upper(),
                "included_keys": sorted(list(mapping.keys())),
                "source_keys": sorted(list(cfg.keys())),
            },
        )

        return mapping
    except Exception:
        return None


# ───────────────────────────── TOP-10: розширені пороги ─────────────────────────────
# Логіка:
#  • mega-cap (BTC, ETH): нижчі vol_z / тісніші VWAP-пороги; суворіші ретести на breakout.
#  • high-beta (SOL, AVAX, LINK): середні vol_z, ширший ATR-band для breakout.
#  • noisy/meme (DOGE, частково XRP): підвищені vol_z / RSI; ширші VWAP-відхилення.
#  • нові/агресивні (TON): високі vol_z/ATR гейти, але допускаємо швидкі breakouts.
#
# Пояснення ключів:
#  • atr_pct_min — мінімальна волатильність (ATR% від ціни), нижче якої сигнали занижуються/ігноруються.
#  • vwap_deviation — базовий поріг відхилення від VWAP (частка від ціни).
#  • signal_thresholds.* — детальні пороги для Stage1 тригерів.
#  • state_overrides — мультиплікатори/дельти до порогів залежно від поточного стану (range, trend, high_vol).


TOP100_THRESHOLDS: dict[str, dict[str, Any]] = {
    "xauusd": {
        # --- Загальні параметри ---
        "vol_z_threshold": 2.0,  # сплеск обсягу ≥2.0σ, помірний, бо золото має стабільні обсяги
        "rsi_overbought": 70.0,
        "rsi_oversold": 30.0,
        "atr_pct_min": 0.0025,  # дозволяємо працювати в більш спокійному діапазоні (~0.25%)
        "vwap_deviation": 0.010,  # відхилення 1% від VWAP як сигнал
        "low_gate": 0.0035,
        "high_gate": 0.012,
        "atr_target": 0.35,
        # --- Параметри за типами сигналів ---
        "signal_thresholds": {
            "volume_spike": {
                "z_score": 2.0,  # трохи послаблено, щоб спрацьовувати у спокійні періоди
                "min_notional_usd": 5_000_000.0,  # мінімальний обсяг 5 млн USD
                "cooldown_bars": 2,  # мінімум 2 бари між сплесками
            },
            "rsi_trigger": {
                "overbought": 72.0,  # трохи суворіше для XAUUSD
                "oversold": 28.0,
                "divergence_strength": 1.2,  # помірна вимога до сили дивергенції
            },
            "breakout": {
                "band_pct_atr": 0.65,  # ближче до рівня, щоб не втрачати діапазонні пробої
                "min_retests": 2,  # мінімум 2 ретести підтверджень рівня
                "confirm_bars": 2,  # підтверджуючі бари для надійності
            },
            "vwap_deviation": {
                "threshold": 0.012,  # відхилення 1.2% від VWAP як сигнал
                "duration_bars": 3,
            },  # тривалість підтвердження в барах, зберігається хоча б 3 хвилини
            "atr_volatility": {
                "low_gate_pct": 0.25,  # 0.25% ATR/price - нижня межа волатильності для сигналів
                "high_gate_pct": 1.10,  # трохи нижче верхньої межі — швидше реагуємо
            },  # у % від ціни
        },
        # --- Специфічні налаштування для XAUUSD ---
        "state_overrides": {
            "range_bound": {
                "vwap_deviation": +0.002,  # трохи суворіше для range-bound, для уникнення шумів
                "signal_thresholds.breakout.band_pct_atr": +0.10,  # трохи ширше для breakout, для уникнення фальшивих спрацьовувань
            },
            "trend_strong": {
                "rsi_trigger.overbought": +2.0,  # трохи вищий поріг для тренду, для уникнення фальшивих спрацьовувань
                "rsi_trigger.oversold": -2.0,  # трохи нижчий поріг для тренду, для уникнення фальшивих спрацьовувань
            },
            "high_volatility": {
                "volume_spike.z_score": +0.2,  # трохи вищий поріг для шумних періодів
                "vwap_deviation": +0.002,
            },  # трохи суворіше для шумних періодів
        },
        # --- Метадані для аналізу стану ---
        "meta": {
            "class": "precious_metal",
            "sensitivity": "low_noise",
            "range_bias": "soft_breakout",
        },  # низька чутливість до шумів
    },
}
