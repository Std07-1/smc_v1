"""Entry-point для експериментального SMC viewer.
Відображає стан одного символу в терміналі за допомогою rich.
Приклад виклику:
    python -m UI.ui_consumer_experimental_entry
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from rich.console import Console
from rich.logging import RichHandler

from config.config import (
    FXCM_FAST_SYMBOLS,
    REDIS_CHANNEL_ASSET_STATE,
    UI_VIEWER_PROFILE,
)
from UI.ui_consumer_experimental import ExperimentalUIConsumer

logger = logging.getLogger("ui_consumer_experimental_entry")
logger.setLevel(logging.INFO)
logger.handlers.clear()
logger.addHandler(RichHandler(console=Console(stderr=True), show_path=False))
logger.propagate = False


def _resolve_symbol() -> str:
    candidates = [sym.lower() for sym in FXCM_FAST_SYMBOLS if sym]
    cli_arg = sys.argv[1].lower() if len(sys.argv) > 1 else None
    if cli_arg:
        if cli_arg in candidates:
            return cli_arg
        logger.warning(
            "Symbol %s не входить до FXCM_FAST_SYMBOLS (%s); використовую %s",
            cli_arg,
            ", ".join(candidates) or "порожній список",
            candidates[0] if candidates else "xauusd",
        )
    if candidates:
        return candidates[0]
    return "xauusd"


async def main() -> None:
    symbol = _resolve_symbol()
    profile = UI_VIEWER_PROFILE
    consumer = ExperimentalUIConsumer(symbol=symbol, viewer_profile=profile)
    logger.info("🚀 Запуск experimental SMC viewer для %s", symbol.upper())
    await consumer.redis_consumer(
        redis_url=(
            os.getenv("REDIS_URL")
            or f"redis://{os.getenv('REDIS_HOST','localhost')}:{os.getenv('REDIS_PORT','6379')}/0"
        ),
        channel=REDIS_CHANNEL_ASSET_STATE,
        refresh_rate=0.5,
        loading_delay=1.0,
        smooth_delay=0.05,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Завершення experimental viewer по Ctrl+C…")
        sys.exit(0)
    except asyncio.CancelledError:
        logger.info("Experimental viewer скасовано…")
        sys.exit(0)
