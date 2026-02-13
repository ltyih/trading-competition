"""Global in-memory state aggregation for market data ingestion."""

from __future__ import annotations

from collections import defaultdict
import logging
import time
from typing import Any

from ritc_mm.api.client import ApiClient
from ritc_mm.api.models import CaseResponse, LimitInfo, OrderResponse, SecurityResponse
from ritc_mm.data.book import L1, L2Book, parse_book_response, to_l1
from ritc_mm.data.news import NewsStorage
from ritc_mm.data.tape import TapeBuffer


class GlobalState:
    """Canonical in-memory state container for the ingestion loop."""

    def __init__(
        self,
        universe: list[str],
        book_depth: int,
        tape_maxlen: int,
        news_max_items: int,
        logger: logging.Logger | logging.LoggerAdapter[logging.Logger] | None = None,
    ) -> None:
        self.universe = list(universe)
        self.book_depth = int(book_depth)

        self.case: CaseResponse | None = None
        self.securities: list[SecurityResponse] = []
        self.positions_by_ticker: dict[str, SecurityResponse] = {}
        self.open_orders_by_ticker: dict[str, list[OrderResponse]] = {}
        self.limits: list[LimitInfo] = []

        self.books: dict[str, L2Book] = {}
        self.l1: dict[str, L1] = {}

        self.tape = TapeBuffer(maxlen_per_ticker=tape_maxlen)
        self.news = NewsStorage(max_items=news_max_items)

        self.tas_after: dict[str, int] = {ticker: 0 for ticker in self.universe}
        self.news_since: int = 0
        self.last_update_ts: float | None = None

        self._logger = logger

    def _warn(self, message: str, *, ticker: str | None = None, exc: Exception | None = None) -> None:
        if self._logger is None:
            return
        kwargs: dict[str, Any] = {"extra": {"order_id": None, "ticker": ticker}}
        if exc is not None:
            kwargs["exc_info"] = True
        self._logger.warning(message, **kwargs)

    def update(self, api: ApiClient, now_ts: float | None = None) -> dict[str, int]:
        """Poll API endpoints and refresh in-memory state.

        The update is resilient: endpoint failures are logged and prior values are
        retained for affected slices.
        """
        counts = {
            "case": 0,
            "securities": 0,
            "orders": 0,
            "limits": 0,
            "news": 0,
            "books": 0,
            "tas": 0,
            "errors": 0,
        }

        try:
            case = api.get_case()
            self.case = case
            counts["case"] = 1
        except Exception as exc:
            counts["errors"] += 1
            self._warn("Failed to refresh /case; retaining last value", exc=exc)

        try:
            securities = api.get_securities()
            self.securities = securities
            self.positions_by_ticker = {sec.ticker: sec for sec in securities}
            counts["securities"] = len(securities)
        except Exception as exc:
            counts["errors"] += 1
            self._warn("Failed to refresh /securities; retaining last values", exc=exc)

        try:
            open_orders = api.get_orders(status="OPEN")
            grouped: dict[str, list[OrderResponse]] = defaultdict(list)
            for order in open_orders:
                grouped[order.ticker].append(order)
            self.open_orders_by_ticker = dict(grouped)
            counts["orders"] = len(open_orders)
        except Exception as exc:
            counts["errors"] += 1
            self._warn("Failed to refresh /orders; retaining last values", exc=exc)

        try:
            limits = api.get_limits()
            self.limits = limits
            counts["limits"] = len(limits)
        except Exception as exc:
            counts["errors"] += 1
            self._warn("Failed to refresh /limits; retaining last values", exc=exc)

        try:
            news_items = api.get_news(since=self.news_since)
            added_news = self.news.apply(news_items)
            if added_news:
                self.news_since = max(self.news_since, added_news[0].news_id, added_news[-1].news_id)
            counts["news"] = len(added_news)
        except Exception as exc:
            counts["errors"] += 1
            self._warn("Failed to refresh /news; retaining last values", exc=exc)

        new_books = dict(self.books)
        new_l1 = dict(self.l1)

        for ticker in self.universe:
            try:
                book = api.get_book(ticker=ticker, limit=self.book_depth)
                parsed = parse_book_response(ticker=ticker, book=book)
                new_books[ticker] = parsed
                new_l1[ticker] = to_l1(parsed)
                counts["books"] += 1
            except Exception as exc:
                counts["errors"] += 1
                self._warn("Failed to refresh /securities/book for ticker", ticker=ticker, exc=exc)

            try:
                trades = api.get_tas(ticker=ticker, after=self.tas_after.get(ticker, 0))
                accepted = self.tape.apply(ticker=ticker, entries=trades)
                if accepted:
                    self.tas_after[ticker] = accepted[-1].id
                counts["tas"] += len(accepted)
            except Exception as exc:
                counts["errors"] += 1
                self._warn("Failed to refresh /securities/tas for ticker", ticker=ticker, exc=exc)

        self.books = new_books
        self.l1 = new_l1

        self.last_update_ts = float(now_ts) if now_ts is not None else time.time()
        return counts
