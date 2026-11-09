import asyncio
import logging
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import settings
from database.crud import (
    can_send_free_lead,
    compute_user_status,
    get_all_subscribers_with_filters,
    get_user_status,
)
from parsers.base import RawLead

logger = logging.getLogger(__name__)


def matches_filter(lead_text: str, keywords_str: str, stop_words_str: str) -> bool:
    # 1. Check stop words first
    if stop_words_str.strip():
        stop_words = [w.strip().lower() for w in stop_words_str.split(",") if w.strip()]
        for sw in stop_words:
            if sw in lead_text:
                return False

    # 2. Check keywords
    if not keywords_str.strip():
        return True  # If no keywords, match everything in allowed category

    keywords = [w.strip().lower() for w in keywords_str.split(",") if w.strip()]
    for kw in keywords:
        if kw in lead_text:
            return True

    return False


def format_lead_message(
    lead: RawLead, is_premium: bool = False, is_trial: bool = False
) -> str:
    source_badges = {
        "fl_ru": "🟦 FL.ru",
        "freelancehunt": "🟨 Freelancehunt",
        "habr": "🟩 Хабр Фриланс",
        "kwork": "🟧 Kwork",
    }
    source_name = source_badges.get(lead.source, f"🌐 {lead.source.upper()}")

    if is_premium:
        badge = "⚡ <b>МГНОВЕННЫЙ ЛИД [PREMIUM]</b>"
    elif is_trial:
        badge = "⚡ <b>МГНОВЕННЫЙ ЛИД [TRIAL 24ч]</b>"
    else:
        badge = "⏳ <b>Лид доставлен с задержкой 15 мин [FREE]</b>"

    message = (
        f"{badge}\n"
        f"<b>Источник:</b> {source_name}\n"
        f"<b>Бюджет:</b> 💰 {lead.budget}\n\n"
        f"📌 <b>{lead.title}</b>\n\n"
        f"{lead.description}\n\n"
    )

    if is_trial:
        message += (
            "<i>⚡ Лид доставлен без задержки благодаря 24-часовому Welcome Trial. "
            "Чтобы закрепить 0-сек скорость навсегда — оформите /premium!</i>"
        )
    elif not is_premium:
        message += (
            "<i>⚠️ Премиум-пользователи получают заказы в первые секунды публикации и уже пишут клиенту. "
            "Отключите задержку в /premium!</i>"
        )

    return message


def build_lead_keyboard(
    lead: RawLead, is_premium: bool = False, is_trial: bool = False
) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text="🚀 Откликнуться на заказ", url=lead.url)]]
    if not is_premium:
        btn_text = "⚡ Закрепить 0 сек (Premium)" if is_trial else "⚡ Убрать задержку (Premium)"
        buttons.append(
            [InlineKeyboardButton(text=btn_text, callback_data="buy_premium")]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def send_delayed_lead(bot: Bot, user_tg_id: int, lead: RawLead, delay_seconds: int):
    try:
        await asyncio.sleep(delay_seconds)
        # Check if user upgraded to premium or activated trial in the meantime
        status = await get_user_status(user_tg_id)
        if status["has_instant_delivery"]:
            return  # They will get or already got instant leads

        text = format_lead_message(lead, is_premium=False, is_trial=False)
        kb = build_lead_keyboard(lead, is_premium=False, is_trial=False)
        await bot.send_message(chat_id=user_tg_id, text=text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error(f"Error sending delayed lead to {user_tg_id}: {e}")


async def dispatch_lead(bot: Bot, lead: RawLead) -> None:
    subscribers = await get_all_subscribers_with_filters()
    lead_text = f"{lead.title} {lead.description}".lower()

    for user, user_filter in subscribers:
        if not matches_filter(lead_text, user_filter.keywords, user_filter.stop_words):
            continue

        status = compute_user_status(user)

        if status["has_instant_delivery"]:
            # Send immediately! (either Premium or 24h Welcome Trial)
            is_prem = status["is_premium"]
            is_tr = status["is_trial"]
            text = format_lead_message(lead, is_premium=is_prem, is_trial=is_tr)
            kb = build_lead_keyboard(lead, is_premium=is_prem, is_trial=is_tr)
            try:
                await bot.send_message(
                    chat_id=user.telegram_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=kb,
                )
            except Exception as e:
                logger.error(f"Failed to send instant lead to {user.telegram_id}: {e}")
        else:
            # Free user: check daily limit and schedule delayed send
            can_send = await can_send_free_lead(
                user.telegram_id, max_daily=settings.FREE_MAX_DAILY_LEADS
            )
            if can_send:
                asyncio.create_task(
                    send_delayed_lead(
                        bot=bot,
                        user_tg_id=user.telegram_id,
                        lead=lead,
                        delay_seconds=settings.FREE_DELAY_SECONDS,
                    )
                )
