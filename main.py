import asyncio
import logging
import sys
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from config import settings
from database.session import init_db
from handlers import filters, help as help_handler, payments, profile, start
from parsers.manager import IngestionManager
from services.promo_notifier import PromoNotifier
from services.trial_notifier import TrialNotifier
from webapp.server import create_webapp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("LeadRadarBot")

BOT_COMMANDS = [
    BotCommand(command="start", description="🚀 Главный радар и статус"),
    BotCommand(command="filters", description="🎯 Настройка стека и фильтров"),
    BotCommand(command="test_lead", description="🧪 Проверить радар (Тестовый лид)"),
    BotCommand(command="premium", description="⚡ Подключить 0 сек (Premium)"),
    BotCommand(command="profile", description="👤 Профиль и рефералы"),
    BotCommand(command="help", description="❓ Помощь и оплата Stars"),
]

BOT_DESCRIPTION = (
    "⚡ Lead Radar — перехват горячих заказов с FL.ru и Freelancehunt в реальном времени.\n\n"
    "🎯 80% заказов забирают те, кто откликнулся в первые 3 минуты.\n\n"
    "Что внутри:\n"
    "• 24ч Welcome Trial бесплатно — мгновенные пуши за 0–2 сек\n"
    "• Генератор готовых откликов под каждый заказ в 1 клик\n"
    "• Умные фильтры по стеку, ключевым словам и стоп-словам\n"
    "• Оплата Premium через Telegram Stars или бонусы за друзей\n\n"
    "Жмите Start, выберите стек и получите первый свежий заказ прямо сейчас!"
)

BOT_SHORT_DESCRIPTION = (
    "⚡ Перехват заказов с фриланс-бирж за 0 сек! 24ч Welcome Trial бесплатно + генератор откликов."
)


async def configure_bot_profile(bot: Bot) -> None:
    """Configure Telegram command menu, bot description, short description, and WebApp button."""
    try:
        await bot.set_my_commands(BOT_COMMANDS)
        logger.info("Telegram bot commands configured.")
    except Exception as e:
        logger.warning(f"Could not set bot commands: {e}")

    try:
        await bot.set_my_description(description=BOT_DESCRIPTION)
        logger.info("Telegram bot description configured.")
    except Exception as e:
        logger.warning(f"Could not set bot description: {e}")

    try:
        await bot.set_my_short_description(short_description=BOT_SHORT_DESCRIPTION)
        logger.info("Telegram bot short description configured.")
    except Exception as e:
        logger.warning(f"Could not set bot short description: {e}")

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
    dp.include_router(help_handler.router)

    await configure_bot_profile(bot)

    # Start RSS Ingestion Manager
    ingestion = IngestionManager(bot=bot)
    ingestion_task = asyncio.create_task(ingestion.start())

    # Start Trial Expiration Notifier Background Service
    trial_notifier = TrialNotifier(bot=bot, check_interval_seconds=60)
    trial_task = asyncio.create_task(trial_notifier.start())

    # Start Promo Notifier Background Service (2h Referral & 48h Reactivation)
    promo_notifier = PromoNotifier(bot=bot, check_interval_seconds=60)
    promo_task = asyncio.create_task(promo_notifier.start())

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
        promo_notifier.stop()
        promo_task.cancel()
        trial_notifier.stop()
        trial_task.cancel()
        ingestion.stop()
        ingestion_task.cancel()
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
