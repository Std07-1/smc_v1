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

from config.config import REDIS_CHANNEL_ASSET_STATE
from UI.ui_consumer_experimental import ExperimentalUIConsumer

logger = logging.getLogger("ui_consumer_experimental_entry")
logger.setLevel(logging.INFO)
logger.handlers.clear()
logger.addHandler(RichHandler(console=Console(stderr=True), show_path=False))
logger.propagate = False


async def main() -> None:
    symbol = os.getenv("SMC_EXPERIMENT_SYMBOL", "xauusd")
    consumer = ExperimentalUIConsumer(symbol=symbol)
    logger.info("🚀 Запуск experimental SMC viewer для %s", symbol.upper())
    await consumer.redis_consumer(
        redis_url=(
            os.getenv("REDIS_URL")
            or f"redis://{os.getenv('REDIS_HOST','localhost')}:{os.getenv('REDIS_PORT','6379')}/0"
        ),
        channel=REDIS_CHANNEL_ASSET_STATE,
        refresh_rate=1.2,
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
