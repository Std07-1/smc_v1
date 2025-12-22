"""Центральний движок, який координує обчислення SMC."""

from __future__ import annotations

import logging
from typing import Any, cast

import smc_execution
import smc_liquidity
import smc_structure
import smc_zones
from smc_core.config import SMC_CORE_CONFIG, SmcCoreConfig
from smc_core.smc_types import SmcHint, SmcInput, SmcSignal
from smc_core.stage6_scenario import decide_42_43, to_signal_dict

LOGGER = logging.getLogger("smc_core.engine")


class SmcCoreEngine:
    """Оркеструє виклик окремих SMC-підмодулів."""

    def __init__(self, cfg: SmcCoreConfig | None = None) -> None:
        self._cfg = cfg or SMC_CORE_CONFIG

    def process_snapshot(self, snapshot: SmcInput) -> SmcHint:
        """Будує підказку по знімку даних, використовуючи всі підмодулі."""

        LOGGER.debug(
            "SMC обробляє знімок",
            extra={"symbol": snapshot.symbol, "tf": snapshot.tf_primary},
        )
        structure_state = smc_structure.compute_structure_state(snapshot, self._cfg)
        liquidity_state = smc_liquidity.compute_liquidity_state(
            snapshot, structure_state, self._cfg
        )
        # Підетап 4.2: зони містять принаймні Order Block-и з нового детектора.
        zones_state = smc_zones.compute_zones_state(
            snapshot=snapshot,
            structure=structure_state,
            liquidity=liquidity_state,
            cfg=self._cfg,
        )

        # Stage5: execution (1m) — micro-події лише коли in_play біля POI/targets.
        try:
            execution_state = smc_execution.compute_execution_state(
                snapshot=snapshot,
                structure=structure_state,
                liquidity=liquidity_state,
                zones=zones_state,
                cfg=self._cfg,
            )
        except Exception as exc:
            # Execution — soft-fail: не ламаємо підказку.
            LOGGER.debug(
                "Stage5 execution впав, пропускаю",
                extra={"err": str(exc), "symbol": snapshot.symbol},
                exc_info=True,
            )
            execution_state = None

        # Stage6: машинний розбір 4.2 vs 4.3 (не «сигнал», а класифікація сценарію).
        signals: list[SmcSignal] = []
        try:
            primary_frame = snapshot.ohlc_by_tf.get(snapshot.tf_primary)
            if primary_frame is not None:
                decision = decide_42_43(
                    symbol=snapshot.symbol,
                    tf_primary=snapshot.tf_primary,
                    primary_frame=primary_frame,
                    ohlc_by_tf=snapshot.ohlc_by_tf,
                    structure=structure_state,
                    liquidity=liquidity_state,
                    zones=zones_state,
                    context=snapshot.context,
                )
                # to_signal_dict повертає dict — приводимо до SmcSignal для статичної перевірки типів
                signals = [cast(SmcSignal, to_signal_dict(decision))]
        except Exception as exc:
            # Stage6 — soft-fail: не ламаємо підказку.
            LOGGER.debug(
                "Stage6 впав, пропускаю",
                extra={"err": str(exc), "symbol": snapshot.symbol},
                exc_info=True,
            )

        last_price = _extract_last_price(snapshot)
        hint_meta: dict[str, Any] = {"snapshot_tf": snapshot.tf_primary}
        if last_price is not None:
            hint_meta["last_price"] = last_price

        # Replay/QA: preview vs close (для UI-гейтінгу та звітів).
        try:
            ck = (snapshot.context or {}).get("smc_compute_kind")
            if ck is not None:
                hint_meta["smc_compute_kind"] = str(ck)
        except Exception:
            pass

        # Stage0/Context: власні сесійні екстремуми (Asia/London/NY), якщо вже пораховані адаптером.
        ctx = snapshot.context or {}
        for k in (
            "session_tag",
            "smc_session_tag",
            "smc_session_start_ms",
            "smc_session_end_ms",
            "smc_session_high",
            "smc_session_low",
            "smc_session_tf",
            "smc_sessions",
        ):
            if k in ctx:
                hint_meta[k] = ctx.get(k)

        return SmcHint(
            structure=structure_state,
            liquidity=liquidity_state,
            zones=zones_state,
            signals=signals,
            execution=execution_state,
            meta=hint_meta,
        )


def _extract_last_price(snapshot: SmcInput) -> float | None:
    frame = snapshot.ohlc_by_tf.get(snapshot.tf_primary)
    if frame is None:
        return None
    try:
        close_series = frame["close"]
    except Exception:
        return None
    if len(close_series) == 0:
        return None
    try:
        return float(close_series.iloc[-1])
    except Exception:
        return None
