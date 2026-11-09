from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from config import settings
from database.crud import get_or_create_user, get_user, get_user_status, toggle_user_active

router = Router()


async def render_welcome(event: Message | CallbackQuery):
    user = event.from_user
    user_id = user.id
    username = user.username

    # Parse referral code from /start ref_<id> if it's a Message
    referred_by = None
    if isinstance(event, Message) and event.text and len(event.text.split()) > 1:
        param = event.text.split()[1]
        if param.startswith("ref_"):
            try:
                ref_id = int(param.replace("ref_", ""))
                if ref_id != user_id:
                    referred_by = ref_id
            except ValueError:
                pass

    db_user, created = await get_or_create_user(
        telegram_id=user_id,
        username=username,
        referred_by=referred_by,
    )

    status = await get_user_status(user_id)
    is_active = getattr(db_user, "is_active", True)

    if status["is_premium"]:
        sub_text = "⚡ <b>PREMIUM</b> (0 сек задержка, безлимит)"
    elif status["is_trial"]:
        hours = status["trial_hours_left"]
        sub_text = f"⚡ <b>WELCOME TRIAL {hours}ч</b> (0 сек задержка активна)"
    else:
        sub_text = (
            f"⏳ <b>FREE</b> (задержка {settings.FREE_DELAY_SECONDS // 60} мин, "
            f"до {settings.FREE_MAX_DAILY_LEADS} лидов/день)"
        )

    radar_status_text = "🟢 <b>АКТИВЕН</b> (лиды приходят)" if is_active else "⏸ <b>НА ПАУЗЕ</b> (лиды отключены)"

    welcome_text = (
        "👋 <b>Добро пожаловать в Lead Radar!</b>\n\n"
        "⚡ <b>Перехват заказов с фриланс-бирж в реальном времени.</b>\n"
        "Мониторинг FL.ru, Freelancehunt, Хабр Фриланс за 0–2 секунды с момента публикации.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>Тариф:</b> {sub_text}\n"
        f"📡 <b>Статус радара:</b> {radar_status_text}\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "💡 <i>Отклик в первые 3 минуты повышает вероятность взять заказ на 80%.</i>"
    )

    buttons = []
    if settings.WEBAPP_URL:
        buttons.append(
            [InlineKeyboardButton(text="📱 Открыть Mini App", web_app=WebAppInfo(url=settings.WEBAPP_URL))]
        )

    prem_btn_text = "⚡ Закрепить 0 сек" if status["is_trial"] else "⚡ Включить Premium"
    toggle_btn_text = "⏸ Приостановить лиды" if is_active else "▶️ Возобновить лиды"

    buttons.extend([
        [
            InlineKeyboardButton(text="🎯 Фильтры", callback_data="open_filters"),
            InlineKeyboardButton(text=prem_btn_text, callback_data="buy_premium"),
        ],
        [
            InlineKeyboardButton(text=toggle_btn_text, callback_data="toggle_leads"),
        ],
        [
            InlineKeyboardButton(text="👤 Профиль и Рефералы", callback_data="open_profile"),
        ],
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(welcome_text, parse_mode="HTML", reply_markup=keyboard)
        except Exception:
            await event.message.answer(welcome_text, parse_mode="HTML", reply_markup=keyboard)
        await event.answer()
    else:
        await event.answer(welcome_text, parse_mode="HTML", reply_markup=keyboard)


@router.message(CommandStart())
async def cmd_start(message: Message):
    await render_welcome(message)


@router.callback_query(F.data == "toggle_leads")
async def toggle_leads_callback(callback: CallbackQuery):
    new_state = await toggle_user_active(callback.from_user.id)
    toast_msg = "▶️ Радар включен. Лиды снова поступают!" if new_state else "⏸ Радар на паузе. Лиды приостановлены."
    await callback.answer(toast_msg, show_alert=True)
    await render_welcome(callback)


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu_callback(callback: CallbackQuery):
    await render_welcome(callback)
