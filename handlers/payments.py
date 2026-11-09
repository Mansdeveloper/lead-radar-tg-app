from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from config import settings
from database.crud import get_user_status, grant_premium

router = Router()


@router.message(Command("premium"))
@router.callback_query(F.data == "buy_premium")
async def show_premium_offer(event: Message | CallbackQuery):
    user_id = event.from_user.id
    status = await get_user_status(user_id)

    if status["is_premium"]:
        status_str = "активна ⚡"
    elif status["is_trial"]:
        hours = status["trial_hours_left"]
        status_str = f"активен Welcome Trial (осталось ~{hours}ч) ⚡"
    else:
        status_str = "не активна ⏳"

    text = (
        "💎 <b>Lead Radar Premium</b>\n\n"
        f"Ваша подписка: <b>{status_str}</b>\n\n"
        "⚡ <b>Что дает Premium:</b>\n"
        "• <b>Мгновенные алерты</b> (0 сек задержки) — пишите клиенту раньше других!\n"
        "• <b>Безлимит лидов</b> (вместо 3 в день)\n"
        "• Точные фильтры по ключевым словам и стоп-словам\n"
        "• Приоритетная доставка в Telegram\n\n"
        f"💳 <b>Стоимость:</b> {settings.STARS_PRICE} Telegram Stars (~$5) на 30 дней\n"
        "<i>Оплата в 1 клик прямо внутри Telegram!</i>"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"⭐️ Оплатить {settings.STARS_PRICE} Stars", callback_data="pay_stars"
                )
            ],
            [
                InlineKeyboardButton(text="👥 Получить бесплатно за рефералов", callback_data="open_profile")
            ],
            [
                InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu")
            ],
        ]
    )

    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        await event.answer()
    else:
        await event.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "pay_stars")
async def process_stars_invoice(callback: CallbackQuery):
    prices = [LabeledPrice(label="Подписка Lead Radar Premium (30 дней)", amount=settings.STARS_PRICE)]
    
    await callback.bot.send_invoice(
        chat_id=callback.from_user.id,
        title="Lead Radar Premium (30 дней)",
        description="Мгновенные уведомления о новых заказах с бирж фриланса без задержки и лимитов.",
        payload=f"sub_30_days_{callback.from_user.id}",
        currency="XTR",  # Telegram Stars
        prices=prices,
        provider_token="",  # Telegram Stars don't need a third-party provider token
    )
    await callback.answer()


@router.pre_checkout_query()
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    # Always confirm if valid
    await pre_checkout_query.answer(ok=True)


@router.message(F.successful_payment)
async def process_successful_payment(message: Message):
    user_id = message.from_user.id
    payment = message.successful_payment

    # Grant 30 days of premium
    new_until = await grant_premium(user_id, days=30)
    until_str = new_until.strftime("%d.%m.%Y")

    success_text = (
        "🎉 <b>Оплата успешно получена!</b>\n\n"
        "⚡ <b>Lead Radar Premium активирован!</b>\n"
        f"Ваша подписка действует до: <b>{until_str}</b>\n\n"
        "Теперь все новые заказы будут приходить вам мгновенно в первую секунду публикации. Успешных откликов!"
    )
    await message.answer(success_text, parse_mode="HTML")
