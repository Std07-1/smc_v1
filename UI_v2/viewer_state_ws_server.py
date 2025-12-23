"""WebSocket сервер для трансляції SmcViewerState у live-режимі."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

try:  # pragma: no cover - опційна залежність у runtime
    from redis.asyncio import Redis
except Exception:  # pragma: no cover
    Redis = Any  # type: ignore[assignment]

try:  # pragma: no cover - опційно для типів/обробки помилок
    from redis.exceptions import ConnectionError as RedisConnectionError
except Exception:  # pragma: no cover
    RedisConnectionError = Exception  # type: ignore[assignment]

logger = logging.getLogger("smc_viewer_ws")

from websockets.exceptions import ConnectionClosed

from core.serialization import json_dumps, json_loads

try:
    from websockets.asyncio.server import serve
except Exception:  # pragma: no cover - сумісність зі старими версіями
    from websockets.legacy.server import serve

from prometheus_client import Counter, Gauge

from core.contracts.viewer_state import SmcViewerState
from UI_v2.viewer_state_store import ViewerStateStore

SMC_VIEWER_WS_CONNECTIONS = Gauge(
    "ai_one_smc_viewer_ws_connections",
    "Кількість активних WebSocket-підключень smc-viewer",
)
SMC_VIEWER_WS_MESSAGES_TOTAL = Counter(
    "ai_one_smc_viewer_ws_messages_total",
    "Кількість WS-повідомлень smc-viewer за типом",
    labelnames=("type",),
)
SMC_VIEWER_WS_ERRORS_TOTAL = Counter(
    "ai_one_smc_viewer_ws_errors_total",
    "Кількість помилок у WebSocket сервісі smc-viewer",
    labelnames=("stage",),
)


@dataclass(slots=True)
class ViewerStateWsServer:
    """Легкий WebSocket-сервер поверх Redis каналу з viewer_state."""

    store: ViewerStateStore
    redis: Redis  # type: ignore[type-arg]
    channel_name: str
    host: str = "127.0.0.1"
    port: int = 8081

    _stopping: asyncio.Event = field(
        default_factory=asyncio.Event,
        init=False,
        repr=False,
    )

    async def run(self) -> None:
        """Стартує WS-сервер і працює, доки таск не буде завершено."""

        try:
            async with serve(
                self._handle_client,
                self.host,
                self.port,
                ping_interval=20,
                ping_timeout=20,
            ) as ws_server:
                sockets = ", ".join(
                    str(sock.getsockname()) for sock in ws_server.sockets or []
                )
                logger.info(
                    "[SMC viewer WS] Server listening on %s (channel=%s)",
                    sockets or "(no sockets)",
                    self.channel_name,
                )
                await asyncio.Future()
        except asyncio.CancelledError:
            self._stopping.set()
            logger.debug("[SMC viewer WS] Server task cancelled")
            raise
        finally:
            self._stopping.set()

    async def _handle_client(self, websocket: Any) -> None:
        path = getattr(websocket, "path", None) or ""
        if not path:
            request = getattr(websocket, "request", None)
            path = getattr(request, "path", "") if request is not None else ""
        symbol = self._extract_symbol(path)
        if symbol is None:
            await websocket.close(code=4400, reason="symbol query parameter required")
            return

        logger.info("[SMC viewer WS] Client subscribed to %s", symbol)
        SMC_VIEWER_WS_CONNECTIONS.inc()
        try:
            await self._send_initial_state(websocket, symbol)
            await self._stream_updates(websocket, symbol)
        except ConnectionClosed:
            logger.debug("[SMC viewer WS] Client disconnected (%s)", symbol)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - захисний блок
            logger.exception("[SMC viewer WS] Internal error for %s", symbol)
            SMC_VIEWER_WS_ERRORS_TOTAL.labels(stage="handler").inc()
            await websocket.close(code=1011, reason="internal_error")
        finally:
            SMC_VIEWER_WS_CONNECTIONS.dec()

    async def _send_initial_state(
        self,
        websocket: Any,
        symbol: str,
    ) -> None:
        state = await self.store.get_state(symbol)
        payload = self._build_payload("snapshot", symbol, state)
        await websocket.send(payload)
        SMC_VIEWER_WS_MESSAGES_TOTAL.labels(type="snapshot").inc()

    async def _stream_updates(
        self,
        websocket: Any,
        symbol: str,
    ) -> None:
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(self.channel_name)
        try:
            while True:
                if getattr(websocket, "close_code", None) is not None:
                    break
                try:
                    message = await pubsub.get_message(
                        ignore_subscribe_messages=True,
                        timeout=1.0,
                    )
                except asyncio.CancelledError:
                    raise
                except ConnectionClosed:
                    raise
                except RedisConnectionError as exc:
                    # Під час штатного shutdown Redis клієнт може закритись раніше,
                    # ніж завершаться клієнтські хендлери. Це не є помилкою.
                    if self._stopping.is_set():
                        break
                    logger.warning(
                        "[SMC viewer WS] Redis pubsub error (symbol=%s): %s",
                        symbol,
                        str(exc),
                    )
                    SMC_VIEWER_WS_ERRORS_TOTAL.labels(stage="redis_poll").inc()
                    await asyncio.sleep(1.0)
                    continue
                except Exception as exc:
                    if self._stopping.is_set():
                        break
                    logger.warning(
                        "[SMC viewer WS] Redis pubsub error (symbol=%s): %s",
                        symbol,
                        str(exc),
                        exc_info=False,
                    )
                    SMC_VIEWER_WS_ERRORS_TOTAL.labels(stage="redis_poll").inc()
                    await asyncio.sleep(1.0)
                    continue
                if message is None:
                    continue
                parsed = self._parse_channel_message(message.get("data"))
                if parsed is None:
                    SMC_VIEWER_WS_ERRORS_TOTAL.labels(stage="parse").inc()
                    continue
                msg_symbol, viewer_state = parsed
                if msg_symbol != symbol:
                    continue
                payload = self._build_payload("update", msg_symbol, viewer_state)
                try:
                    await websocket.send(payload)
                except ConnectionClosed:
                    raise
                except Exception:
                    logger.warning(
                        "[SMC viewer WS] Failed to send update (symbol=%s)",
                        symbol,
                        exc_info=True,
                    )
                    SMC_VIEWER_WS_ERRORS_TOTAL.labels(stage="send").inc()
                    await asyncio.sleep(0.2)
                    continue
                SMC_VIEWER_WS_MESSAGES_TOTAL.labels(type="update").inc()
        finally:
            try:
                await pubsub.unsubscribe(self.channel_name)
            except Exception:
                pass
            try:
                await pubsub.close()
            except Exception:
                pass

    @staticmethod
    def _extract_symbol(path: str) -> str | None:
        parsed = urlsplit(path)
        if parsed.path != "/smc-viewer/stream":
            return None
        symbols = parse_qs(parsed.query).get("symbol")
        if not symbols:
            return None
        symbol = symbols[0].strip().upper()
        return symbol or None

    @staticmethod
    def _parse_channel_message(
        data: Any,
    ) -> tuple[str, SmcViewerState] | None:
        if isinstance(data, bytes):
            data = data.decode("utf-8", errors="replace")
        if isinstance(data, str):
            try:
                data = json_loads(data)
            except Exception:
                logger.debug("[SMC viewer WS] Failed to parse update", exc_info=True)
                return None
        if not isinstance(data, dict):
            return None
        symbol = str(data.get("symbol") or "").upper()
        state = data.get("viewer_state")
        if not symbol or not isinstance(state, dict):
            return None
        return symbol, state  # type: ignore[return-value]

    @staticmethod
    def _build_payload(
        event_type: str,
        symbol: str,
        state: SmcViewerState | None,
    ) -> str:
        return json_dumps(
            {
                "type": event_type,
                "symbol": symbol,
                "viewer_state": state,
            }
        )
