from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import settings
from database.crud import get_user, get_user_status, toggle_user_active

router = Router()


@router.message(Command("profile"))
@router.callback_query(F.data == "open_profile")
async def show_profile(event: Message | CallbackQuery):
    user_id = event.from_user.id
    try:
        from services.achievements import check_and_grant_achievements
        await check_and_grant_achievements(user_id, bot=event.bot)
    except Exception:
        pass

    user = await get_user(user_id)
    status = await get_user_status(user_id)

    bot_info = await event.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"

    if status["is_premium"] and user and user.premium_until:
        sub_status = f"⚡ <b>PREMIUM</b> (до {user.premium_until.strftime('%d.%m.%Y')})"
    elif status["is_trial"]:
        hours = status["trial_hours_left"]
        sub_status = f"⚡ <b>WELCOME TRIAL</b> (~{hours}ч 0-сек задержки)"
    else:
        sub_status = f"⏳ <b>FREE</b> (задержка {settings.FREE_DELAY_SECONDS // 60} мин)"

    is_active = getattr(user, "is_active", True) if user else True
    radar_status = "🟢 <b>АКТИВЕН</b>" if is_active else "⏸ <b>НА ПАУЗЕ</b>"
    ref_count = user.referral_count if user else 0

    text = (
        "👤 <b>Профиль фрилансера</b>\n\n"
        f"<b>ID:</b> <code>{user_id}</code>\n"
        f"<b>Тариф:</b> {sub_status}\n"
        f"<b>Радар:</b> {radar_status}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Приглашено коллег:</b> {ref_count}\n"
        f"🎁 <b>Награда:</b> <b>+{settings.REFERRAL_PREMIUM_DAYS} дней Premium</b> за каждого друга,\n"
        f"а друг получает <b>{settings.TRIAL_HOURS_REFERRAL}ч Турбо-режима</b>!\n\n"
        f"🔗 <b>Реферальная ссылка:</b>\n"
        f"<code>{ref_link}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )

    share_url = f"https://t.me/share/url?url={ref_link}&text=Перехватывай%20горячие%20заказы%20на%20фрилансе%20быстрее%20всех!"
    toggle_text = "⏸ Поставить на паузу" if is_active else "▶️ Включить получение"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Поделиться ссылкой", url=share_url)],
            [
                InlineKeyboardButton(text="🧪 Тестовый лид", callback_data="test_lead"),
                InlineKeyboardButton(text=toggle_text, callback_data="toggle_leads"),
            ],
            [
                InlineKeyboardButton(text="💎 Premium", callback_data="buy_premium"),
                InlineKeyboardButton(text="❓ Помощь / Stars", callback_data="open_help"),
            ],
            [
                InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_menu"),
            ],
        ]
    )

    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        await event.answer()
    else:
        await event.answer(text, parse_mode="HTML", reply_markup=kb)
