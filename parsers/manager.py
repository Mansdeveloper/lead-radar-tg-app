import asyncio
import logging
from aiogram import Bot

from config import settings
from database.crud import get_lead_by_external_id, save_lead
from parsers.fl import FLRuParser
from parsers.freelancehunt import FreelancehuntParser
from services.matcher import dispatch_lead

logger = logging.getLogger(__name__)


class IngestionManager:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.parsers = [
            FLRuParser(),
            FreelancehuntParser(),
        ]
        self._is_running = False

    async def start(self):
        self._is_running = True
        logger.info("Starting IngestionManager loop...")

        while self._is_running:
            for parser in self.parsers:
                try:
                    leads = await parser.fetch_latest()
                    new_count = 0
                    for lead in leads:
                        inserted = await save_lead(
                            source=lead.source,
                            external_id=lead.external_id,
                            title=lead.title,
                            description=lead.description,
                            url=lead.url,
                            budget=lead.budget,
                            category=lead.category,
                            published_at=lead.published_at,
                        )
                        if inserted:
                            new_count += 1
                            record = await get_lead_by_external_id(lead.external_id)
                            if record:
                                lead.id = record.id
                            # Dispatch to interested users
                            await dispatch_lead(self.bot, lead)

                    if new_count > 0:
                        logger.info(f"[{parser.SOURCE_NAME}] Found and dispatched {new_count} new leads.")
                except Exception as e:
                    logger.error(f"Error running parser {parser.SOURCE_NAME}: {e}")

            await asyncio.sleep(settings.PARSE_INTERVAL_SECONDS)

    def stop(self):
        self._is_running = False
