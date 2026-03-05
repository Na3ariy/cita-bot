"""
Точка входу: запускає Telegram-бота з планувальником.
"""
import asyncio
import logging
import sys

from config import TELEGRAM_TOKEN, CHECK_INTERVAL
from bot import build_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def main() -> None:
    if not TELEGRAM_TOKEN:
        logger.error("❌ TELEGRAM_TOKEN не встановлено! Додай його як змінну середовища.")
        sys.exit(1)

    logger.info("🚀 Запускаємо бота...")
    logger.info(f"⏱  Інтервал перевірки: {CHECK_INTERVAL // 60} хв.")

    app = build_app()

    logger.info("✅ Бот запущений. Очікую повідомлень...")
    app.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
