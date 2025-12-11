"""HTTP API для доступу до SmcViewerState поверх Redis snapshot.

Призначення:
- читати snapshot viewer_state з Redis (ключ REDIS_SNAPSHOT_KEY_SMC_VIEWER);
- віддавати один або всі стейти через простий HTTP GET.

Маршрути:
- GET /smc-viewer/snapshot?symbol=SYM
  Повертає SmcViewerState для конкретного символу або 404, якщо його немає.
- GET /smc-viewer/snapshot
  Повертає мапу symbol -> SmcViewerState для всіх доступних символів.

WebSocket-стрім (/smc-viewer/stream) поки не реалізовано; запити туди
отримують 501 з поясненням у JSON.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from UI_v2.ohlcv_provider import OhlcvNotFound, OhlcvProvider
from UI_v2.schemas import OhlcvResponse, SmcViewerState
from UI_v2.viewer_state_store import ViewerStateStore
from prometheus_client import Counter, Histogram

logger = logging.getLogger("smc_viewer_http")

DEFAULT_OHLCV_LIMIT = 500
MAX_OHLCV_LIMIT = 2000

SMC_VIEWER_HTTP_REQUESTS_TOTAL = Counter(
    "ai_one_smc_viewer_http_requests_total",
    "Кількість HTTP-запитів до smc-viewer",
    labelnames=("path", "status"),
)
SMC_VIEWER_HTTP_LATENCY_MS = Histogram(
    "ai_one_smc_viewer_http_latency_ms",
    "Час обробки HTTP-запитів smc-viewer (ms)",
    labelnames=("path",),
    buckets=(1, 5, 10, 25, 50, 100, 250, 500, 1000, 2000),
)


@dataclass
class ViewerStateHttpServer:
    """Простий HTTP-сервер для SmcViewerState поверх asyncio.start_server.

    Мета:
    - не тягнути важких фреймворків;
    - реалізувати мінімально необхідний HTTP-контракт для фронтенду.
    """

    store: ViewerStateStore
    ohlcv_provider: OhlcvProvider | None = None
    host: str = "127.0.0.1"
    port: int = 8080

    async def run(self) -> None:
        """Стартує HTTP-сервер і тримає його вічно."""
        server = await asyncio.start_server(self._handle_client, self.host, self.port)
        addr = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
        logger.info("[SMC viewer HTTP] Server listening on %s", addr)
        async with server:
            await server.serve_forever()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Обробляє один HTTP-запит поверх TCP-з'єднання."""

        path_for_metrics = "unknown"
        status_code = 500
        start = perf_counter()
        try:
            request_head = await reader.readuntil(b"\r\n\r\n")
            response_bytes, status_code, path_for_metrics = (
                await self._process_http_request(request_head)
            )
        except asyncio.IncompleteReadError:
            writer.close()
            await writer.wait_closed()
            return
        except Exception:
            logger.exception("[SMC viewer HTTP] Internal error while handling request")
            response_bytes = self._build_response(
                status_code=500,
                reason="Internal Server Error",
                body={"error": "internal_error"},
            )
            status_code = 500

        writer.write(response_bytes)
        try:
            await writer.drain()
        except Exception:
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        duration_ms = (perf_counter() - start) * 1000.0
        SMC_VIEWER_HTTP_REQUESTS_TOTAL.labels(
            path=path_for_metrics,
            status=str(status_code),
        ).inc()
        SMC_VIEWER_HTTP_LATENCY_MS.labels(path=path_for_metrics).observe(duration_ms)

    async def _process_http_request(
        self, request_head: bytes
    ) -> tuple[bytes, int, str]:
        """Розбирає запит і повертає тіло, статус і шлях для метрик."""

        path_for_metrics = "unknown"
        try:
            text = request_head.decode("utf-8", errors="replace")
            lines = text.split("\r\n")
            request_line = lines[0]
            method, target, _ = request_line.split(" ", 2)
        except Exception:
            return (
                self._build_response(
                    status_code=400,
                    reason="Bad Request",
                    body={"error": "bad_request"},
                ),
                400,
                path_for_metrics,
            )

        method_upper = method.upper()
        if method_upper == "OPTIONS":
            return (
                self._build_response(status_code=200, reason="OK", body=None),
                200,
                path_for_metrics,
            )

        if method_upper != "GET":
            return (
                self._build_response(
                    status_code=405,
                    reason="Method Not Allowed",
                    body={"error": "method_not_allowed"},
                ),
                405,
                path_for_metrics,
            )

        parsed = urlsplit(target)
        path = parsed.path or "unknown"
        path_for_metrics = path
        query_params = parse_qs(parsed.query)
        flat_query = {k: v[0] for k, v in query_params.items() if v}

        if path == "/smc-viewer/ohlcv":
            response_bytes, status = await self._handle_ohlcv(flat_query)
            return response_bytes, status, path_for_metrics

        if path == "/smc-viewer/snapshot":
            symbol = flat_query.get("symbol")

            if symbol:
                response_bytes, status = await self._handle_get_snapshot_for_symbol(
                    symbol
                )
                return response_bytes, status, path_for_metrics
            response_bytes, status = await self._handle_get_snapshot_all()
            return response_bytes, status, path_for_metrics

        if path.startswith("/smc-viewer/stream"):
            # WebSocket не реалізовано; явно повертаємо 501.
            return (
                self._build_response(
                    status_code=501,
                    reason="Not Implemented",
                    body={"error": "websocket_not_implemented"},
                ),
                501,
                path_for_metrics,
            )

        return (
            self._build_response(
                status_code=404,
                reason="Not Found",
                body={"error": "not_found"},
            ),
            404,
            path_for_metrics,
        )

    async def _handle_get_snapshot_all(self) -> tuple[bytes, int]:
        """Обробляє GET /smc-viewer/snapshot без symbol."""
        states = await self.store.get_all_states()
        return (
            self._build_response(
                status_code=200,
                reason="OK",
                body=states,
            ),
            200,
        )

    async def _handle_ohlcv(self, query: Mapping[str, str]) -> tuple[bytes, int]:
        if self.ohlcv_provider is None:
            return (
                self._build_response(
                    status_code=501,
                    reason="Not Implemented",
                    body={"error": "ohlcv_not_enabled"},
                ),
                501,
            )

        symbol = (query.get("symbol") or "").strip().lower()
        timeframe = (query.get("tf") or "").strip()
        limit_raw = (query.get("limit") or str(DEFAULT_OHLCV_LIMIT)).strip()

        if not symbol or not timeframe:
            return (
                self._build_response(
                    status_code=400,
                    reason="Bad Request",
                    body={"error": "symbol_and_tf_required"},
                ),
                400,
            )

        try:
            limit = int(limit_raw or DEFAULT_OHLCV_LIMIT)
        except ValueError:
            return (
                self._build_response(
                    status_code=400,
                    reason="Bad Request",
                    body={"error": "invalid_limit"},
                ),
                400,
            )

        if not (1 <= limit <= MAX_OHLCV_LIMIT):
            return (
                self._build_response(
                    status_code=400,
                    reason="Bad Request",
                    body={"error": "limit_out_of_range"},
                ),
                400,
            )

        try:
            bars = list(await self.ohlcv_provider.fetch_ohlcv(symbol, timeframe, limit))
        except OhlcvNotFound:
            return (
                self._build_response(
                    status_code=404,
                    reason="Not Found",
                    body={"error": "ohlcv_not_found"},
                ),
                404,
            )
        except Exception:
            logger.exception(
                "[SMC viewer HTTP] OHLCV error for %s tf=%s", symbol, timeframe
            )
            return (
                self._build_response(
                    status_code=500,
                    reason="Internal Server Error",
                    body={"error": "ohlcv_internal_error"},
                ),
                500,
            )

        payload: OhlcvResponse = {
            "symbol": symbol,
            "timeframe": timeframe,
            "limit": limit,
            "bars": bars,
        }
        return (
            self._build_response(
                status_code=200,
                reason="OK",
                body=payload,
            ),
            200,
        )

    async def _handle_get_snapshot_for_symbol(self, symbol: str) -> tuple[bytes, int]:
        """Обробляє GET /smc-viewer/snapshot?symbol=SYM."""
        state = await self.store.get_state(symbol)
        if state is None:
            return (
                self._build_response(
                    status_code=404,
                    reason="Not Found",
                    body={"error": "symbol_not_found", "symbol": symbol},
                ),
                404,
            )
        return (
            self._build_response(
                status_code=200,
                reason="OK",
                body=state,
            ),
            200,
        )

    @staticmethod
    def _build_response(
        *,
        status_code: int,
        reason: str,
        body: Any | None,
    ) -> bytes:
        """Будує просту HTTP/1.1-відповідь з JSON-тілом."""

        if body is None:
            body_bytes = b""
        else:
            body_bytes = json.dumps(body, default=str).encode("utf-8")

        headers = [
            f"HTTP/1.1 {status_code} {reason}",
            "Content-Type: application/json; charset=utf-8",
            f"Content-Length: {len(body_bytes)}",
            "Connection: close",
            "Access-Control-Allow-Origin: *",
            "Access-Control-Allow-Headers: Content-Type",
            "Access-Control-Allow-Methods: GET, OPTIONS",
            "",
            "",
        ]
        head_bytes = "\r\n".join(headers).encode("ascii", errors="replace")
        return head_bytes + body_bytes


async def main() -> None:
    """Приклад запуску HTTP-сервера.

    Реальний код інтеграції має передати сюди готовий Redis-клієнт та
    ключ snapshot (наприклад, config.REDIS_SNAPSHOT_KEY_SMC_VIEWER).
    """
    import os

    from redis.asyncio import Redis as AsyncRedis  # type: ignore[import]

    host = os.getenv("SMC_VIEWER_HTTP_HOST", "127.0.0.1")
    port = int(os.getenv("SMC_VIEWER_HTTP_PORT", "8080"))
    redis_host = os.getenv("REDIS_HOST", "127.0.0.1")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    snapshot_key = os.getenv("SMC_VIEWER_SNAPSHOT_KEY", "ai_one:ui:smc_viewer_snapshot")

    redis = AsyncRedis(host=redis_host, port=redis_port, decode_responses=False)
    store = ViewerStateStore(redis=redis, snapshot_key=snapshot_key)
    server = ViewerStateHttpServer(store=store, host=host, port=port)

    await server.run()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
