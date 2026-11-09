import math
from datetime import date, datetime, timedelta, timezone
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import Lead, User, UserFilter
from database.session import async_session


async def get_or_create_user(
    telegram_id: int, username: str | None = None, referred_by: int | None = None
) -> tuple[User, bool]:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        created = False
        now = datetime.now(timezone.utc)
        if not user:
            user = User(
                telegram_id=telegram_id,
                username=username,
                referred_by=referred_by if referred_by != telegram_id else None,
                trial_until=now + timedelta(hours=24),
                trial_expired_notified=False,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            created = True

            # If user had a valid referrer, increment referrer count and add bonus days
            if referred_by and referred_by != telegram_id:
                ref_res = await session.execute(
                    select(User).where(User.telegram_id == referred_by)
                )
                referrer = ref_res.scalar_one_or_none()
                if referrer:
                    referrer.referral_count += 1
                    current_until = referrer.premium_until
                    if current_until is not None and current_until.tzinfo is None:
                        current_until = current_until.replace(tzinfo=timezone.utc)
                    if current_until is None or current_until < now:
                        current_until = now
                    referrer.premium_until = current_until + timedelta(days=7)
                    referrer.is_premium = True
                    referrer.trial_expired_notified = True
                    await session.commit()
        else:
            # Grant trial if existing user never had trial_until initialized and is not premium
            if user.trial_until is None and not user.is_premium and not user.trial_expired_notified:
                user.trial_until = now + timedelta(hours=24)
                await session.commit()
                await session.refresh(user)

        # Ensure filter exists for user
        fil_res = await session.execute(
            select(UserFilter).where(UserFilter.user_telegram_id == telegram_id)
        )
        if not fil_res.scalar_one_or_none():
            new_filter = UserFilter(user_telegram_id=telegram_id)
            session.add(new_filter)
            await session.commit()

        return user, created


async def get_user(telegram_id: int) -> User | None:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        return result.scalar_one_or_none()


def compute_user_status(user: User | None) -> dict:
    """Synchronously compute status from user model instance with 0 DB overhead"""
    if not user:
        return {
            "is_premium": False,
            "is_trial": False,
            "trial_hours_left": 0,
            "has_instant_delivery": False,
        }

    now = datetime.now(timezone.utc)

    # 1. Check paid premium
    is_prem = False
    if user.premium_until:
        target_p = user.premium_until
        if target_p.tzinfo is None:
            target_p = target_p.replace(tzinfo=timezone.utc)
        is_prem = target_p > now
    elif user.is_premium:
        is_prem = True

    # 2. Check 24h Welcome Trial
    is_trial = False
    trial_hours_left = 0
    if not is_prem and user.trial_until:
        target_t = user.trial_until
        if target_t.tzinfo is None:
            target_t = target_t.replace(tzinfo=timezone.utc)
        if target_t > now:
            is_trial = True
            trial_hours_left = max(1, int(math.ceil((target_t - now).total_seconds() / 3600)))

    return {
        "is_premium": is_prem,
        "is_trial": is_trial,
        "trial_hours_left": trial_hours_left,
        "has_instant_delivery": is_prem or is_trial,
    }


async def get_user_status(telegram_id: int) -> dict:
    """Returns is_premium, is_trial, trial_hours_left, has_instant_delivery"""
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        return compute_user_status(user)


async def is_user_premium(telegram_id: int) -> bool:
    """Returns True if user has active paid premium"""
    status = await get_user_status(telegram_id)
    return status["is_premium"]


async def is_turbo_active(telegram_id: int) -> bool:
    """Returns True if user has 0-sec instant delivery (paid premium OR active 24h trial)"""
    status = await get_user_status(telegram_id)
    return status["has_instant_delivery"]


async def grant_premium(telegram_id: int, days: int) -> datetime:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if not user:
            user = User(telegram_id=telegram_id)
            session.add(user)

        now = datetime.now(timezone.utc)
        current = user.premium_until
        if current is not None and current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)

        if current is None or current < now:
            current = now

        new_until = current + timedelta(days=days)
        user.premium_until = new_until
        user.is_premium = True
        user.trial_expired_notified = True
        await session.commit()
        return new_until


