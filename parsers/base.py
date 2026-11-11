from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class RawLead:
    source: str
    external_id: str
    title: str
    description: str
    url: str
    budget: str
    category: str
    published_at: datetime
    raw_text: str
    id: int | None = None


class BaseParser(ABC):
    @abstractmethod
    async def fetch_latest(self) -> list[RawLead]:
        """Fetch and return newly published leads from the source."""
        pass
