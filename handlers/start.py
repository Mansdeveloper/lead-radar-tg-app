from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from config import settings
from database.crud import get_or_create_user, get_user_status

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
    if status["is_premium"]:
        sub_text = "⚡ <b>PREMIUM</b> (0 сек задержка, безлимит)"
    elif status["is_trial"]:
        hours = status["trial_hours_left"]
        sub_text = f"⚡ <b>WELCOME TRIAL 24ч: 0 сек задержка АКТИВНА</b> (осталось ~{hours}ч)!"
    else:
        sub_text = (
            f"⏳ <b>FREE</b> (задержка {settings.FREE_DELAY_SECONDS // 60} мин, "
            f"до {settings.FREE_MAX_DAILY_LEADS} лидов/день)"
        )

    welcome_text = (
        "👋 <b>Добро пожаловать в Lead Radar!</b>\n\n"
        "⚡ <b>Что делает бот:</b>\n"
        "В реальном времени сканирует биржи фриланса (Хабр Фриланс, FL.ru, Freelancehunt) и присылает горячие заказы "
        "прямо вам в Telegram за пару секунд с момента их публикации заказчиком.\n\n"
        "🎯 <b>Во фрилансе побеждает первый:</b> отклик в первые 3 минуты повышает вероятность взять заказ на 80%.\n\n"
        f"📊 <b>Ваш статус:</b> {sub_text}\n\n"
    )

    if status["is_trial"]:
        welcome_text += (
            "🎁 <b>Вам активирован 24-часовой Welcome Trial!</b>\n"
            "Все новые заказы будут поступать вам <b>мгновенно (0 секунд задержки)</b>, "
            "чтобы вы оценили преимущество перехвата заказов первее конкурентов.\n\n"
        )

    welcome_text += (
        "🚀 <i>Хотите закрепить мгновенные уведомления без задержки и безлимит?</i>\n"
        "Оформите <b>Premium</b> или пригласите коллегу по реферальной ссылке и получите <b>+7 дней Premium бесплатно</b>!"
    )

    buttons = []
    if settings.WEBAPP_URL:
        buttons.append(
            [InlineKeyboardButton(text="📱 Открыть Mini App", web_app=WebAppInfo(url=settings.WEBAPP_URL))]
        )

    prem_btn_text = "⚡ Закрепить 0 сек" if status["is_trial"] else "⚡ Включить Premium"

    buttons.extend([
        [
            InlineKeyboardButton(text="🎯 Настроить фильтры", callback_data="open_filters"),
            InlineKeyboardButton(text=prem_btn_text, callback_data="buy_premium"),
        ],
        [
            InlineKeyboardButton(text="👤 Мой профиль / Рефералы", callback_data="open_profile"),
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


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu_callback(callback: CallbackQuery):
    await render_welcome(callback)