async def can_send_free_lead(telegram_id: int, max_daily: int) -> bool:
    today_str = date.today().isoformat()
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if not user:
            return False

        if user.last_lead_date != today_str:
            user.last_lead_date = today_str
            user.daily_leads_sent = 1
            await session.commit()
            return True

        if user.daily_leads_sent < max_daily:
            user.daily_leads_sent += 1
            await session.commit()
            return True

        return False


async def get_user_filters(telegram_id: int) -> UserFilter:
    async with async_session() as session:
        result = await session.execute(
            select(UserFilter).where(UserFilter.user_telegram_id == telegram_id)
        )
        user_filter = result.scalar_one_or_none()
        if not user_filter:
            user_filter = UserFilter(user_telegram_id=telegram_id)
            session.add(user_filter)
            await session.commit()
            await session.refresh(user_filter)
        return user_filter


async def update_user_keywords(telegram_id: int, keywords: str) -> None:
    async with async_session() as session:
        await session.execute(
            update(UserFilter)
            .where(UserFilter.user_telegram_id == telegram_id)
            .values(keywords=keywords)
        )
        await session.commit()


async def update_user_stop_words(telegram_id: int, stop_words: str) -> None:
    async with async_session() as session:
        await session.execute(
            update(UserFilter)
            .where(UserFilter.user_telegram_id == telegram_id)
            .values(stop_words=stop_words)
        )
        await session.commit()


async def save_lead(
    source: str,
    external_id: str,
    title: str,
    description: str,
    url: str,
    budget: str,
    category: str,
    published_at: datetime,
) -> bool:
    """Returns True if inserted, False if duplicate"""
    async with async_session() as session:
        existing = await session.execute(
            select(Lead).where(Lead.external_id == external_id)
        )
        if existing.scalar_one_or_none():
            return False

        lead = Lead(
            source=source,
            external_id=external_id,
            title=title,
            description=description,
            url=url,
            budget=budget,
            category=category,
            published_at=published_at,
        )
        session.add(lead)
        await session.commit()
        return True


async def get_all_subscribers_with_filters() -> list[tuple[User, UserFilter]]:
    async with async_session() as session:
        query = select(User, UserFilter).join(
            UserFilter, User.telegram_id == UserFilter.user_telegram_id
        )
        result = await session.execute(query)
        return list(result.all())


async def get_recent_leads(
    limit: int = 50, category: str | None = None, search: str | None = None
) -> list[Lead]:
    async with async_session() as session:
        query = select(Lead).order_by(Lead.published_at.desc()).limit(limit)
        if category and category != "all":
            query = query.where(Lead.category == category)
        if search and search.strip():
            term = f"%{search.strip().lower()}%"
            query = query.where(Lead.title.ilike(term) | Lead.description.ilike(term))

        result = await session.execute(query)
        return list(result.scalars().all())


async def update_user_full_filters(
    telegram_id: int, keywords: str, stop_words: str, min_budget: int = 0
) -> None:
    async with async_session() as session:
        await session.execute(
            update(UserFilter)
            .where(UserFilter.user_telegram_id == telegram_id)
            .values(keywords=keywords, stop_words=stop_words, min_budget=min_budget)
        )
        await session.commit()


async def get_users_with_expired_trial() -> list[User]:
    """Find users whose trial ended within the last 48 hours, not yet notified, and no active premium"""
    now = datetime.now(timezone.utc)
    lookback = now - timedelta(days=2)
    async with async_session() as session:
        query = select(User).where(
            User.trial_until.is_not(None),
            User.trial_until <= now,
            User.trial_until >= lookback,
            User.trial_expired_notified == False,
        )
        result = await session.execute(query)
        users = list(result.scalars().all())

        eligible = []
        to_mark_notified = []
        for u in users:
            is_active_prem = u.is_premium
            if u.premium_until:
                p_target = u.premium_until if u.premium_until.tzinfo else u.premium_until.replace(tzinfo=timezone.utc)
                if p_target > now:
                    is_active_prem = True
            if is_active_prem:
                to_mark_notified.append(u.telegram_id)
            else:
                eligible.append(u)

        if to_mark_notified:
            await session.execute(
                update(User)
                .where(User.telegram_id.in_(to_mark_notified))
                .values(trial_expired_notified=True)
            )
            await session.commit()

        return eligible


async def mark_trial_expired_notified(user_id: int) -> None:
    async with async_session() as session:
        await session.execute(
            update(User)
            .where(User.telegram_id == user_id)
            .values(trial_expired_notified=True)
        )
        await session.commit()

