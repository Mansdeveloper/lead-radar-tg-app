from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from database.crud import (
    get_user_filters,
    update_user_keywords,
    update_user_stop_words,
)

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

    return (
        "⚙️ <b>Настройки фильтрации заказов</b>\n\n"
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

    await update_user_keywords(message.from_user.id, keywords)
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
    await state.clear()
    await message.answer("✅ Стоп-слова успешно обновлены!")
    await show_filters(message)


@router.callback_query(F.data == "reset_filters")
async def reset_filters_handler(callback: CallbackQuery):
    await update_user_keywords(callback.from_user.id, "")
    await update_user_stop_words(callback.from_user.id, "")
    await callback.answer("🔄 Фильтры сброшены", show_alert=True)
    await show_filters(callback)
