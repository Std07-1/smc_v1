"""WebSocket сервер, який прокидує Redis-канали FXCM у браузер.

Призначення:
- дев-інструменти (dev_chart_playground) мають бачити live-бар (complete=false)
  і complete-бари без прямого доступу до Redis.

WS endpoints:
- ws://HOST:PORT/fxcm/ohlcv?symbol=XAUUSD&tf=1m
- ws://HOST:PORT/fxcm/ticks?symbol=XAUUSD
- ws://HOST:PORT/fxcm/status

Пейлоад:
- OHLCV: прокидуємо лише потрібні поля: symbol, tf, bars[] (із complete/synthetic).
- ticks: прокидуємо нормалізований снапшот bid/ask/mid із часовими мітками.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

from core.contracts.fxcm_channels import (
    FXCM_CH_OHLCV,
    FXCM_CH_PRICE_TIK,
    FXCM_CH_STATUS,
)
from core.contracts.fxcm_validate import (
    validate_fxcm_ohlcv_message,
    validate_fxcm_price_tick_message,
    validate_fxcm_status_message,
)
from core.serialization import json_dumps, json_loads

try:  # pragma: no cover - опційна залежність у runtime
    from redis.asyncio import Redis
except Exception:  # pragma: no cover
    Redis = Any  # type: ignore[assignment]

from websockets.exceptions import ConnectionClosed

try:
    from websockets.asyncio.server import ServerConnection as WsConnection, serve
except Exception:  # pragma: no cover
    from websockets.legacy.server import WebSocketServerProtocol as WsConnection, serve

logger = logging.getLogger("fxcm_ohlcv_ws")


def _env_flag(name: str, default: str = "0") -> bool:
    raw = os.getenv(name, default)
    value = str(raw).strip().lower()
    return value in {"1", "true", "yes", "on"}


FXCM_WS_STRICT_VALIDATE_ENABLED = _env_flag("FXCM_WS_STRICT_VALIDATE", "0")


def _should_gate_fxcm_payload(
    kind: str, payload: object, *, strict_enabled: bool
) -> bool:
    """Повертає True, якщо payload має бути відсічений у strict-режимі.

    Важливо:
    - якщо strict вимкнено, ніколи не гейтить (поведінка ідентична soft-validate).
    - не логує і не модифікує payload.
    """

    if not strict_enabled:
        return False

    if kind == "ohlcv":
        return validate_fxcm_ohlcv_message(payload) is None
    if kind == "ticks":
        return validate_fxcm_price_tick_message(payload) is None
    if kind == "status":
        return validate_fxcm_status_message(payload) is None
    return False


@dataclass(slots=True)
class FxcmOhlcvWsServer:
    """WS сервер для live трансляції FXCM OHLCV."""

    redis: Redis  # type: ignore[type-arg]
    channel_name: str = FXCM_CH_OHLCV
    price_tick_channel_name: str = FXCM_CH_PRICE_TIK
    status_channel_name: str = FXCM_CH_STATUS
    host: str = "127.0.0.1"
    port: int = 8082
    _soft_validate_last_log_ts: dict[str, float] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _soft_validate_debounce_s: float = 10.0

    def _soft_validate(
        self, kind: str, payload: dict[str, Any], *, ctx: str = ""
    ) -> None:
        """Soft-validate без впливу на протокол.

        Правило хвилі C4.3b': валідація не блокує повідомлення і не змінює outgoing.
        """

        if kind == "ohlcv":
            ok = validate_fxcm_ohlcv_message(payload) is not None
        elif kind == "ticks":
            ok = validate_fxcm_price_tick_message(payload) is not None
        elif kind == "status":
            ok = validate_fxcm_status_message(payload) is not None
        else:
            ok = True

        if ok:
            return

        now = time.monotonic()
        last = self._soft_validate_last_log_ts.get(kind)
        if last is not None and (now - last) < self._soft_validate_debounce_s:
            return
        self._soft_validate_last_log_ts[kind] = now

        suffix = f" ({ctx})" if ctx else ""
        logger.warning(
            "FXCM WS: невалідний payload для %s%s; пропускаю далі (soft-validate).",
            kind,
            suffix,
        )

    async def run(self) -> None:
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
                    "[FXCM OHLCV WS] Listening on %s (channel=%s)",
                    sockets or "(no sockets)",
                    self.channel_name,
                )
                await asyncio.Future()
        except asyncio.CancelledError:
            logger.info("[FXCM OHLCV WS] Server task cancelled")
            raise

    async def _handle_client(self, websocket: WsConnection) -> None:
        path = getattr(websocket, "path", None) or ""
        if not path:
            request = getattr(websocket, "request", None)
            path = getattr(request, "path", "") if request is not None else ""
        ohlcv_selection = self._extract_ohlcv_selection(path)
        tick_selection = self._extract_tick_selection(path)
        wants_status = self._is_status_path(path)
        if ohlcv_selection is None and tick_selection is None and not wants_status:
            await websocket.close(code=4400, reason="unsupported endpoint")
            return

        if ohlcv_selection is not None:
            symbol, tf = ohlcv_selection
            await self._handle_ohlcv(websocket, symbol=symbol, tf=tf)
            return

        if wants_status:
            await self._handle_status(websocket)
            return

        assert tick_selection is not None
        symbol = tick_selection
        await self._handle_ticks(websocket, symbol=symbol)

    async def _handle_ohlcv(
        self,
        websocket: WsConnection,
        *,
        symbol: str,
        tf: str,
    ) -> None:
        logger.info("[FXCM OHLCV WS] Client subscribed %s %s", symbol, tf)

        pubsub = self.redis.pubsub()
        await pubsub.subscribe(self.channel_name)
        try:
            while True:
                if getattr(websocket, "close_code", None) is not None:
                    break
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if message is None:
                    continue

                payload = self._parse_message(message.get("data"))
                if payload is None:
                    continue

                msg_symbol = str(payload.get("symbol") or "").upper()
                msg_tf = str(
                    payload.get("tf") or payload.get("timeframe") or ""
                ).lower()
                if msg_symbol != symbol or msg_tf != tf:
                    continue

                self._soft_validate("ohlcv", payload, ctx=f"{msg_symbol} {msg_tf}")

                # Для браузера тримаємо контракт простим.
                outgoing = {
                    "symbol": msg_symbol,
                    "tf": msg_tf,
                    "bars": (
                        payload.get("bars")
                        if isinstance(payload.get("bars"), list)
                        else []
                    ),
                }

                if _should_gate_fxcm_payload(
                    "ohlcv",
                    outgoing,
                    strict_enabled=FXCM_WS_STRICT_VALIDATE_ENABLED,
                ):
                    continue
                await websocket.send(json_dumps(outgoing))
        except ConnectionClosed:
            logger.debug("[FXCM OHLCV WS] Client disconnected (%s %s)", symbol, tf)
        finally:
            try:
                await pubsub.unsubscribe(self.channel_name)
            except Exception:
                pass
            await pubsub.close()

    async def _handle_ticks(
        self,
        websocket: WsConnection,
        *,
        symbol: str,
    ) -> None:
        logger.info("[FXCM TICKS WS] Client subscribed %s", symbol)

        pubsub = self.redis.pubsub()
        await pubsub.subscribe(self.price_tick_channel_name)
        try:
            while True:
                if getattr(websocket, "close_code", None) is not None:
                    break
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if message is None:
                    continue

                payload = self._parse_message(message.get("data"))
                if payload is None:
                    continue

                msg_symbol = str(payload.get("symbol") or "").upper()
                if msg_symbol != symbol:
                    continue

                self._soft_validate("ticks", payload, ctx=f"{msg_symbol}")

                outgoing = {
                    "symbol": msg_symbol,
                    "bid": payload.get("bid"),
                    "ask": payload.get("ask"),
                    "mid": payload.get("mid"),
                    "tick_ts": payload.get("tick_ts"),
                    "snap_ts": payload.get("snap_ts"),
                }

                if _should_gate_fxcm_payload(
                    "ticks",
                    outgoing,
                    strict_enabled=FXCM_WS_STRICT_VALIDATE_ENABLED,
                ):
                    continue
                await websocket.send(json_dumps(outgoing))
        except ConnectionClosed:
            logger.debug("[FXCM TICKS WS] Client disconnected (%s)", symbol)
        finally:
            try:
                await pubsub.unsubscribe(self.price_tick_channel_name)
            except Exception:
                pass
            await pubsub.close()

    async def _handle_status(self, websocket: WsConnection) -> None:
        logger.info("[FXCM STATUS WS] Client subscribed")

        pubsub = self.redis.pubsub()
        await pubsub.subscribe(self.status_channel_name)
        try:
            while True:
                if getattr(websocket, "close_code", None) is not None:
                    break
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if message is None:
                    continue

                payload = self._parse_message(message.get("data"))
                if payload is None:
                    continue

                self._soft_validate("status", payload)

                if _should_gate_fxcm_payload(
                    "status",
                    payload,
                    strict_enabled=FXCM_WS_STRICT_VALIDATE_ENABLED,
                ):
                    continue

                await websocket.send(json_dumps(payload))
        except ConnectionClosed:
            logger.debug("[FXCM STATUS WS] Client disconnected")
        finally:
            try:
                await pubsub.unsubscribe(self.status_channel_name)
            except Exception:
                pass
            await pubsub.close()

    @staticmethod
    def _extract_ohlcv_selection(path: str) -> tuple[str, str] | None:
        parsed = urlsplit(path)
        if parsed.path != "/fxcm/ohlcv":
            return None
        query = parse_qs(parsed.query)
        symbol_raw = (query.get("symbol") or [""])[0]
        tf_raw = (query.get("tf") or [""])[0]
        symbol = str(symbol_raw).strip().upper()
        tf = str(tf_raw).strip().lower()
        if not symbol or not tf:
            return None
        return symbol, tf

    @staticmethod
    def _extract_tick_selection(path: str) -> str | None:
        parsed = urlsplit(path)
        if parsed.path != "/fxcm/ticks":
            return None
        query = parse_qs(parsed.query)
        symbol_raw = (query.get("symbol") or [""])[0]
        symbol = str(symbol_raw).strip().upper()
        return symbol or None

    @staticmethod
    def _is_status_path(path: str) -> bool:
        parsed = urlsplit(path)
        return parsed.path == "/fxcm/status"

    @staticmethod
    def _parse_message(data: Any) -> dict[str, Any] | None:
        if data is None:
            return None
        if isinstance(data, bytes):
            text = data.decode("utf-8", errors="replace")
        else:
            text = str(data)
        try:
            obj = json_loads(text)
        except Exception:
            return None
        return obj if isinstance(obj, dict) else None


__all__ = ["FxcmOhlcvWsServer"]
