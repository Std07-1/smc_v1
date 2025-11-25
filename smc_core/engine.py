"""Центральний движок, який координує обчислення SMC."""

from __future__ import annotations

import logging
from typing import Any

import smc_liquidity
import smc_structure
import smc_zones
from smc_core.config import SMC_CORE_CONFIG, SmcCoreConfig
from smc_core.smc_types import SmcHint, SmcInput

LOGGER = logging.getLogger(__name__)


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

        last_price = _extract_last_price(snapshot)
        hint_meta: dict[str, Any] = {"snapshot_tf": snapshot.tf_primary}
        if last_price is not None:
            hint_meta["last_price"] = last_price

        return SmcHint(
            structure=structure_state,
            liquidity=liquidity_state,
            zones=zones_state,
            signals=[],
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
