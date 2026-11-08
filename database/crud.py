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
        if not user:
            user = User(
                telegram_id=telegram_id,
                username=username,
                referred_by=referred_by if referred_by != telegram_id else None,
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
                    now = datetime.now(timezone.utc)
                    current_until = referrer.premium_until or now
                    if current_until < now:
                        current_until = now
                    referrer.premium_until = current_until + timedelta(days=7)
                    referrer.is_premium = True
                    await session.commit()

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


async def is_user_premium(telegram_id: int) -> bool:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if not user:
            return False
        if not user.premium_until:
            return False
        # Normalize timezone
        now = datetime.now(timezone.utc)
        target = user.premium_until
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        return target > now


async def grant_premium(telegram_id: int, days: int) -> datetime:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if not user:
            user = User(telegram_id=telegram_id)
            session.add(user)

        now = datetime.now(timezone.utc)
        current = user.premium_until
        if current is None or (current.tzinfo is None and current < now.replace(tzinfo=None)):
            current = now
        elif current.tzinfo is not None and current < now:
            current = now

        new_until = current + timedelta(days=days)
        user.premium_until = new_until
        user.is_premium = True
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
