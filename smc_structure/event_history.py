"""Пам'ять для BOS/CHOCH подій структури."""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd
from rich.console import Console
from rich.logging import RichHandler

from smc_core.smc_types import SmcStructureEvent

# ───────────────────────────── Логування ─────────────────────────────
logger = logging.getLogger("smc_structure.event_history")
if not logger.handlers:  # захист від повторної ініціалізації
    logger.setLevel(logging.DEBUG)
    # show_path=True для відображення файлу/рядка у WARN/ERROR
    logger.addHandler(RichHandler(console=Console(stderr=True), show_path=True))
    logger.propagate = False


@dataclass(slots=True)
class _TrackedEvent:
    """Обгортка над подією з мітками часу появи."""

    event: SmcStructureEvent
    first_seen: pd.Timestamp
    last_seen: pd.Timestamp


class StructureEventHistory:
    """Зберігає BOS/CHOCH події для символа/таймфрейму з TTL."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: dict[tuple[str, str], OrderedDict[str, _TrackedEvent]] = {}

    def update_history(
        self,
        *,
        symbol: str,
        timeframe: str,
        events: Iterable[SmcStructureEvent],
        snapshot_end_ts: pd.Timestamp | None,
        retention_minutes: int,
        max_entries: int,
    ) -> list[SmcStructureEvent]:
        key = (symbol.lower(), timeframe.lower())
        now = snapshot_end_ts or pd.Timestamp.utcnow()
        new_events = list(events or [])
        with self._lock:
            bucket = self._store.setdefault(key, OrderedDict())
            added = 0
            for event in new_events:
                event_key = self._event_key(event)
                tracked = bucket.get(event_key)
                if tracked is None:
                    bucket[event_key] = _TrackedEvent(
                        event=event,
                        first_seen=now,
                        last_seen=now,
                    )
                    added += 1
                else:
                    tracked.event = event
                    tracked.last_seen = max(tracked.last_seen, now)
            pruned = self._prune_bucket(bucket, now, retention_minutes, max_entries)
            after_count = len(bucket)
            logger.debug(
                "Оновлено історію BOS/CHOCH",
                extra={
                    "symbol": key[0],
                    "timeframe": key[1],
                    "added": added,
                    "pruned": pruned,
                    "retained": after_count,
                    "retention_minutes": retention_minutes,
                    "max_entries": max_entries,
                },
            )
            return [tracked.event for tracked in bucket.values()]

    def get_history(self, symbol: str, timeframe: str) -> list[SmcStructureEvent]:
        key = (symbol.lower(), timeframe.lower())
        with self._lock:
            bucket = self._store.get(key)
            if not bucket:
                return []
            return [tracked.event for tracked in bucket.values()]

    def clear(self, symbol: str | None = None, timeframe: str | None = None) -> None:
        with self._lock:
            if symbol is None and timeframe is None:
                self._store.clear()
                return
            symbol_key = symbol.lower() if symbol else None
            timeframe_key = timeframe.lower() if timeframe else None
            keys_to_delete: list[tuple[str, str]] = []
            for existing_symbol, existing_tf in self._store:
                if symbol_key is not None and existing_symbol != symbol_key:
                    continue
                if timeframe_key is not None and existing_tf != timeframe_key:
                    continue
                keys_to_delete.append((existing_symbol, existing_tf))
            for candidate in keys_to_delete:
                self._store.pop(candidate, None)

    def _prune_bucket(
        self,
        bucket: OrderedDict[str, _TrackedEvent],
        now: pd.Timestamp,
        retention_minutes: int,
        max_entries: int,
    ) -> int:
        removed = 0
        items = list(bucket.items())
        if retention_minutes > 0:
            cutoff = now - pd.Timedelta(minutes=retention_minutes)
            items = [
                (key, tracked)
                for key, tracked in items
                if self._event_time(tracked.event, now) >= cutoff
            ]
            removed += len(bucket) - len(items)
        items.sort(key=lambda item: self._event_time(item[1].event, now))
        if max_entries > 0 and len(items) > max_entries:
            removed += len(items) - max_entries
            items = items[-max_entries:]
        bucket.clear()
        for key, value in items:
            bucket[key] = value
        return removed

    @staticmethod
    def _event_key(event: SmcStructureEvent) -> str:
        ts = event.time.isoformat() if event.time is not None else "unknown"
        return f"{event.event_type}:{event.direction}:{ts}:{event.price_level:.6f}"

    @staticmethod
    def _event_time(event: SmcStructureEvent, fallback: pd.Timestamp) -> pd.Timestamp:
        try:
            ts = pd.Timestamp(event.time)
        except Exception:
            return fallback
        return ts


EVENT_HISTORY = StructureEventHistory()


def reset_structure_event_history(
    symbol: str | None = None, timeframe: str | None = None
) -> None:
    """Скидає кеш історії для тестів або діагностики."""

    EVENT_HISTORY.clear(symbol=symbol, timeframe=timeframe)
