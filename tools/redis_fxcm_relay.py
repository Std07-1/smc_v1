"""Релей FXCM Pub/Sub: VPS Redis → локальний Redis.

Мета: перемкнутися на локальну розробку (SMC/UI локально), не змішуючись із
системою, що працює на VPS, але отримувати свічки/статус від FXCM-конектора.

Патерн:
- Підключаємось до **remote** Redis (звичайно через SSH-тунель до VPS).
- Підписуємось на канали FXCM (`fxcm:ohlcv`, `fxcm:price_tik`, `fxcm:status`).
- Перепубліковуємо ці ж повідомлення в **local** Redis на ті самі канали.

Таким чином:
- VPS нічого не змінюємо (лише читаємо).
- Локальний SMC/UDS/UI працює з локальним Redis і не забруднює VPS-ключі/канали.

Запуск (PowerShell, рекомендовано через SSH-тунель):

1) Тунель до VPS Redis (на VPS Redis має слухати 127.0.0.1:6379):
   ssh -N -L 16379:127.0.0.1:6379 <user>@<vps_host>

2) Релей (remote = тунельний порт, local = стандартний локальний Redis):
   ./.venv/Scripts/python.exe -m tools.redis_fxcm_relay --remote-host 127.0.0.1 --remote-port 16379 --local-host 127.0.0.1 --local-port 6379

Після цього локально запускаємо SMC/UI, який читає локальний Redis.

Примітки:
- Скрипт не декодує payload: пересилає bytes як є.
- Є reconnect/backoff, щоб короткі обриви мережі/SSH не валили процес.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from dataclasses import dataclass

from redis.asyncio import Redis

from core.contracts.fxcm_channels import (
    FXCM_CH_OHLCV,
    FXCM_CH_PRICE_TIK,
    FXCM_CH_STATUS,
)

logger = logging.getLogger("tools.redis_fxcm_relay")


@dataclass(frozen=True)
class RelayCfg:
    remote_host: str
    remote_port: int
    remote_password: str | None
    local_host: str
    local_port: int
    local_password: str | None
    channels: tuple[str, ...]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Релей FXCM каналів: remote Redis -> local Redis (read-only з VPS)"
    )
    parser.add_argument(
        "--remote-host",
        default=os.getenv("REMOTE_REDIS_HOST", ""),
        help="Хост remote Redis (або ENV REMOTE_REDIS_HOST)",
    )
    parser.add_argument(
        "--remote-port",
        type=int,
        default=int(os.getenv("REMOTE_REDIS_PORT", "0") or 0),
        help="Порт remote Redis (або ENV REMOTE_REDIS_PORT)",
    )
    parser.add_argument(
        "--remote-password",
        default=os.getenv("REMOTE_REDIS_PASSWORD"),
        help="Пароль remote Redis (або ENV REMOTE_REDIS_PASSWORD)",
    )

    parser.add_argument(
        "--local-host",
        default=os.getenv("LOCAL_REDIS_HOST", "127.0.0.1"),
        help="Хост local Redis (або ENV LOCAL_REDIS_HOST, default 127.0.0.1)",
    )
    parser.add_argument(
        "--local-port",
        type=int,
        default=int(os.getenv("LOCAL_REDIS_PORT", "6379") or 6379),
        help="Порт local Redis (або ENV LOCAL_REDIS_PORT, default 6379)",
    )
    parser.add_argument(
        "--local-password",
        default=os.getenv("LOCAL_REDIS_PASSWORD"),
        help="Пароль local Redis (або ENV LOCAL_REDIS_PASSWORD)",
    )

    parser.add_argument(
        "--channels",
        nargs="+",
        default=[FXCM_CH_STATUS, FXCM_CH_PRICE_TIK, FXCM_CH_OHLCV],
        help="Список каналів для ретрансляції (default: fxcm:status fxcm:price_tik fxcm:ohlcv)",
    )
    return parser.parse_args()


def _build_cfg(args: argparse.Namespace) -> RelayCfg:
    remote_host = str(getattr(args, "remote_host", "") or "").strip()
    remote_port = int(getattr(args, "remote_port", 0) or 0)
    if not remote_host or remote_port <= 0:
        raise SystemExit(
            "Не задано remote Redis. Вкажіть --remote-host/--remote-port або ENV REMOTE_REDIS_HOST/REMOTE_REDIS_PORT. "
            "(Зазвичай remote доступний через SSH-тунель.)"
        )

    local_host = str(getattr(args, "local_host", "127.0.0.1") or "127.0.0.1").strip()
    local_port = int(getattr(args, "local_port", 6379) or 6379)

    channels_raw = getattr(args, "channels", None) or []
    channels: list[str] = []
    for ch in channels_raw:
        c = str(ch or "").strip()
        if c:
            channels.append(c)
    if not channels:
        raise SystemExit("channels не може бути порожнім")

    return RelayCfg(
        remote_host=remote_host,
        remote_port=remote_port,
        remote_password=(
            str(args.remote_password).strip() if args.remote_password else None
        ),
        local_host=local_host,
        local_port=local_port,
        local_password=(
            str(args.local_password).strip() if args.local_password else None
        ),
        channels=tuple(channels),
    )


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _channel_to_str(value: object) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    return str(value)


async def _run_relay(cfg: RelayCfg, stop_event: asyncio.Event) -> None:
    backoff_sec = 1.0
    backoff_max_sec = 30.0

    while not stop_event.is_set():
        remote = Redis(
            host=cfg.remote_host,
            port=cfg.remote_port,
            password=cfg.remote_password,
            decode_responses=False,
        )
        local = Redis(
            host=cfg.local_host,
            port=cfg.local_port,
            password=cfg.local_password,
            decode_responses=False,
        )
        pubsub = remote.pubsub()
        try:
            await remote.ping()
            await local.ping()

            await pubsub.subscribe(*cfg.channels)
            logger.info(
                "[RELAY] Підписано remote %s:%d -> local %s:%d | channels=%s",
                cfg.remote_host,
                cfg.remote_port,
                cfg.local_host,
                cfg.local_port,
                ",".join(cfg.channels),
            )
            backoff_sec = 1.0

            while not stop_event.is_set():
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if not msg:
                    continue

                ch = _channel_to_str(msg.get("channel"))
                data = msg.get("data")
                if data is None:
                    continue

                # Важливо: передаємо bytes як є.
                await local.publish(ch, data)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[RELAY] Помилка/обрив. Перепідключення через %.1fs. Причина: %s",
                backoff_sec,
                exc,
            )
            await asyncio.sleep(backoff_sec)
            backoff_sec = min(backoff_max_sec, max(1.0, backoff_sec * 1.6))
        finally:
            try:
                try:
                    await pubsub.unsubscribe(*cfg.channels)
                finally:
                    await pubsub.close()
            except Exception:
                pass
            try:
                await remote.close()
            except Exception:
                pass
            try:
                await local.close()
            except Exception:
                pass


async def main() -> None:
    _setup_logging()
    args = _parse_args()
    cfg = _build_cfg(args)

    stop_event = asyncio.Event()

    def _request_stop() -> None:
        stop_event.set()

    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _request_stop)
            except NotImplementedError:
                # Windows: сигнал-хендлери можуть бути недоступні.
                pass

        await _run_relay(cfg, stop_event)
    finally:
        logger.info("[RELAY] Зупинка")


if __name__ == "__main__":
    asyncio.run(main())
