import asyncio
import logging
import os
import sys

from rich.console import Console
from rich.logging import RichHandler

from config.config import REDIS_CHANNEL_ASSET_STATE  # SIMPLE_UI_MODE fallback
from UI.ui_consumer import UIConsumer

# ── Налаштування логування ─────────────────────────────────────────────────
logger = logging.getLogger("ui_consumer_entry")
logger.setLevel(logging.INFO)
logger.handlers.clear()
logger.addHandler(RichHandler(console=Console(stderr=True), show_path=False))
logger.propagate = False


async def main():
    # Додаємо low_atr_threshold як у конструкторі UI_Consumer
    # Отримуємо SIMPLE_UI_MODE динамічно (fallback False для сумісності зі старими версіями)
    ui = UIConsumer(vol_z_threshold=2.5, low_atr_threshold=0.005)
    logger.info("🚀 Запуск UI Consumer...")

    logger.info(
        "Коротке пояснення: \n"
        "Alerts: BUY / SELL / TOTAL — поточний зріз активних сигналів.\n"
        "Blocks: htf_blocked / lowatr_blocked — оцінка за meta (hard time frame, low ATR).\n"
        "Gen: кумулятивно скільки разів Stage1 сформував ALERT-сигнали за цикл.\n"
        "Skip: скільки циклів минуло без жодного ALERT."
    )
    await ui.redis_consumer(
        redis_url=(
            os.getenv("REDIS_URL")
            or f"redis://{os.getenv('REDIS_HOST','localhost')}:{os.getenv('REDIS_PORT','6379')}/0"
        ),
        channel=REDIS_CHANNEL_ASSET_STATE,
        refresh_rate=0.8,
        loading_delay=1.5,
        smooth_delay=0.05,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Грейсфул завершення при Ctrl+C
        logger.info("Завершення UI Consumer по Ctrl+C…")
        sys.exit(0)
    except asyncio.CancelledError:
        logger.info("UI Consumer скасовано…")
        sys.exit(0)
