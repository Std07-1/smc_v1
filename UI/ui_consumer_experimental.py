"""Асинхронний консюмер для експериментального SMC viewer.
Відображає стан одного символу в терміналі за допомогою rich.
Приклад виклику див. у ui_consumer_experimental_entry.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import redis.asyncio as redis
from rich.console import Console
from rich.live import Live
from rich.logging import RichHandler
from rich.panel import Panel

from config.config import (
    FXCM_FAST_SYMBOLS,
    REDIS_CHANNEL_ASSET_STATE,
    REDIS_SNAPSHOT_KEY,
    UI_VIEWER_PROFILE,
)
from UI.experimental_viewer import SmcExperimentalViewer
from UI.experimental_viewer_extended import SmcExperimentalViewerExtended

logger = logging.getLogger("ui_consumer_experimental")
logger.setLevel(logging.INFO)
logger.handlers.clear()
logger.addHandler(RichHandler(console=Console(stderr=True), show_path=False))
logger.propagate = False


DEFAULT_VIEWER_SYMBOL = FXCM_FAST_SYMBOLS[0].lower() if FXCM_FAST_SYMBOLS else "xauusd"


class ExperimentalUIConsumer:
    """Легковаговий консюмер, що відображає один символ у SMC viewer."""

    def __init__(
        self,
        symbol: str | None = None,
        snapshot_dir: str = "tmp",
        viewer_profile: str | None = None,
    ) -> None:
        base_symbol = (symbol or DEFAULT_VIEWER_SYMBOL or "xauusd").lower()
        self.symbol = base_symbol
        self.console = Console(stderr=False, force_terminal=True)
        self.viewer_profile = (viewer_profile or UI_VIEWER_PROFILE).lower()
        self.viewer = self._create_viewer(snapshot_dir)

    async def redis_consumer(
        self,
        redis_url: str | None = None,
        channel: str | None = None,
        refresh_rate: float = 0.5,  # секунд на оновлення екрану
        loading_delay: float = 1.0,  # секунд очікування початкового snapshot
        smooth_delay: float = 0.05,  # секунд очікування між перевірками повідомлень
    ) -> None:
        redis_url = (
            redis_url
            or os.getenv("REDIS_URL")
            or f"redis://{os.getenv('REDIS_HOST','localhost')}:{os.getenv('REDIS_PORT','6379')}/0"
        )
        channel = channel or REDIS_CHANNEL_ASSET_STATE

        redis_client = redis.from_url(
            redis_url, decode_responses=True, encoding="utf-8"
        )
        pubsub = redis_client.pubsub()

        placeholder = self.viewer.render_placeholder()
        await self._hydrate_from_snapshot(redis_client)

        await pubsub.subscribe(channel)
        logger.debug("SMC experimental viewer підписано на канал %s", channel)

        await asyncio.sleep(loading_delay)
        with Live(
            placeholder,
            console=self.console,
            refresh_per_second=refresh_rate,
            screen=False,
        ) as live:
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if not message:
                    await asyncio.sleep(smooth_delay)
                    continue
                try:
                    data = json.loads(message.get("data"))
                except Exception:
                    logger.debug(
                        "Не вдалося розпарсити payload experimental UI", exc_info=True
                    )
                    continue
                asset = self._extract_asset(data)
                if asset is None:
                    continue
                viewer_state = self.viewer.build_state(
                    asset, data.get("meta") or {}, data.get("fxcm")
                )
                live.update(self.viewer.render_panel(viewer_state))
                self.viewer.dump_snapshot(viewer_state)

    async def _hydrate_from_snapshot(self, redis_client: redis.Redis) -> None:
        try:
            snapshot_raw = await redis_client.get(REDIS_SNAPSHOT_KEY)
            if not snapshot_raw:
                return
            data = json.loads(snapshot_raw)
            asset = self._extract_asset(data)
            if asset is None:
                return
            viewer_state = self.viewer.build_state(
                asset, data.get("meta") or {}, data.get("fxcm")
            )
            panel = self.viewer.render_panel(viewer_state)
            self.console.print(panel)
            self.viewer.dump_snapshot(viewer_state)
        except Exception:
            logger.debug(
                "Початковий snapshot для experimental UI недоступний", exc_info=True
            )

    def _extract_asset(self, payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        assets = payload.get("assets")
        if not isinstance(assets, list):
            return None
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            symbol = str(asset.get("symbol") or "").lower()
            if symbol == self.symbol:
                return asset
        return None

    @staticmethod
    def placeholder_panel() -> Panel:
        return Panel("Очікування даних…", border_style="yellow")

    def _create_viewer(self, snapshot_dir: str) -> SmcExperimentalViewer:
        if self.viewer_profile == "extended":
            return SmcExperimentalViewerExtended(
                symbol=self.symbol, snapshot_dir=snapshot_dir
            )
        return SmcExperimentalViewer(symbol=self.symbol, snapshot_dir=snapshot_dir)
