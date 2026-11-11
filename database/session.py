import logging
from pathlib import Path
from sqlalchemy import event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from config import settings
from database.models import Base

logger = logging.getLogger(__name__)


def ensure_sqlite_parent_dir(database_url: str) -> None:
    """Safely create the parent directory for file-based SQLite URLs if permissions allow."""
    try:
        url = make_url(database_url)
    except Exception:
        return

    if not url.drivername.startswith("sqlite"):
        return

    db_name = url.database
    if not db_name or db_name == ":memory:" or db_name.startswith("file::memory:"):
        return

    if str(url.query.get("mode", "")).lower() == "memory":
        return

    if db_name.startswith("file:"):
        file_part, _, query_part = db_name[5:].partition("?")
        if "mode=memory" in query_part.lower() or not file_part or file_part == ":memory:":
            return
        db_name = file_part

    parent_dir = Path(db_name).parent
    if parent_dir and str(parent_dir) != ".":
        try:
            parent_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(f"Could not create SQLite directory '{parent_dir}': {exc}")


ensure_sqlite_parent_dir(settings.DATABASE_URL)

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
    ensure_sqlite_parent_dir(settings.DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_db)
