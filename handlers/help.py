import html
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import settings

router = Router()


def build_help_text() -> str:
    if settings.SUPPORT_CONTACT:
        support_line = f"💬 <b>Поддержка:</b> {html.escape(settings.SUPPORT_CONTACT)}"
    else:
        admin_ids = sorted(settings.get_admin_ids())
        support_id = admin_ids[0] if admin_ids else 875592041
        support_line = (
            f'💬 <b>Поддержка:</b> <a href="tg://user?id={support_id}">Написать администратору</a>'
        )

    return (
        "❓ <b>Помощь по Lead Radar и оплата Stars</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📡 <b>Как работает радар:</b>\n"
        "1. <b>Мгновенный перехват:</b> бот сканирует FL.ru и Freelancehunt каждые 60 сек. "
        "На Welcome Trial (24ч) и Premium лиды прилетают за <b>0 сек</b>, на Free — с задержкой 15 мин.\n"
        "2. <b>Настройка фильтров:</b> перейдите в /filters или Mini App, выберите стек, "
        "задайте ключевые слова и добавьте стоп-слова (<code>диплом, курсовая, бесплатно</code>), "
        "чтобы отсекать мусор.\n"
        "3. <b>Генератор откликов:</b> под каждым лидом нажмите <b>«💡 Шаблон отклика»</b> — "
        "бот сформирует готовый отклик под задачу. Нажмите на блок текста, чтобы скопировать в 1 тап.\n"
        "4. <b>Проверка работы:</b> команда /test_lead мгновенно пришлёт свежий заказ по вашим фильтрам.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⭐️ <b>Как купить Telegram Stars за 30 секунд (для РФ):</b>\n"
        "• <b>Способ 1 (самый быстрый — @PremiumBot):</b>\n"
        "  1) Откройте официальный бот @PremiumBot в Telegram.\n"
        "  2) Нажмите <b>«Купить звёзды»</b> (Telegram Stars) и выберите количество "
        f"(от <b>{settings.STARS_PRICE} ⭐️</b>).\n"
        "  3) Оплатите российской картой (МИР / СБП) через встроенную форму.\n"
        "• <b>Способ 2 (Fragment):</b> купите Stars на <code>fragment.com</code> за TON без ограничений.\n"
        "• <b>Способ 3 (в приложении Telegram):</b> <i>Настройки → Мои звёзды</i> через Apple Pay / Google Pay.\n\n"
        "После пополнения баланса нажмите /premium и активируйте подписку в 1 клик!\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"{support_line}"
    )


def build_help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🎯 Настроить фильтры", callback_data="open_filters"),
                InlineKeyboardButton(text="🧪 Тестовый лид", callback_data="test_lead"),
            ],
            [
                InlineKeyboardButton(
                    text=f"⭐️ Оформить Premium ({settings.STARS_PRICE} Stars)",
                    callback_data="buy_premium",
                )
            ],
            [
                InlineKeyboardButton(text="🔙 В главное меню", callback_data="back_to_menu"),
            ],
        ]
    )


@router.message(Command("help"))
@router.callback_query(F.data == "open_help")
async def show_help(event: Message | CallbackQuery):
    text = build_help_text()
    kb = build_help_keyboard()

    if isinstance(event, CallbackQuery):
        try:
            await event.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=kb,
                disable_web_page_preview=True,
            )
        except Exception:
            await event.message.answer(
                text,
                parse_mode="HTML",
                reply_markup=kb,
                disable_web_page_preview=True,
            )
        await event.answer()
    else:
        await event.answer(
            text,
            parse_mode="HTML",
            reply_markup=kb,
            disable_web_page_preview=True,
        )
