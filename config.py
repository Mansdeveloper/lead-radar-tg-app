import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BOT_TOKEN: str = "YOUR_BOT_TOKEN_HERE"
    CRYPTO_BOT_TOKEN: str = ""
    STARS_PRICE: int = 250  # 250 Telegram Stars (~$5)
    CRYPTO_PRICE_USD: float = 5.0
    DATABASE_URL: str = "sqlite+aiosqlite:///lead_radar.db"
    
    # Lead delivery settings
    FREE_MAX_DAILY_LEADS: int = 3
    FREE_DELAY_SECONDS: int = 900  # 15 minutes
    PARSE_INTERVAL_SECONDS: int = 60  # Check feeds every minute
    
    # Referral reward (days of premium per referral)
    REFERRAL_PREMIUM_DAYS: int = 7
    TRIAL_HOURS_DEFAULT: int = 24
    TRIAL_HOURS_REFERRAL: int = 48

    # WebApp & API settings
    WEBAPP_HOST: str = "0.0.0.0"
    WEBAPP_PORT: int = int(os.environ.get("PORT", 8080))
    WEBAPP_URL: str = os.environ.get("WEBAPP_URL", "")  # Configured by Railway / public domain

    # Admin settings
    ADMIN_IDS: str = ""

    def get_admin_ids(self) -> set[int]:
        ids = set()
        if self.ADMIN_IDS:
            for part in str(self.ADMIN_IDS).split(","):
                part = part.strip()
                if part.isdigit():
                    ids.add(int(part))
        return ids

    def is_admin(self, user_id: int) -> bool:
        admin_ids = self.get_admin_ids()
        if not admin_ids:
            return True
        return user_id in admin_ids

    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
