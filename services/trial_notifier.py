import asyncio
import logging
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from database.crud import get_users_with_expired_trial, mark_trial_expired_notified

logger = logging.getLogger("TrialNotifier")


class TrialNotifier:
    def __init__(self, bot: Bot, check_interval_seconds: int = 60):
        self.bot = bot
        self.check_interval_seconds = check_interval_seconds
        self._running = False

    async def start(self) -> None:
        self._running = True
        logger.info(f"TrialNotifier service started (interval={self.check_interval_seconds}s)")
        while self._running:
            try:
                await self.check_and_notify_expired_trials()
            except Exception as e:
                logger.error(f"Error checking expired trials: {e}", exc_info=True)

            try:
                await asyncio.sleep(self.check_interval_seconds)
            except asyncio.CancelledError:
                break

    def stop(self) -> None:
        self._running = False
        logger.info("TrialNotifier service stopped")

    async def check_and_notify_expired_trials(self) -> None:
        users = await get_users_with_expired_trial()
        if not users:
            return

        logger.info(f"Found {len(users)} users with expired trial to notify")
        text = (
            "⏳ <b>Ваш Welcome Trial тест 0-секундного радара завершён.</b>\n\n"
            "Теперь новые заказы будут поступать с задержкой 15 минут.\n\n"
            "Чтобы продолжить перехватывать заказы первее конкурентов — включите <b>Premium за 250 Stars</b>."
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⭐️ Оплатить Premium (250 Stars)",
                        callback_data="pay_stars",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💎 Подробнее о тарифе",
                        callback_data="buy_premium",
                    ),
                    InlineKeyboardButton(
                        text="👥 +7 дней за друга",
                        callback_data="open_profile",
                    ),
                ],
            ]
        )

        for user in users:
            try:
                await self.bot.send_message(
                    chat_id=user.telegram_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=kb,
                )
                logger.info(f"Sent trial expiration notification to user {user.telegram_id}")
            except Exception as e:
                logger.warning(
                    f"Failed to send trial expiration notification to {user.telegram_id}: {e}"
                )
            try:
                await mark_trial_expired_notified(user.telegram_id)
            except Exception as e:
                logger.error(
                    f"Failed to mark user {user.telegram_id} as trial_expired_notified: {e}"
                )
