import asyncio
import logging
import sys
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import MenuButtonWebApp, WebAppInfo

from config import settings
from database.session import init_db
from handlers import filters, payments, profile, start
from parsers.manager import IngestionManager
from webapp.server import create_webapp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("LeadRadarBot")


async def main():
    if settings.BOT_TOKEN == "YOUR_BOT_TOKEN_HERE" or not settings.BOT_TOKEN:
        logger.error(
            "BOT_TOKEN is not configured! Please set BOT_TOKEN in .env file or environment variables."
        )
        return

    logger.info("Initializing database...")
    await init_db()

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Register routers
    dp.include_router(start.router)
    dp.include_router(filters.router)
    dp.include_router(payments.router)
    dp.include_router(profile.router)

    # Configure Telegram Menu Button for WebApp if URL provided
    if settings.WEBAPP_URL:
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="🚀 Открыть Радар",
                    web_app=WebAppInfo(url=settings.WEBAPP_URL),
                )
            )
            logger.info(f"Telegram MenuButton configured with WebApp URL: {settings.WEBAPP_URL}")
        except Exception as e:
            logger.warning(f"Could not set MenuButtonWebApp: {e}")

    # Start RSS Ingestion Manager
    ingestion = IngestionManager(bot=bot)
    ingestion_task = asyncio.create_task(ingestion.start())

    # Start Embedded WebApp / REST API server
    webapp_app = create_webapp(bot=bot)
    runner = web.AppRunner(webapp_app)
    await runner.setup()
    site = web.TCPSite(runner, settings.WEBAPP_HOST, settings.WEBAPP_PORT)
    await site.start()
    logger.info(
        f"Mini App & REST API server running at http://{settings.WEBAPP_HOST}:{settings.WEBAPP_PORT}"
    )

    logger.info("Starting polling...")
    try:
        await dp.start_polling(bot)
    finally:
        ingestion.stop()
        ingestion_task.cancel()
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
