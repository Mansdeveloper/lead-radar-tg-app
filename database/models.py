from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    premium_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    trial_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    trial_expired_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    referred_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    referral_count: Mapped[int] = mapped_column(Integer, default=0)
    daily_leads_sent: Mapped[int] = mapped_column(Integer, default=0)
    last_lead_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    promo_referral_sent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    promo_reactivation_sent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    total_leads_clicked: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    consecutive_active_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_active_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class Achievement(Base):
    __tablename__ = "achievements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    achievement_key: Mapped[str] = mapped_column(String(64), index=True)
    unlocked_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class UserFilter(Base):
    __tablename__ = "user_filters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    keywords: Mapped[str] = mapped_column(Text, default="")
    stop_words: Mapped[str] = mapped_column(Text, default="")
    min_budget: Mapped[int] = mapped_column(Integer, default=0)
    categories: Mapped[str] = mapped_column(String(255), default="all")  # e.g. "development,design"


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    external_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(1024))
    budget: Mapped[str] = mapped_column(String(128), default="По договоренности")
    category: Mapped[str] = mapped_column(String(128), default="development")
    published_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
