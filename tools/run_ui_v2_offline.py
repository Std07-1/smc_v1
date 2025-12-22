"""Офлайн запуск UI_v2 (HTTP+WS) без FXCM і без SMC пайплайна.

Навіщо:
- Коли ринок закритий або FXCM не доступний, але треба «бачити» останні бари й
  оверлеї (FVG/POI/події) на графіку.
- Разом із `tools/replay_snapshot_to_viewer.py` дає режим "replay/QA":
  UI працює, а SMC-state подається зі снапшоту бар-за-баром.

Запуск (PowerShell):
; function с { }
; cd C:/Aione_projects/smc_v1
; & "C:/Aione_projects/smc_v1/.venv/Scripts/python.exe" tools/run_ui_v2_offline.py

Потім відкрий:
- http://127.0.0.1:8080/

Примітка:
- Потрібен запущений Redis (той самий, що використовує основний пайплайн).
- Цей скрипт НЕ запускає `smc_producer` і НЕ піднімає FXCM WS.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
from pathlib import Path


def _ensure_repo_on_syspath() -> None:
    """Гарантує імпорти з кореня репо при запуску як скрипта.

    У Windows запуск `python tools/xxx.py` не додає корінь проєкту в sys.path,
    тому локальні імпорти (`app`, `config`, `UI_v2`) можуть не знаходитися.
    """

    if __package__:
        return
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))


_ensure_repo_on_syspath()

from app.runtime import bootstrap, create_redis_client  # noqa: E402
from config.config import (  # noqa: E402
    REDIS_CHANNEL_SMC_VIEWER_EXTENDED,
    REDIS_SNAPSHOT_KEY_SMC_VIEWER,
)
from UI_v2.ohlcv_provider import UnifiedStoreOhlcvProvider  # noqa: E402
from UI_v2.viewer_state_server import ViewerStateHttpServer  # noqa: E402
from UI_v2.viewer_state_store import ViewerStateStore  # noqa: E402
from UI_v2.viewer_state_ws_server import ViewerStateWsServer  # noqa: E402

logger = logging.getLogger("tools.run_ui_v2_offline")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler())


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        logger.warning(
            "Некоректне значення %s=%s — використовую %d", name, raw, default
        )
        return default


def _pick_free_port(host: str, preferred_port: int, *, max_tries: int = 50) -> int:
    """Повертає перший вільний TCP-порт, починаючи з preferred_port.

    Це робить офлайн-UI більш дружнім: якщо порт зайнятий (Errno 10048),
    ми просто підбираємо наступний вільний порт.
    """

    start = max(1, int(preferred_port))
    for port in range(start, start + max_tries + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((host, port))
                return port
        except OSError:
            continue
    return start


async def main() -> None:
    datastore, _cfg = await bootstrap()

    redis_conn, source = create_redis_client(decode_responses=True)
    logger.info("Redis підключено (%s)", source)

    snapshot_key = os.getenv("SMC_VIEWER_SNAPSHOT_KEY", REDIS_SNAPSHOT_KEY_SMC_VIEWER)
    host = os.getenv("SMC_VIEWER_HTTP_HOST", "127.0.0.1") or "127.0.0.1"
    port = _env_int("SMC_VIEWER_HTTP_PORT", 8080)
    port_eff = _pick_free_port(host, port)
    if port_eff != port:
        logger.warning("HTTP порт %d зайнятий, використовую %d", port, port_eff)
    port = port_eff

    ws_host = os.getenv("SMC_VIEWER_WS_HOST", host) or host
    ws_port = _env_int("SMC_VIEWER_WS_PORT", 8081)
    ws_port_eff = _pick_free_port(ws_host, ws_port)
    if ws_port_eff == port:
        ws_port_eff = _pick_free_port(ws_host, ws_port_eff + 1)
    if ws_port_eff != ws_port:
        logger.warning("WS порт %d зайнятий, використовую %d", ws_port, ws_port_eff)
    ws_port = ws_port_eff

    store = ViewerStateStore(redis=redis_conn, snapshot_key=snapshot_key)
    ohlcv_provider = UnifiedStoreOhlcvProvider(datastore)

    http_server = ViewerStateHttpServer(
        store=store,
        ohlcv_provider=ohlcv_provider,
        host=host,
        port=port,
    )

    ws_server = ViewerStateWsServer(
        store=store,
        redis=redis_conn,
        channel_name=REDIS_CHANNEL_SMC_VIEWER_EXTENDED,
        host=ws_host,
        port=ws_port,
    )

    logger.info(
        "UI_v2 offline запущено: HTTP http://%s:%d  WS ws://%s:%d  snapshot_key=%s",
        host,
        port,
        ws_host,
        ws_port,
        snapshot_key,
    )

    await asyncio.gather(
        http_server.run(),
        ws_server.run(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Зупинено користувачем (Ctrl+C).")
