import math
from datetime import date, datetime, timedelta, timezone
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from config import settings
from database.models import Lead, User, UserFilter
from database.session import async_session


async def get_or_create_user(
    telegram_id: int,
    username: str | None = None,
    referred_by: int | None = None,
    utm_source: str | None = None,
) -> tuple[User, bool]:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        created = False
        now = datetime.now(timezone.utc)
        if not user:
            is_valid_ref = bool(referred_by and referred_by != telegram_id)
            bonus_hours = settings.TRIAL_HOURS_REFERRAL if is_valid_ref else settings.TRIAL_HOURS_DEFAULT
            user = User(
                telegram_id=telegram_id,
                username=username,
                referred_by=referred_by if is_valid_ref else None,
                trial_until=now + timedelta(hours=bonus_hours),
                trial_expired_notified=False,
                is_active=True,
                utm_source=utm_source,
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
            "is_active": True,
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
        "is_active": getattr(user, "is_active", True) is not False,
    }


async def toggle_user_active(telegram_id: int, is_active: bool | None = None) -> bool:
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if not user:
            return True
        if is_active is None:
            user.is_active = not user.is_active
        else:
            user.is_active = is_active
        new_val = user.is_active
        await session.commit()
        return new_val


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


async def get_lead_by_id(lead_id: int) -> Lead | None:
    async with async_session() as session:
        result = await session.execute(select(Lead).where(Lead.id == lead_id))
        return result.scalar_one_or_none()


async def get_lead_by_external_id(external_id: str) -> Lead | None:
    async with async_session() as session:
        result = await session.execute(select(Lead).where(Lead.external_id == external_id))
        return result.scalar_one_or_none()


async def get_all_subscribers_with_filters() -> list[tuple[User, UserFilter]]:
    async with async_session() as session:
        query = (
            select(User, UserFilter)
            .join(UserFilter, User.telegram_id == UserFilter.user_telegram_id)
            .where(User.is_active == True)
        )
        result = await session.execute(query)
        return list(result.all())


async def get_recent_leads(
    limit: int = 50,
    category: str | None = None,
    search: str | None = None,
    include_test: bool = False,
) -> list[Lead]:
    async with async_session() as session:
        query = select(Lead).order_by(Lead.published_at.desc()).limit(limit)
        if not include_test:
            query = query.where(~Lead.external_id.like("test_%"))
        if category and category != "all":
            query = query.where(Lead.category == category)
        if search and search.strip():
            term = f"%{search.strip().lower()}%"
            query = query.where(Lead.title.ilike(term) | Lead.description.ilike(term))

        result = await session.execute(query)
        return list(result.scalars().all())


async def update_user_full_filters(
    telegram_id: int, keywords: str, stop_words: str, min_budget: int = 0, categories: str | None = None
) -> None:
    values = {"keywords": keywords, "stop_words": stop_words, "min_budget": min_budget}
    if categories is not None:
        values["categories"] = categories
    async with async_session() as session:
        await session.execute(
            update(UserFilter)
            .where(UserFilter.user_telegram_id == telegram_id)
            .values(**values)
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


async def add_bonus_hours_to_user(telegram_id: int, hours: int) -> None:
    now = datetime.now(timezone.utc)
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if not user:
            return

        if user.premium_until:
            target = user.premium_until if user.premium_until.tzinfo else user.premium_until.replace(tzinfo=timezone.utc)
            if target > now:
                user.premium_until = target + timedelta(hours=hours)
                await session.commit()
                return

        if user.trial_until:
            target_t = user.trial_until if user.trial_until.tzinfo else user.trial_until.replace(tzinfo=timezone.utc)
            if target_t > now:
                user.trial_until = target_t + timedelta(hours=hours)
                await session.commit()
                return

        user.trial_until = now + timedelta(hours=hours)
        user.trial_expired_notified = False
        await session.commit()


async def record_lead_click(telegram_id: int) -> int:
    today_str = date.today().isoformat()
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()
    async with async_session() as session:
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if not user:
            return 0

        user.total_leads_clicked = (user.total_leads_clicked or 0) + 1

        last_date = user.last_active_date
        consec = user.consecutive_active_days or 0
        if last_date == yesterday_str:
            user.consecutive_active_days = consec + 1
        elif last_date != today_str:
            user.consecutive_active_days = 1

        user.last_active_date = today_str
        clicks = user.total_leads_clicked
        await session.commit()
        return clicks


async def get_users_for_referral_promo() -> list[User]:
    """Users registered >= 2 hours ago who haven't received referral promo yet"""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=2)
    async with async_session() as session:
        query = select(User).where(
            User.created_at <= cutoff,
            User.promo_referral_sent == False,
        )
        result = await session.execute(query)
        return list(result.scalars().all())


