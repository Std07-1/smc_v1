"""In-memory менеджер стану для чистого SMC пайплайна."""

from __future__ import annotations

from typing import Any

from config.constants import ASSET_STATE, K_STATS, K_SYMBOL
from core.serialization import utc_now_iso_z


class SmcStateManager:
    """Мінімальний менеджер стану без Stage1 спадщини."""

    def __init__(
        self,
        initial_assets: list[str] | None = None,
        *,
        cache_handler: Any | None = None,
    ) -> None:
        self.state: dict[str, dict[str, Any]] = {}
        self.cache = cache_handler
        for symbol in initial_assets or []:
            self.init_asset(symbol)

    def set_cache_handler(self, cache_handler: Any | None) -> None:
        """Призначаємо хендлер кешу/сховища (best-effort)."""

        self.cache = cache_handler

    def init_asset(self, symbol: str) -> None:
        """Створюємо дефолтну структуру активу."""

        sym = str(symbol).lower()
        self.state[sym] = {
            K_SYMBOL: sym,
            "state": ASSET_STATE["INIT"],
            "signal": "SMC_NONE",
            "smc_hint": None,
            "hints": ["Очікування SMC даних..."],
            K_STATS: {},
            "last_updated": utc_now_iso_z(),
        }

    def update_asset(self, symbol: str, updates: dict[str, Any]) -> None:
        """Мерджимо оновлення з новими полями (без тригерів)."""

        sym = str(symbol).lower()
        if sym not in self.state:
            self.init_asset(sym)

        current = self.state[sym]
        merged = {**current, **updates}

        stats_current = current.get(K_STATS)
        stats_updates = updates.get(K_STATS)
        if isinstance(stats_current, dict) or isinstance(stats_updates, dict):
            merged[K_STATS] = {
                **(stats_current or {}),
                **(stats_updates or {}),
            }

        if "hints" in merged and not isinstance(merged.get("hints"), list):
            merged["hints"] = [str(merged["hints"])]

        merged[K_SYMBOL] = sym
        merged["last_updated"] = utc_now_iso_z()
        self.state[sym] = merged

    def get_all_assets(self) -> list[dict[str, Any]]:
        """Повертаємо копії станів для UI."""

        return [dict(asset) for asset in self.state.values()]
