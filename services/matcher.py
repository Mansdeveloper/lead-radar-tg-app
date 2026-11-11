import asyncio
import html
import logging
import re
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
from services.classifier import (
    CATEGORY_ALIASES,
    CATEGORY_MARKERS,
    _CATEGORY_REGEX_CACHE,
    parse_budget_amount,
)

logger = logging.getLogger(__name__)


def matches_filter(
    lead_text: str,
    keywords_str: str = "",
    stop_words_str: str = "",
    min_budget: int = 0,
    categories_str: str = "all",
    lead_budget_str: str = "",
    lead_category: str = "",
) -> bool:
    """
    Evaluates whether a lead matches a user's filter criteria:
    1. Stop words: If any stop word is found in lead_text -> rejected immediately.
    2. Min budget: If user specified min_budget > 0 and lead has a parsed numeric budget,
       reject if parsed budget < min_budget. Orders with negotiable/undefined budget ('По договоренности') pass.
    3. Categories and Keywords: If specific categories are chosen (not 'all' and not empty),
       the lead is matched against category markers or lead.category.
       If keywords are also specified, the lead is accepted if it matches EITHER category OR keyword (OR logic).
    """
    # 1. Check stop words first (with word boundary safety for single words)
    if stop_words_str and stop_words_str.strip():
        stop_words = [w.strip().lower() for w in stop_words_str.split(",") if w.strip()]
        for sw in stop_words:
            if " " in sw or "-" in sw or "/" in sw or "." in sw:
                if sw in lead_text:
                    return False
            else:
                sw_pattern = r"\b" + re.escape(sw) + r"[а-яa-z0-9_-]*"
                if re.search(sw_pattern, lead_text, re.IGNORECASE):
                    return False

    # 2. Check min_budget
    if min_budget > 0:
        amount = parse_budget_amount(lead_budget_str)
        if amount is not None and amount < min_budget:
            return False

    # 3. Check categories & keywords
    has_categories = False
    selected_categories: list[str] = []
    if categories_str and categories_str.strip() and categories_str.strip().lower() != "all":
        raw_cats = [c.strip().lower() for c in categories_str.split(",") if c.strip()]
        if raw_cats and "all" not in raw_cats:
            selected_categories = raw_cats
            has_categories = True

    has_keywords = bool(keywords_str and keywords_str.strip())

    # If neither categories nor keywords specified -> match all leads
    if not has_categories and not has_keywords:
        return True

    matches_category = False
    if has_categories:
        lead_cat_norm = lead_category.strip().lower() if lead_category else ""
        for cat in selected_categories:
            canonical_cat = CATEGORY_ALIASES.get(cat, cat)
            if lead_cat_norm and (lead_cat_norm == cat or lead_cat_norm == canonical_cat):
                matches_category = True
                break
            # Check category regex pattern
            pattern = _CATEGORY_REGEX_CACHE.get(canonical_cat) or _CATEGORY_REGEX_CACHE.get(cat)
            if pattern and pattern.search(lead_text):
                matches_category = True
                break

    matches_keyword = False
    if has_keywords:
        keywords = [w.strip().lower() for w in keywords_str.split(",") if w.strip()]
        for kw in keywords:
            if " " in kw or "-" in kw or "/" in kw or "." in kw:
                if kw in lead_text:
                    matches_keyword = True
                    break
            else:
                kw_pattern = r"\b" + re.escape(kw) + r"[а-яa-z0-9_-]*"
                if re.search(kw_pattern, lead_text, re.IGNORECASE):
                    matches_keyword = True
                    break

    # Combined OR logic if both categories and keywords are defined
    if has_categories and has_keywords:
        return matches_category or matches_keyword
    elif has_categories:
        return matches_category
    else:
        return matches_keyword


def matches_user_filter(lead: RawLead, user_filter) -> bool:
    """Convenience wrapper for matching a RawLead against a UserFilter model."""
    lead_text = f"{lead.title} {lead.description}".lower()
    return matches_filter(
        lead_text=lead_text,
        keywords_str=getattr(user_filter, "keywords", "") or "",
        stop_words_str=getattr(user_filter, "stop_words", "") or "",
        min_budget=getattr(user_filter, "min_budget", 0) or 0,
        categories_str=getattr(user_filter, "categories", "all") or "all",
        lead_budget_str=lead.budget,
        lead_category=lead.category,
    )