async def mark_promo_referral_sent(user_id: int) -> None:
    async with async_session() as session:
        await session.execute(
            update(User).where(User.telegram_id == user_id).values(promo_referral_sent=True)
        )
        await session.commit()


async def get_users_for_reactivation_promo() -> list[User]:
    """Users registered/expired >= 48 hours ago who haven't received reactivation promo and are currently Free"""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=48)
    async with async_session() as session:
        query = select(User).where(
            User.promo_reactivation_sent == False,
            User.created_at <= cutoff,
        )
        result = await session.execute(query)
        users = list(result.scalars().all())
        eligible = []
        for u in users:
            # 1. Skip if currently active paid premium
            is_active_prem = False
            if u.premium_until:
                p = u.premium_until if u.premium_until.tzinfo else u.premium_until.replace(tzinfo=timezone.utc)
                if p > now:
                    is_active_prem = True
            elif u.is_premium and not u.premium_until:
                is_active_prem = True

            if is_active_prem:
                continue

            # 2. Skip if currently active trial or trial expired < 48 hours ago
            if u.trial_until:
                t = u.trial_until if u.trial_until.tzinfo else u.trial_until.replace(tzinfo=timezone.utc)
                if t > now or t > cutoff:
                    continue

            # 3. Skip if premium expired < 48 hours ago
            if u.premium_until:
                p = u.premium_until if u.premium_until.tzinfo else u.premium_until.replace(tzinfo=timezone.utc)
                if p > cutoff:
                    continue

            eligible.append(u)
        return eligible


async def mark_promo_reactivation_sent(user_id: int) -> None:
    async with async_session() as session:
        await session.execute(
            update(User).where(User.telegram_id == user_id).values(promo_reactivation_sent=True)
        )
        await session.commit()


async def get_leads_count_last_hours(hours: int = 48, keywords: str | None = None) -> int:
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    async with async_session() as session:
        query = select(Lead).where(Lead.published_at >= since)
        result = await session.execute(query)
        leads = list(result.scalars().all())
        if not keywords or not keywords.strip():
            return len(leads)
        kw_list = [w.strip().lower() for w in keywords.split(",") if w.strip()]
        if not kw_list:
            return len(leads)
        return sum(
            1 for lead in leads
            if any(kw in f"{lead.title} {lead.description}".lower() for kw in kw_list)
        )


async def get_utm_analytics() -> list[dict]:
    """
    Returns aggregated metrics by UTM source:
    - utm_source: source string or 'organic'
    - total_users: total registered users
    - active_users: users with is_active == True
    - premium_users: users with active paid premium
    - trial_users: users with active trial
    - premium_or_trial: users with either active premium or active trial
    """
    async with async_session() as session:
        result = await session.execute(select(User))
        users = list(result.scalars().all())

    stats_map: dict[str, dict] = {}
    for u in users:
        source = u.utm_source.strip() if u.utm_source and u.utm_source.strip() else "organic"
        if source not in stats_map:
            stats_map[source] = {
                "utm_source": source,
                "total_users": 0,
                "active_users": 0,
                "premium_users": 0,
                "trial_users": 0,
                "premium_or_trial": 0,
            }
        stats = stats_map[source]
        stats["total_users"] += 1

        user_status = compute_user_status(u)
        if user_status["is_active"]:
            stats["active_users"] += 1
        if user_status["is_premium"]:
            stats["premium_users"] += 1
        if user_status["is_trial"]:
            stats["trial_users"] += 1
        if user_status["is_premium"] or user_status["is_trial"]:
            stats["premium_or_trial"] += 1

    return sorted(stats_map.values(), key=lambda x: x["total_users"], reverse=True)


