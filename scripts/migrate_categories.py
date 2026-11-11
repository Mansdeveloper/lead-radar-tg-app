import asyncio
import html
import io
import logging
from pathlib import Path
import re
import sys
from collections import Counter

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from sqlalchemy import select

from database.models import Lead
from database.session import async_session, init_db
from services.classifier import detect_lead_category

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("MigrateCategories")

_BUDGET_PATTERN = re.compile(
    r"((?:[$€₽₴]\s*\d[\d\s]*)|(?:\d[\d\s]*\s*(?:руб|₽|USD|\$|EUR|€|грн|UAH|RUB|₴)))",
    re.IGNORECASE,
)


def _extract_lead_budget(title: str, description: str) -> str:
    m = _BUDGET_PATTERN.search(title) or _BUDGET_PATTERN.search(description)
    if m:
        return m.group(1).strip()
    return "По договоренности"


async def run_migration() -> dict:
    await init_db()

    async with async_session() as session:
        result = await session.execute(select(Lead))
        leads = list(result.scalars().all())
        total_leads = len(leads)
        logger.info(f"Loaded {total_leads} leads from database.")

        updated_categories_count = 0
        updated_budgets_count = 0
        distribution: Counter = Counter()

        for lead in leads:
            # Unescape title and description if they contain HTML entities (e.g. &#8381;)
            clean_title = html.unescape(lead.title)
            clean_desc = html.unescape(lead.description)
            if lead.title != clean_title:
                lead.title = clean_title
            if lead.description != clean_desc:
                lead.description = clean_desc

            # Re-extract budget if missing or negotiable but explicitly specified in text
            if lead.budget == "По договоренности":
                extracted_budget = _extract_lead_budget(clean_title, clean_desc)
                if extracted_budget != "По договоренности":
                    lead.budget = extracted_budget
                    updated_budgets_count += 1

            new_cat = detect_lead_category(clean_title, clean_desc)
            distribution[new_cat] += 1
            if lead.category != new_cat:
                lead.category = new_cat
                updated_categories_count += 1

        await session.commit()
        logger.info(
            f"Migration completed! Updated {updated_categories_count}/{total_leads} categories, "
            f"recovered {updated_budgets_count} budgets."
        )
        logger.info(f"Category distribution: {dict(distribution)}")

        return {
            "total_leads": total_leads,
            "updated_categories_count": updated_categories_count,
            "updated_budgets_count": updated_budgets_count,
            "distribution": dict(distribution),
        }


if __name__ == "__main__":
    asyncio.run(run_migration())
