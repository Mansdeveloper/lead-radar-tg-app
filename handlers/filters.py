from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database.crud import (
    get_user_filters,
    update_user_full_filters,
    update_user_keywords,
    update_user_stop_words,
)
from services.achievements import check_and_grant_achievements

router = Router()


class FilterStates(StatesGroup):
    waiting_for_keywords = State()
    waiting_for_stop_words = State()


def get_filters_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Задать ключевые слова", callback_data="set_keywords"),
                InlineKeyboardButton(text="🚫 Задать стоп-слова", callback_data="set_stop_words"),
            ],
            [
                InlineKeyboardButton(text="🔄 Сбросить все фильтры", callback_data="reset_filters"),
            ],
            [
                InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_menu"),
            ],
        ]
    )


async def render_filters_text(telegram_id: int) -> str:
    user_filter = await get_user_filters(telegram_id)
    kw = user_filter.keywords if user_filter.keywords else "<i>Все заказы подряд</i>"
    sw = user_filter.stop_words if user_filter.stop_words else "<i>Не заданы</i>"
    cats_raw = (user_filter.categories or "all").strip().lower()
    if cats_raw == "none":
        cat_text = "<i>Не выбран (выберите в /start)</i>"
    elif cats_raw == "all":
        cat_text = "<i>Все направления</i>"
    else:
        cat_text = cats_raw

    return (
        "⚙️ <b>Настройки фильтрации заказов</b>\n\n"
        f"📂 <b>Направление:</b> {cat_text}\n"
        f"🔍 <b>Ключевые слова:</b> {kw}\n"
        "<i>(Бот присылает заказы, если в названии или описании есть хотя бы одно из этих слов)</i>\n\n"
        f"🚫 <b>Стоп-слова:</b> {sw}\n"
        "<i>(Заказы с этими словами автоматически отсекаются)</i>\n\n"
        "💡 <i>Пример ключевых слов:</i> <code>python, django, telegram, бот, парсер</code>\n"
        "💡 <i>Пример стоп-слов:</i> <code>курсовая, диплом, бесплатно, бартер</code>"
    )


@router.message(Command("filters"))
@router.callback_query(F.data == "open_filters")
async def show_filters(event: Message | CallbackQuery):
    user_id = event.from_user.id
    text = await render_filters_text(user_id)
    kb = get_filters_keyboard()

    if isinstance(event, CallbackQuery):
        await event.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        await event.answer()
    else:
        await event.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "set_keywords")
async def ask_keywords(callback: CallbackQuery, state: FSMContext):
    await state.set_state(FilterStates.waiting_for_keywords)
    await callback.message.answer(
        "✏️ Отправьте сообщение со списком ключевых слов через запятую:\n"
        "Например: <code>python, fastapi, react, telegram бот</code>\n\n"
        "<i>Чтобы ловить все заказы подряд — отправьте «все»</i>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(FilterStates.waiting_for_keywords)
async def process_keywords(message: Message, state: FSMContext):
    text = message.text.strip()
    if text.lower() in ["все", "all", "всё"]:
        keywords = ""
    else:
        keywords = text

    user_filter = await get_user_filters(message.from_user.id)
    if (user_filter.categories or "").strip().lower() == "none":
        await update_user_full_filters(
            telegram_id=message.from_user.id,
            keywords=keywords,
            stop_words=user_filter.stop_words or "",
            min_budget=user_filter.min_budget or 0,
            categories="all",
        )
    else:
        await update_user_keywords(message.from_user.id, keywords)

    await check_and_grant_achievements(message.from_user.id, bot=message.bot)
    await state.clear()
    await message.answer("✅ Ключевые слова успешно обновлены!")
    await show_filters(message)


@router.callback_query(F.data == "set_stop_words")
async def ask_stop_words(callback: CallbackQuery, state: FSMContext):
    await state.set_state(FilterStates.waiting_for_stop_words)
    await callback.message.answer(
        "🚫 Отправьте список стоп-слов через запятую:\n"
        "Например: <code>курсовая, диплом, бесплатно, реферат, юду</code>\n\n"
        "<i>Чтобы удалить стоп-слова — отправьте «нет»</i>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(FilterStates.waiting_for_stop_words)
async def process_stop_words(message: Message, state: FSMContext):
    text = message.text.strip()
    if text.lower() in ["нет", "none", "очистить"]:
        stop_words = ""
    else:
        stop_words = text

    await update_user_stop_words(message.from_user.id, stop_words)
    await check_and_grant_achievements(message.from_user.id, bot=message.bot)
    await state.clear()
    await message.answer("✅ Стоп-слова успешно обновлены!")
    await show_filters(message)


@router.callback_query(F.data == "reset_filters")
async def reset_filters_handler(callback: CallbackQuery):
    await update_user_full_filters(
        telegram_id=callback.from_user.id,
        keywords="",
        stop_words="",
        min_budget=0,
        categories="all",
    )
    await callback.answer("🔄 Фильтры сброшены", show_alert=True)
    await show_filters(callback)
