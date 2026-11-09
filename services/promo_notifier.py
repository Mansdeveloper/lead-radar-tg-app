import asyncio
import logging
from urllib.parse import quote
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from config import settings
from database.crud import (
    get_leads_count_last_hours,
    get_user_filters,
    get_users_for_reactivation_promo,
    get_users_for_referral_promo,
    mark_promo_reactivation_sent,
    mark_promo_referral_sent,
)

logger = logging.getLogger("PromoNotifier")


class PromoNotifier:
    def __init__(self, bot: Bot, check_interval_seconds: int = 60):
        self.bot = bot
        self.check_interval_seconds = check_interval_seconds
        self._running = False
        self._bot_username: str | None = None

    async def _get_username(self) -> str:
        if not self._bot_username:
            try:
                me = await self.bot.get_me()
                self._bot_username = me.username or "fast_leadss_bot"
            except Exception:
                self._bot_username = "fast_leadss_bot"
        return self._bot_username

    async def start(self) -> None:
        self._running = True
        logger.info(f"PromoNotifier service started (interval={self.check_interval_seconds}s)")
        while self._running:
            try:
                await self.check_and_send_referral_promo()
                await self.check_and_send_reactivation_promo()
            except Exception as e:
                logger.error(f"Error in PromoNotifier iteration: {e}", exc_info=True)

            try:
                await asyncio.sleep(self.check_interval_seconds)
            except asyncio.CancelledError:
                break

    def stop(self) -> None:
        self._running = False
        logger.info("PromoNotifier service stopped")

    async def check_and_send_referral_promo(self) -> None:
        users = await get_users_for_referral_promo()
        if not users:
            return

        bot_username = await self._get_username()
        logger.info(f"Found {len(users)} users for 2h referral promo")

        for user in users:
            try:
                ref_link = f"https://t.me/{bot_username}?start=ref_{user.telegram_id}"
                share_text = quote(
                    "Лови заказы с фриланса быстрее всех в Telegram: 48 часов турбо-режима бесплатно!"
                )
                share_url = f"https://t.me/share/url?url={ref_link}&text={share_text}"

                text = (
                    "👥 <b>Пригласи коллегу — оба получаете супер-бонус!</b>\n\n"
                    f"Ты получаешь <b>+{settings.REFERRAL_PREMIUM_DAYS} дней Premium</b> за каждого приглашенного,\n"
                    f"а твой друг — <b>{settings.TRIAL_HOURS_REFERRAL} часа</b> бесплатного турбо-режима (0 сек задержки)!\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔗 <b>Твоя персональная ссылка:</b>\n"
                    f"<code>{ref_link}</code>\n"
                    "━━━━━━━━━━━━━━━━━━━━\n\n"
                    "<i>Отправь ссылку в чат разработчиков или коллеге — бонус начислится сразу при запуске бота.</i>"
                )

                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="📢 Поделиться ссылкой", url=share_url)],
                        [InlineKeyboardButton(text="👤 Профиль и Рефералы", callback_data="open_profile")],
                    ]
                )

                await self.bot.send_message(
                    chat_id=user.telegram_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=kb,
                )
                logger.info(f"Sent referral promo to user {user.telegram_id}")
            except Exception as e:
                logger.warning(f"Failed to send referral promo to {user.telegram_id}: {e}")

            try:
                await mark_promo_referral_sent(user.telegram_id)
            except Exception as e:
                logger.error(f"Failed to mark referral promo for {user.telegram_id}: {e}")

    async def check_and_send_reactivation_promo(self) -> None:
        users = await get_users_for_reactivation_promo()
        if not users:
            return

        total_recent_leads = await get_leads_count_last_hours(48)
        logger.info(f"Found {len(users)} users for 48h reactivation promo (total_leads={total_recent_leads})")

        for user in users:
            try:
                user_filter = await get_user_filters(user.telegram_id)
                direction_count = await get_leads_count_last_hours(48, keywords=user_filter.keywords)
                missed_count = max(direction_count, 15) if user_filter.keywords else max(total_recent_leads, 45)

                text = (
                    f"💡 <b>За последние 48 часов на биржах появилось {missed_count} заказов по вашему направлению!</b>\n\n"
                    "Многие из них уже закрыты исполнителями, которые откликнулись в первые 3 минуты.\n\n"
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "⚡ <b>Не упускайте проекты:</b> включите мгновенный перехват или оформите <b>Premium за 250 Stars</b>, "
                    "чтобы забирать лучшие заказы без задержки."
                )

                buttons = []
                if settings.WEBAPP_URL:
                    buttons.append(
                        [InlineKeyboardButton(text="🚀 Открыть радар заказов", web_app=WebAppInfo(url=settings.WEBAPP_URL))]
                    )

                buttons.extend([
                    [
                        InlineKeyboardButton(text="⚡ Включить Premium (Stars)", callback_data="buy_premium"),
                    ],
                    [
                        InlineKeyboardButton(text="🎯 Проверить фильтры", callback_data="open_filters"),
                    ],
                ])

                kb = InlineKeyboardMarkup(inline_keyboard=buttons)

                await self.bot.send_message(
                    chat_id=user.telegram_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=kb,
                )
                logger.info(f"Sent reactivation promo to user {user.telegram_id}")
            except Exception as e:
                logger.warning(f"Failed to send reactivation promo to {user.telegram_id}: {e}")

            try:
                await mark_promo_reactivation_sent(user.telegram_id)
            except Exception as e:
                logger.error(f"Failed to mark reactivation promo for {user.telegram_id}: {e}")
