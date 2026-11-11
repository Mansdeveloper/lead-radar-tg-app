from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from config import settings
from database.models import Base

engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    if "sqlite" in settings.DATABASE_URL:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


def _migrate_db(connection):
    inspector = inspect(connection)
    if "users" in inspector.get_table_names():
        cols = [c["name"] for c in inspector.get_columns("users")]
        if "trial_until" not in cols:
            connection.execute(text("ALTER TABLE users ADD COLUMN trial_until DATETIME"))
        if "trial_expired_notified" not in cols:
            connection.execute(text("ALTER TABLE users ADD COLUMN trial_expired_notified BOOLEAN DEFAULT 0"))
        if "is_active" not in cols:
            connection.execute(text("ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT 1"))
        if "promo_referral_sent" not in cols:
            connection.execute(text("ALTER TABLE users ADD COLUMN promo_referral_sent BOOLEAN DEFAULT 0"))
        if "promo_reactivation_sent" not in cols:
            connection.execute(text("ALTER TABLE users ADD COLUMN promo_reactivation_sent BOOLEAN DEFAULT 0"))
        if "total_leads_clicked" not in cols:
            connection.execute(text("ALTER TABLE users ADD COLUMN total_leads_clicked INTEGER DEFAULT 0"))
        if "consecutive_active_days" not in cols:
            connection.execute(text("ALTER TABLE users ADD COLUMN consecutive_active_days INTEGER DEFAULT 0"))
        if "last_active_date" not in cols:
            connection.execute(text("ALTER TABLE users ADD COLUMN last_active_date VARCHAR(10)"))
        if "utm_source" not in cols:
            connection.execute(text("ALTER TABLE users ADD COLUMN utm_source VARCHAR(128)"))

        connection.execute(text("UPDATE users SET total_leads_clicked = 0 WHERE total_leads_clicked IS NULL"))
        connection.execute(text("UPDATE users SET consecutive_active_days = 0 WHERE consecutive_active_days IS NULL"))
        connection.execute(text("UPDATE users SET is_active = 1 WHERE is_active IS NULL"))
        connection.execute(text("UPDATE users SET promo_referral_sent = 0 WHERE promo_referral_sent IS NULL"))
        connection.execute(text("UPDATE users SET promo_reactivation_sent = 0 WHERE promo_reactivation_sent IS NULL"))


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_db)
