"""CLI-консьюмер для experimental viewer (SMC стан)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any

import redis.asyncio as redis
from redis.asyncio.client import PubSub, Redis
from rich.console import Console
from rich.live import Live

from app.settings import settings
from config.config import (
    FXCM_FAST_SYMBOLS,
    FXCM_PRICE_TICK_CHANNEL,
    REDIS_CHANNEL_SMC_STATE,
    REDIS_SNAPSHOT_KEY_SMC,
    UI_VIEWER_ALT_SCREEN_ENABLED,
    UI_VIEWER_SNAPSHOT_DIR,
)
from UI.experimental_viewer import SmcExperimentalViewer
from UI.experimental_viewer_extended import SmcExperimentalViewerExtended

SMC_FEED_CHANNEL = REDIS_CHANNEL_SMC_STATE
SMC_SNAPSHOT_KEY = REDIS_SNAPSHOT_KEY_SMC
VIEWER_CLASS: type[SmcExperimentalViewer] = SmcExperimentalViewerExtended

CLI_LOGGER = logging.getLogger("smc_viewer.cli")
if not CLI_LOGGER.handlers:
    CLI_LOGGER.setLevel(logging.INFO)
    CLI_LOGGER.addHandler(logging.StreamHandler())
    CLI_LOGGER.propagate = False

ALT_SCREEN_ENABLED = bool(UI_VIEWER_ALT_SCREEN_ENABLED)


def _default_symbol() -> str:
    for sym in FXCM_FAST_SYMBOLS:
        if sym:
            return sym.lower()
    return "xauusd"


def _resolve_symbol(cli_symbol: str | None) -> str:
    if cli_symbol:
        return cli_symbol.lower()
    candidates = [sym.lower() for sym in FXCM_FAST_SYMBOLS if sym]
    return candidates[0] if candidates else _default_symbol()


def _build_default_redis_url() -> str:
    host = settings.redis_host
    port = settings.redis_port
    return f"redis://{host}:{port}/0"


class ExperimentalViewerConsumer:
    """Мінімальний Redis-консьюмер для SMC viewer."""

    def __init__(
        self,
        *,
        symbol: str,
        snapshot_dir: str,
        channel: str = SMC_FEED_CHANNEL,
        snapshot_key: str = SMC_SNAPSHOT_KEY,
        price_tick_channel: str = FXCM_PRICE_TICK_CHANNEL,
        viewer_cls: type[SmcExperimentalViewer] = VIEWER_CLASS,
    ) -> None:
        self.symbol = (symbol or _default_symbol()).lower()
        self.channel = channel
        self.price_tick_channel = (
            price_tick_channel or FXCM_PRICE_TICK_CHANNEL
        ).strip()
        self.snapshot_key = snapshot_key
        self.console = Console(stderr=False, force_terminal=True)
        self.viewer = viewer_cls(symbol=self.symbol, snapshot_dir=snapshot_dir)

        # Кеш останнього SMC payload (щоб оновлювати лише ціну по тиках).
        self._last_asset: dict[str, Any] | None = None
        self._last_meta: dict[str, Any] | None = None
        self._last_fxcm: Any | None = None
        self._last_tick_mid: float | None = None

    async def run(
        self,
        *,
        redis_url: str | None = None,
        refresh_rate: float = 0.5,
        loading_delay: float = 1.0,
        smooth_delay: float = 0.05,
    ) -> None:
        redis_url = redis_url or _build_default_redis_url()
        redis_client = redis.from_url(
            redis_url, decode_responses=True, encoding="utf-8"
        )
        pubsub = redis_client.pubsub()
        placeholder = self.viewer.render_placeholder()
        await self._hydrate_from_snapshot(redis_client)
        channels: list[str] = [self.channel]
        if self.price_tick_channel:
            channels.append(self.price_tick_channel)
        await pubsub.subscribe(*channels)
        CLI_LOGGER.info(
            "Підписка viewer на канали %s (Redis %s)",
            ", ".join(channels),
            redis_url,
        )
        await asyncio.sleep(loading_delay)
        try:
            with Live(
                placeholder,
                console=self.console,
                refresh_per_second=refresh_rate,
                screen=ALT_SCREEN_ENABLED,
            ) as live:
                while True:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=1.0,
                    )
                    if not message:
                        await asyncio.sleep(smooth_delay)
                        continue

                    channel = self._coerce_channel_name(message.get("channel"))
                    payload = self._safe_json(message.get("data"))
                    if payload is None:
                        continue

                    if channel == self.price_tick_channel:
                        tick_mid = self._extract_tick_mid(payload, symbol=self.symbol)
                        if tick_mid is None:
                            continue
                        self._last_tick_mid = float(tick_mid)
                        # Якщо SMC snapshot ще не прийшов — просто чекаємо.
                        if not (self._last_asset and self._last_meta is not None):
                            continue
                        viewer_state = self.viewer.build_state(
                            self._last_asset,
                            self._last_meta,
                            self._last_fxcm,
                        )
                        viewer_state["price"] = self._last_tick_mid
                        live.update(
                            self.viewer.render_panel(viewer_state), refresh=True
                        )
                        self.viewer.dump_snapshot(viewer_state)
                        continue

                    # Основний сценарій: оновлення з SMC payload.
                    asset = self._extract_asset(payload)
                    if asset is None:
                        continue
                    meta = payload.get("meta") or {}
                    fxcm = payload.get("fxcm")
                    self._last_asset = asset
                    self._last_meta = meta if isinstance(meta, dict) else {}
                    self._last_fxcm = fxcm

                    viewer_state = self.viewer.build_state(asset, self._last_meta, fxcm)
                    if self._last_tick_mid is not None:
                        viewer_state["price"] = self._last_tick_mid
                    live.update(self.viewer.render_panel(viewer_state), refresh=True)
                    self.viewer.dump_snapshot(viewer_state)
        finally:
            await self._cleanup(pubsub, redis_client)

    async def _cleanup(self, pubsub: PubSub, client: Redis) -> None:
        try:
            await pubsub.unsubscribe(self.channel)
            if self.price_tick_channel:
                await pubsub.unsubscribe(self.price_tick_channel)
        except Exception:
            CLI_LOGGER.debug("Не вдалося відписатися від %s", self.channel)
        try:
            await pubsub.close()
        except Exception:
            pass
        try:
            await client.close()
        except Exception:
            pass

    async def _hydrate_from_snapshot(self, redis_client: Redis) -> None:
        try:
            snapshot_raw = await redis_client.get(self.snapshot_key)
            if not snapshot_raw:
                return
            data = self._safe_json(snapshot_raw)
            if data is None:
                return
            asset = self._extract_asset(data)
            if asset is None:
                return
            viewer_state = self.viewer.build_state(
                asset,
                data.get("meta") or {},
                data.get("fxcm"),
            )

            self._last_asset = asset
            meta = data.get("meta") or {}
            self._last_meta = meta if isinstance(meta, dict) else {}
            self._last_fxcm = data.get("fxcm")

            self.console.print(self.viewer.render_panel(viewer_state))
            self.viewer.dump_snapshot(viewer_state)
        except Exception:
            CLI_LOGGER.debug(
                "Початковий snapshot для viewer недоступний",
                exc_info=True,
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
            if str(asset.get("symbol") or "").lower() == self.symbol:
                return asset
        return None

    @staticmethod
    def _safe_json(data: Any) -> dict[str, Any] | None:
        if isinstance(data, dict):
            return data
        if isinstance(data, (bytes, bytearray)):
            try:
                text = data.decode("utf-8", errors="replace")
            except Exception:
                return None
            try:
                payload = json.loads(text)
                return payload if isinstance(payload, dict) else None
            except Exception:
                CLI_LOGGER.debug("Не вдалося розпарсити payload viewer", exc_info=True)
                return None
        if isinstance(data, str):
            try:
                payload = json.loads(data)
                return payload if isinstance(payload, dict) else None
            except Exception:
                CLI_LOGGER.debug("Не вдалося розпарсити payload viewer", exc_info=True)
                return None
        return None

    @staticmethod
    def _coerce_channel_name(value: Any) -> str:
        if isinstance(value, (bytes, bytearray)):
            return value.decode("utf-8", errors="ignore")
        return str(value or "").strip()

    @staticmethod
    def _extract_tick_mid(payload: Any, *, symbol: str) -> float | None:
        """Витягує mid з `fxcm:price_tik` для заданого symbol (case-insensitive)."""

        if not isinstance(payload, dict):
            return None
        sym = str(payload.get("symbol") or "").strip().lower()
        if not sym or sym != str(symbol or "").strip().lower():
            return None
        mid = payload.get("mid")
        try:
            return float(mid)
        except Exception:
            return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SMC experimental viewer (extended)")
    parser.add_argument(
        "symbol",
        nargs="?",
        default=None,
        help="Символ із FXCM_FAST_SYMBOLS",
    )
    parser.add_argument(
        "--redis-url",
        default=None,
        help="Повний Redis URL (за замовчуванням з ENV)",
    )
    parser.add_argument(
        "--snapshot-dir",
        default=UI_VIEWER_SNAPSHOT_DIR,
        help="Каталог для збереження viewer snapshot",
    )
    parser.add_argument(
        "--refresh-rate",
        type=float,
        default=0.5,
        help="Частота оновлення екрану (Hz)",
    )
    parser.add_argument(
        "--smooth-delay",
        type=float,
        default=0.05,
        help="Пауза між опитуваннями Redis (сек)",
    )
    return parser.parse_args()


async def _run_cli() -> None:
    args = _parse_args()
    channel = SMC_FEED_CHANNEL
    snapshot_key = SMC_SNAPSHOT_KEY
    symbol = _resolve_symbol(args.symbol)
    consumer = ExperimentalViewerConsumer(
        symbol=symbol,
        snapshot_dir=args.snapshot_dir,
        channel=channel,
        snapshot_key=snapshot_key,
    )
    redis_url = args.redis_url or _build_default_redis_url()
    CLI_LOGGER.info(
        "🚀 Запуск viewer для %s (SMC feed, extended)",
        symbol.upper(),
    )
    await consumer.run(
        redis_url=redis_url,
        refresh_rate=args.refresh_rate,
        smooth_delay=args.smooth_delay,
    )


if __name__ == "__main__":
    try:
        asyncio.run(_run_cli())
    except KeyboardInterrupt:
        CLI_LOGGER.info("Viewer завершено користувачем")
    except asyncio.CancelledError:
        CLI_LOGGER.info("Viewer скасовано")
