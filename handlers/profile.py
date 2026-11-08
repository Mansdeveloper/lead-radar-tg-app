from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import settings
from database.crud import get_user, is_user_premium

router = Router()


@router.message(Command("profile"))
@router.callback_query(F.data == "open_profile")
async def show_profile(event: Message | CallbackQuery):
    user_id = event.from_user.id
    user = await get_user(user_id)
    is_prem = await is_user_premium(user_id)

    bot_info = await event.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start=ref_{user_id}"

    if is_prem and user and user.premium_until:
        sub_status = f"⚡ <b>PREMIUM</b> (до {user.premium_until.strftime('%d.%m.%Y')})"
    else:
        sub_status = "⏳ <b>FREE</b> (задержка 15 мин, до 3 лидов/день)"

    ref_count = user.referral_count if user else 0

    text = (
        "👤 <b>Ваш профиль в Lead Radar</b>\n\n"
        f"<b>ID:</b> <code>{user_id}</code>\n"
        f"<b>Тариф:</b> {sub_status}\n\n"
        f"👥 <b>Приглашено коллег:</b> {ref_count}\n"
        f"🎁 <b>Бонус:</b> За каждого приглашенного коллегу вы получаете <b>+{settings.REFERRAL_PREMIUM_DAYS} дней Premium бесплатно</b>!\n\n"
        f"🔗 <b>Ваша реферальная ссылка:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        "<i>Отправьте эту ссылку коллеге или в чат фрилансеров — когда он запустит бота, вам автоматически начислится Premium!</i>"
    )

    share_text = f"Я перехватываю горячие заказы на фрилансе через Lead Radar: {ref_link}"
    share_url = f"https://t.me/share/url?url={ref_link}&text=Лови%20горячие%20заказы%20на%20фрилансе%20быстрее%20всех!"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Поделиться с другом", url=share_url)],
            [InlineKeyboardButton(text="⚡ Купить Premium (Stars)", callback_data="buy_premium")],
            [InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_menu")],
        ]
    )

    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        await event.answer()
    else:
        await event.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu_handler(callback: CallbackQuery):
    from handlers.start import cmd_start
    await callback.message.delete()
    await cmd_start(callback.message)
    await callback.answer()
