"""News storage and incremental pointer helpers."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import time

from ritc_mm.api.models import NewsItem


@dataclass(frozen=True)
class StoredNews:
    """Normalized news event with local ingest timestamp."""

    news_id: int
    period: int
    tick: int
    ticker: str
    headline: str
    body: str
    ts: float


class NewsStorage:
    """In-memory bounded news store with monotonic ``news_id`` pointer."""

    def __init__(self, max_items: int) -> None:
        self._items: deque[StoredNews] = deque(maxlen=max_items)
        self._last_news_id: int = 0

    def last_news_id(self) -> int:
        """Return latest ingested news id (``0`` if none)."""
        return self._last_news_id

    def apply(self, entries: list[NewsItem]) -> list[StoredNews]:
        """Ingest incremental news entries and return accepted items."""
        accepted: list[StoredNews] = []
        last = self._last_news_id

        for entry in sorted(entries, key=lambda item: int(item.news_id)):
            entry_id = int(entry.news_id)
            if entry_id <= last:
                continue

            item = StoredNews(
                news_id=entry_id,
                period=int(entry.period),
                tick=int(entry.tick),
                ticker=str(entry.ticker),
                headline=str(entry.headline),
                body=str(entry.body),
                ts=time.time(),
            )
            self._items.append(item)
            accepted.append(item)
            last = entry_id

        self._last_news_id = last
        return accepted

    def get_recent(self, limit: int) -> list[StoredNews]:
        """Return most recent news items (newest first)."""
        if limit <= 0:
            return []
        return list(reversed(list(self._items)[-limit:]))

    def get_by_ticker(self, ticker: str, limit: int | None = None) -> list[StoredNews]:
        """Return news items filtered by ticker (newest first)."""
        filtered = [item for item in self._items if item.ticker == ticker]
        filtered.reverse()
        if limit is None or limit >= len(filtered):
            return filtered
        return filtered[:limit]
