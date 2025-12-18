"""Діагностика FXCM Redis PubSub каналів.

Показує:
- скільки підписників на ключових каналах (PUBSUB NUMSUB);
- чи реально приходять повідомлення за короткий проміжок часу.

Запуск (PowerShell):
    ./.venv/Scripts/python.exe -m tools.debug_fxcm_channels

Опції:
    ./.venv/Scripts/python.exe -m tools.debug_fxcm_channels --seconds 15
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Any

from redis.asyncio import Redis

from app.settings import settings

_CHANNELS = (
    "fxcm:status",
    "fxcm:price_tik",
    "fxcm:ohlcv",
    "fxcm:commands",
)


def _short(obj: Any, limit: int = 240) -> str:
    try:
        text = json.dumps(obj, ensure_ascii=False)
    except Exception:
        text = str(obj)
    text = text.replace("\n", " ")
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


async def _print_numsub(redis: Redis) -> None:
    rows = await redis.pubsub_numsub(*_CHANNELS)
    print("NUMSUB:")
    for name, count in rows:
        chan = name.decode() if isinstance(name, (bytes, bytearray)) else str(name)
        print(f"  {chan:14s}  subs={int(count)}")


async def _sample_messages(redis: Redis, *, seconds: float = 6.0) -> None:
    pubsub = redis.pubsub()
    await pubsub.subscribe(*_CHANNELS)

    start = time.monotonic()
    counters: dict[str, int] = {ch: 0 for ch in _CHANNELS}
    last_payload: dict[str, Any] = {}

    ohlcv_by_symbol_tf: dict[tuple[str, str], int] = {}
    price_by_symbol: dict[str, int] = {}

    try:
        while True:
            left = seconds - (time.monotonic() - start)
            if left <= 0:
                break
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if not msg:
                continue

            channel_raw = msg.get("channel")
            channel = (
                channel_raw.decode()
                if isinstance(channel_raw, (bytes, bytearray))
                else str(channel_raw)
            )
            counters[channel] = counters.get(channel, 0) + 1

            data = msg.get("data")
            payload: Any = None
            if isinstance(data, (bytes, bytearray)):
                try:
                    payload = json.loads(data.decode("utf-8", errors="replace"))
                except Exception:
                    payload = data[:200]
            else:
                payload = data

            last_payload[channel] = payload

            if channel == "fxcm:ohlcv" and isinstance(payload, dict):
                sym = str(payload.get("symbol") or "?").strip().upper()
                tf = str(payload.get("tf") or "?").strip().lower()
                key = (sym, tf)
                ohlcv_by_symbol_tf[key] = ohlcv_by_symbol_tf.get(key, 0) + 1
            elif channel == "fxcm:price_tik" and isinstance(payload, dict):
                sym = str(payload.get("symbol") or "?").strip().upper()
                price_by_symbol[sym] = price_by_symbol.get(sym, 0) + 1

    finally:
        try:
            await pubsub.unsubscribe(*_CHANNELS)
        finally:
            await pubsub.close()

    print(f"\nSAMPLE ({seconds:.0f}s):")
    for ch in _CHANNELS:
        print(f"  {ch:14s}  msgs={counters.get(ch, 0)}")
        if ch in last_payload:
            print(f"    last={_short(last_payload[ch])}")

    if ohlcv_by_symbol_tf:
        print("\nOHLCV symbols/tf (top):")
        for (sym, tf), cnt in sorted(
            ohlcv_by_symbol_tf.items(), key=lambda kv: kv[1], reverse=True
        )[:20]:
            print(f"  {sym:10s} {tf:4s}  msgs={cnt}")
    else:
        print("\nOHLCV symbols/tf: (немає)")

    if price_by_symbol:
        print("\nPRICE symbols (top):")
        for sym, cnt in sorted(
            price_by_symbol.items(), key=lambda kv: kv[1], reverse=True
        )[:20]:
            print(f"  {sym:10s}  msgs={cnt}")
    else:
        print("\nPRICE symbols: (немає)")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FXCM Redis PubSub debug")
    parser.add_argument(
        "--seconds",
        type=float,
        default=6.0,
        help="Скільки секунд слухати канали (default: 6)",
    )
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    redis = Redis(
        host=settings.redis_host, port=settings.redis_port, decode_responses=False
    )
    try:
        await redis.ping()
        await _print_numsub(redis)
        await _sample_messages(redis, seconds=float(args.seconds))
    finally:
        await redis.close()


if __name__ == "__main__":
    asyncio.run(main())