def format_lead_message(
    lead: RawLead, is_premium: bool = False, is_trial: bool = False, trial_hours: int = 24
) -> str:
    source_badges = {
        "fl_ru": "🟦 FL.ru",
        "freelancehunt": "🟨 Freelancehunt",
        "kwork": "🟧 Kwork",
    }
    source_name = source_badges.get(lead.source, f"🌐 {lead.source.upper()}")

    if is_premium:
        badge = "⚡ <b>МГНОВЕННЫЙ ЛИД [PREMIUM]</b>"
    elif is_trial:
        badge = f"⚡ <b>МГНОВЕННЫЙ ЛИД [TRIAL {trial_hours}ч]</b>"
    else:
        badge = "⏳ <b>Лид доставлен с задержкой 15 мин [FREE]</b>"

    desc = lead.description.strip()
    if len(desc) > 300:
        desc = desc[:300].rstrip() + "..."

    safe_title = html.escape(lead.title)
    safe_desc = html.escape(desc)
    safe_budget = html.escape(lead.budget)

    divider = "━━━━━━━━━━━━━━━━━━━━"

    message = (
        f"{badge}\n\n"
        f"{source_name}  ·  💰 {safe_budget}\n\n"
        f"{divider}\n\n"
        f"📌 <b>{safe_title}</b>\n\n"
        f"{safe_desc}\n\n"
        f"{divider}\n\n"
    )

    if is_trial:
        trial_desc = f"{trial_hours}-часовому" if trial_hours != 24 else "24-часовому"
        message += (
            f"<i>⚡ Лид доставлен без задержки благодаря {trial_desc} Welcome Trial. "
            "Чтобы закрепить 0-сек скорость навсегда — оформите /premium!</i>"
        )
    elif not is_premium:
        message += (
            "<i>⚠️ Премиум-пользователи получают заказы в первые секунды публикации и уже пишут клиенту. "
            "Отключите задержку в /premium!</i>"
        )

    return message


def build_lead_keyboard(
    lead: RawLead,
    is_premium: bool = False,
    is_trial: bool = False,
    lead_id: int | str | None = None,
) -> InlineKeyboardMarkup:
    target_id = lead_id if lead_id is not None else getattr(lead, "id", None)
    if target_id is not None:
        pitch_cb = f"pitch_{target_id}"
    else:
        ext = getattr(lead, "external_id", "")
        if ext and len(f"pitch_{ext}") <= 64:
            pitch_cb = f"pitch_{ext}"
        else:
            pitch_cb = "pitch_sample"

    buttons = [
        [InlineKeyboardButton(text="🚀 Откликнуться на заказ", url=lead.url)],
        [InlineKeyboardButton(text="💡 Шаблон отклика", callback_data=pitch_cb)],
    ]
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
        kb = build_lead_keyboard(
            lead, is_premium=False, is_trial=False, lead_id=getattr(lead, "id", None)
        )
        await bot.send_message(chat_id=user_tg_id, text=text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        logger.error(f"Error sending delayed lead to {user_tg_id}: {e}")


async def dispatch_lead(bot: Bot, lead: RawLead) -> None:
    subscribers = await get_all_subscribers_with_filters()

    for user, user_filter in subscribers:
        if not matches_user_filter(lead, user_filter):
            continue

        status = compute_user_status(user)

        if status["has_instant_delivery"]:
            # Send immediately! (either Premium or 24h Welcome Trial)
            is_prem = status["is_premium"]
            is_tr = status["is_trial"]
            trial_hrs = status.get("trial_hours_left", 24)
            text = format_lead_message(lead, is_premium=is_prem, is_trial=is_tr, trial_hours=trial_hrs)
            kb = build_lead_keyboard(
                lead, is_premium=is_prem, is_trial=is_tr, lead_id=getattr(lead, "id", None)
            )
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
