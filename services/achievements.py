import logging
from datetime import date, datetime, timedelta, timezone
from aiogram import Bot
from sqlalchemy import select
from database.crud import add_bonus_hours_to_user, get_user, get_user_filters
from database.models import Achievement, User, UserFilter
from database.session import async_session

logger = logging.getLogger("Achievements")

ACHIEVEMENTS_CONFIG = {
    "first_step": {
        "title": "Первый шаг",
        "icon": "🚀",
        "description": "Настроил персональные фильтры заказов",
        "reward_text": "+2 часа турбо-доступа",
        "bonus_hours": 2,
    },
    "sniper": {
        "title": "Снайпер",
        "icon": "🎯",
        "description": "Откликнулся на 5 горячих заказов",
        "reward_text": "+4 часа Premium",
        "bonus_hours": 4,
    },
    "networker": {
        "title": "Нетворкер",
        "icon": "👥",
        "description": "Пригласил первого коллегу во фрилансе",
        "reward_text": "+7 дней Premium",
        "bonus_hours": 0,  # Already rewarded in referral logic
    },
    "on_fire": {
        "title": "На волне",
        "icon": "🔥",
        "description": "Использовал радар 3 дня подряд",
        "reward_text": "+12 часов Premium",
        "bonus_hours": 12,
    },
    "og_user": {
        "title": "OG Фрилансер",
        "icon": "💎",
        "description": "С нами более 14 дней в поиске заказов",
        "reward_text": "+24 часа Premium",
        "bonus_hours": 24,
    },
    "recruiter": {
        "title": "Топ-Рекрутер",
        "icon": "🏆",
        "description": "Пригласил 5 коллег по реферальной ссылке",
        "reward_text": "+30 дней Premium",
        "bonus_hours": 24 * 30,
    },
}


async def get_user_achievements(user_id: int) -> list[str]:
    async with async_session() as session:
        res = await session.execute(
            select(Achievement.achievement_key).where(
                Achievement.user_telegram_id == user_id
            )
        )
        return list(res.scalars().all())


async def check_and_grant_achievements(user_id: int, bot: Bot | None = None) -> list[str]:
    """Check all milestone criteria and award newly unlocked achievements"""
    user = await get_user(user_id)
    if not user:
        return []

    unlocked_keys = set(await get_user_achievements(user_id))
    user_filter = await get_user_filters(user_id)

    now = datetime.now(timezone.utc)
    newly_unlocked = []

    # 1. First step
    if "first_step" not in unlocked_keys:
        has_kw = bool(user_filter.keywords and user_filter.keywords.strip())
        has_sw = bool(user_filter.stop_words and user_filter.stop_words.strip())
        has_budget = bool(user_filter.min_budget and user_filter.min_budget > 0)
        if has_kw or has_sw or has_budget:
            newly_unlocked.append("first_step")

    # 2. Sniper (5 leads clicked)
    if "sniper" not in unlocked_keys:
        if (user.total_leads_clicked or 0) >= 5:
            newly_unlocked.append("sniper")

    # 3. Networker (1 referral)
    if "networker" not in unlocked_keys:
        if (user.referral_count or 0) >= 1:
            newly_unlocked.append("networker")

    # 4. On fire (3 consecutive active days)
    if "on_fire" not in unlocked_keys:
        if (user.consecutive_active_days or 0) >= 3:
            newly_unlocked.append("on_fire")

    # 5. OG user (14+ days since registration)
    if "og_user" not in unlocked_keys:
        user_created = user.created_at
        if user_created:
            if user_created.tzinfo is None:
                user_created = user_created.replace(tzinfo=timezone.utc)
            if (now - user_created).total_seconds() >= 14 * 86400:
                newly_unlocked.append("og_user")

    # 6. Recruiter (5 referrals)
    if "recruiter" not in unlocked_keys:
        if (user.referral_count or 0) >= 5:
            newly_unlocked.append("recruiter")

    if not newly_unlocked:
        return []

    async with async_session() as session:
        for key in newly_unlocked:
            ach = Achievement(
                user_telegram_id=user_id,
                achievement_key=key,
                unlocked_at=now,
            )
            session.add(ach)
        await session.commit()

    # Apply bonus rewards and send congratulations if bot is available
    for key in newly_unlocked:
        cfg = ACHIEVEMENTS_CONFIG.get(key, {})
        bonus = cfg.get("bonus_hours", 0)
        if bonus > 0:
            await add_bonus_hours_to_user(user_id, bonus)

        if bot:
            try:
                title = cfg.get("title", key)
                icon = cfg.get("icon", "🎖")
                desc = cfg.get("description", "")
                reward = cfg.get("reward_text", "")
                text = (
                    f"🎉 <b>Разблокировано достижение!</b>\n\n"
                    f"{icon} <b>{title}</b>\n"
                    f"<i>{desc}</i>\n\n"
                    f"🎁 <b>Награда:</b> {reward} успешно начислена!"
                )
                await bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"Could not send achievement notification to {user_id}: {e}")

    return newly_unlocked


async def get_achievements_payload(user_id: int) -> dict:
    """Format achievements data for Mini App API"""
    user = await get_user(user_id)
    unlocked_keys = set(await get_user_achievements(user_id))

    total_clicks = (user.total_leads_clicked or 0) if user else 0
    consec_days = (user.consecutive_active_days or 0) if user else 0
    ref_count = (user.referral_count or 0) if user else 0

    items = []
    for key, cfg in ACHIEVEMENTS_CONFIG.items():
        is_unlocked = key in unlocked_keys
        progress = 0
        total_req = 1

        if key == "first_step":
            progress = 1 if is_unlocked else 0
            total_req = 1
        elif key == "sniper":
            progress = min(total_clicks, 5)
            total_req = 5
        elif key == "networker":
            progress = min(ref_count, 1)
            total_req = 1
        elif key == "on_fire":
            progress = min(consec_days, 3)
            total_req = 3
        elif key == "og_user":
            if user:
                created = user.created_at.replace(tzinfo=timezone.utc) if user.created_at.tzinfo is None else user.created_at
                days = max(0, int((datetime.now(timezone.utc) - created).total_seconds() // 86400))
                progress = min(days, 14)
            total_req = 14
        elif key == "recruiter":
            progress = min(ref_count, 5)
            total_req = 5

        items.append({
            "key": key,
            "title": cfg["title"],
            "icon": cfg["icon"],
            "description": cfg["description"],
            "reward_text": cfg["reward_text"],
            "unlocked": is_unlocked,
            "progress": progress,
            "total": total_req,
        })

    return {
        "unlocked_count": len(unlocked_keys),
        "total_count": len(ACHIEVEMENTS_CONFIG),
        "achievements": items,
    }
