"""Tests for incremental news storage."""

from __future__ import annotations

from ritc_mm.api.models import NewsItem
from ritc_mm.data.news import NewsStorage


def _news(news_id: int, ticker: str) -> NewsItem:
    return NewsItem.model_validate(
        {
            "news_id": news_id,
            "period": 1,
            "tick": 1,
            "ticker": ticker,
            "headline": f"{ticker} headline {news_id}",
            "body": f"{ticker} body {news_id}",
        }
    )


def test_apply_is_incremental_and_deduplicated() -> None:
    store = NewsStorage(max_items=10)

    accepted = store.apply([_news(3, "SPNG"), _news(1, "SMMR"), _news(2, "SPNG")])
    assert [item.news_id for item in accepted] == [1, 2, 3]
    assert store.last_news_id() == 3

    duplicate = store.apply([_news(2, "SPNG"), _news(3, "SMMR")])
    assert duplicate == []
    assert store.last_news_id() == 3


def test_get_recent_returns_newest_first() -> None:
    store = NewsStorage(max_items=10)
    store.apply([_news(1, "SPNG"), _news(2, "SMMR"), _news(3, "ATMN")])

    recent = store.get_recent(limit=2)
    assert [item.news_id for item in recent] == [3, 2]


def test_get_by_ticker_filters_and_respects_limit() -> None:
    store = NewsStorage(max_items=10)
    store.apply([_news(1, "SPNG"), _news(2, "SMMR"), _news(3, "SPNG"), _news(4, "SPNG")])

    spng = store.get_by_ticker("SPNG", limit=2)
    assert [item.news_id for item in spng] == [4, 3]
