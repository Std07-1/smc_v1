"""Сховище SmcViewerState поверх Redis snapshot у Redis."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from core.serialization import json_loads

try:  # pragma: no cover - опційна залежність у runtime
    from redis.asyncio import Redis
except Exception:  # pragma: no cover
    Redis = Any  # type: ignore[assignment]

from core.contracts.viewer_state import SmcViewerState

logger = logging.getLogger("smc_viewer_store")


@dataclass
class ViewerStateStore:
    """Інкапсулює читання snapshot SmcViewerState (symbol -> state)."""

    redis: Redis  # type: ignore[type-arg]
    snapshot_key: str  # ключ снапшота smc viewer state в Redis

    async def get_all_states(self) -> dict[str, SmcViewerState]:
        """Повертає всю мапу symbol -> SmcViewerState або порожню мапу."""

        try:
            raw = await self.redis.get(self.snapshot_key)
        except Exception:
            logger.warning(
                "[SMC viewer store] Не вдалося прочитати snapshot (%s)",
                self.snapshot_key,
                exc_info=True,
            )
            return {}

        if not raw:
            return {}

        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")

        try:
            data = json_loads(raw)
        except Exception:
            logger.warning(
                "[SMC viewer store] Некоректний JSON у snapshot (%s)",
                self.snapshot_key,
                exc_info=True,
            )
            return {}

        if not isinstance(data, dict):
            return {}

        return data  # type: ignore[return-value]

    async def get_state(self, symbol: str) -> SmcViewerState | None:
        """Витягує SmcViewerState для конкретного символу (якщо він є)."""

        if not symbol:
            return None

        lookup = str(symbol)
        candidates = []
        for variant in (lookup, lookup.upper(), lookup.lower()):
            if variant not in candidates:
                candidates.append(variant)

        all_states = await self.get_all_states()
        for key in candidates:
            state = all_states.get(key)
            if isinstance(state, dict):
                return state  # type: ignore[return-value]
        return None
