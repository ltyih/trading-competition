"""Tests for GlobalState aggregation and resilience."""

from __future__ import annotations

from unittest.mock import MagicMock

from *REMOVED*_mm.api.models import (
    BookResponse,
    CaseResponse,
    LimitInfo,
    NewsItem,
    OrderResponse,
    SecurityResponse,
    TasEntry,
)
from *REMOVED*_mm.data.state import GlobalState


def _case() -> CaseResponse:
    return CaseResponse.model_validate(
        {
            "name": "Algo-MM",
            "period": 1,
            "tick": 42,
            "ticks_per_period": 300,
            "total_periods": 1,
            "status": "ACTIVE",
            "is_enforce_trading_limits": True,
        }
    )


def _order(order_id: int, ticker: str, action: str, price: float) -> OrderResponse:
    return OrderResponse.model_validate(
        {
            "order_id": order_id,
            "period": 1,
            "tick": 42,
            "trader_id": "bot1",
            "ticker": ticker,
            "type": "LIMIT",
            "quantity": 100,
            "action": action,
            "price": price,
            "quantity_filled": 0,
            "vwap": None,
            "status": "OPEN",
        }
    )


def _book(ticker: str, bid: float, ask: float) -> BookResponse:
    return BookResponse.model_validate(
        {
            "bid": [
                {
                    "order_id": 1,
                    "period": 1,
                    "tick": 42,
                    "ticker": ticker,
                    "type": "LIMIT",
                    "quantity": 200,
                    "action": "BUY",
                    "price": bid,
                    "quantity_filled": 0,
                    "vwap": None,
                    "status": "OPEN",
                }
            ],
            "ask": [
                {
                    "order_id": 2,
                    "period": 1,
                    "tick": 42,
                    "ticker": ticker,
                    "type": "LIMIT",
                    "quantity": 250,
                    "action": "SELL",
                    "price": ask,
                    "quantity_filled": 0,
                    "vwap": None,
                    "status": "OPEN",
                }
            ],
        }
    )


def _tas(entry_id: int) -> TasEntry:
    return TasEntry.model_validate(
        {
            "id": entry_id,
            "period": 1,
            "tick": 42,
            "price": 25.0,
            "quantity": 100,
        }
    )


def _news(news_id: int, ticker: str) -> NewsItem:
    return NewsItem.model_validate(
        {
            "news_id": news_id,
            "period": 1,
            "tick": 42,
            "ticker": ticker,
            "headline": "headline",
            "body": "body",
        }
    )


def _limits() -> list[LimitInfo]:
    return [
        LimitInfo.model_validate(
            {
                "name": "Aggregate",
                "gross": 1000,
                "net": 100,
                "gross_limit": 15000,
                "net_limit": 10000,
                "gross_fine": 10.0,
                "net_fine": 5.0,
            }
        )
    ]


def test_global_state_update_aggregates_all_slices() -> None:
    api = MagicMock()
    api.get_case.return_value = _case()
    api.get_securities.return_value = [
        SecurityResponse.model_validate({"ticker": "SPNG", "position": 100}),
        SecurityResponse.model_validate({"ticker": "SMMR", "position": -50}),
    ]
    api.get_orders.return_value = [
        _order(1, "SPNG", "BUY", 24.99),
        _order(2, "SMMR", "SELL", 25.10),
    ]
    api.get_limits.return_value = _limits()
    api.get_news.return_value = [_news(1, "SPNG"), _news(2, "SMMR")]

    def get_book(ticker: str, limit: int) -> BookResponse:
        del limit
        if ticker == "SPNG":
            return _book("SPNG", 24.99, 25.01)
        return _book("SMMR", 25.09, 25.11)

    def get_tas(ticker: str, after: int) -> list[TasEntry]:
        assert after == 0
        if ticker == "SPNG":
            return [_tas(10)]
        return [_tas(20)]

    api.get_book.side_effect = get_book
    api.get_tas.side_effect = get_tas

    state = GlobalState(
        universe=["SPNG", "SMMR"],
        book_depth=20,
        tape_maxlen=100,
        news_max_items=100,
    )

    counts = state.update(api)

    assert counts == {
        "case": 1,
        "securities": 2,
        "orders": 2,
        "limits": 1,
        "news": 2,
        "books": 2,
        "tas": 2,
        "errors": 0,
    }
    assert state.news_since == 2
    assert state.tas_after == {"SPNG": 10, "SMMR": 20}
    assert set(state.positions_by_ticker.keys()) == {"SPNG", "SMMR"}
    assert set(state.open_orders_by_ticker.keys()) == {"SPNG", "SMMR"}
    assert state.case is not None and state.case.tick == 42
    assert set(state.l1.keys()) == {"SPNG", "SMMR"}


def test_global_state_update_continues_when_some_endpoints_fail() -> None:
    api = MagicMock()
    api.get_case.return_value = _case()
    api.get_securities.return_value = [SecurityResponse.model_validate({"ticker": "SPNG", "position": 1})]
    api.get_orders.return_value = [_order(1, "SPNG", "BUY", 24.99)]
    api.get_limits.return_value = _limits()
    api.get_news.return_value = [_news(1, "SPNG")]
    api.get_book.return_value = _book("SPNG", 24.99, 25.01)
    api.get_tas.return_value = [_tas(10)]

    state = GlobalState(
        universe=["SPNG", "SMMR"],
        book_depth=20,
        tape_maxlen=100,
        news_max_items=100,
    )
    first = state.update(api)
    assert first["errors"] == 0
    old_spng_bid = state.l1["SPNG"].bid_px

    def flaky_get_book(ticker: str, limit: int) -> BookResponse:
        del limit
        if ticker == "SPNG":
            raise RuntimeError("book unavailable")
        return _book("SMMR", 25.00, 25.02)

    def flaky_get_tas(ticker: str, after: int) -> list[TasEntry]:
        if ticker == "SMMR":
            raise RuntimeError("tas unavailable")
        return [_tas(after + 1)]

    api.get_news.side_effect = RuntimeError("news unavailable")
    api.get_book.side_effect = flaky_get_book
    api.get_tas.side_effect = flaky_get_tas

    second = state.update(api)

    assert second["errors"] >= 3
    assert state.l1["SPNG"].bid_px == old_spng_bid
    assert "SMMR" in state.l1
    assert state.tas_after["SPNG"] == 11
    assert state.tas_after["SMMR"] == 10
