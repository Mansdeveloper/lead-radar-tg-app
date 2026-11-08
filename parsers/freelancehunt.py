import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import aiohttp
import feedparser

from parsers.base import BaseParser, RawLead


class FreelancehuntParser(BaseParser):
    RSS_URL = "https://freelancehunt.com/projects.rss"
    SOURCE_NAME = "freelancehunt"

    def _clean_html(self, text: str) -> str:
        clean = re.sub(r"<[^>]+>", " ", text)
        clean = html.unescape(clean)
        return " ".join(clean.split())

    def _extract_budget(self, title: str, description: str) -> str:
        # Freelancehunt usually puts budget in title or description like "50USD" or "10 000 UAH / RUB"
        match = re.search(r"(\d[\d\s]*\s*(?:USD|\$|EUR|€|грн|руб|₽))", title, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        match_desc = re.search(r"(\d[\d\s]*\s*(?:USD|\$|EUR|€|грн|руб|₽))", description, re.IGNORECASE)
        if match_desc:
            return match_desc.group(1).strip()
        return "По договоренности"

    async def fetch_latest(self) -> list[RawLead]:
        leads: list[RawLead] = []
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(self.RSS_URL, headers=headers, timeout=15) as response:
                    if response.status != 200:
                        return []
                    content = await response.text()

            feed = feedparser.parse(content)
            for entry in feed.entries:
                link = entry.get("link", "")
                title = entry.get("title", "")
                raw_desc = entry.get("description", "")
                desc = self._clean_html(raw_desc)[:400]
                guid = entry.get("id") or entry.get("guid") or link

                pub_date = datetime.now(timezone.utc)
                if "published" in entry:
                    try:
                        pub_date = parsedate_to_datetime(entry.published)
                    except Exception:
                        pass

                budget = self._extract_budget(title, raw_desc)
                raw_text = f"{title} {desc}".lower()

                leads.append(
                    RawLead(
                        source=self.SOURCE_NAME,
                        external_id=f"fh_{guid}",
                        title=title,
                        description=desc,
                        url=link,
                        budget=budget,
                        category="all",
                        published_at=pub_date,
                        raw_text=raw_text,
                    )
                )
        except Exception as e:
            print(f"[FreelancehuntParser] Error fetching RSS: {e}")

        return leads
