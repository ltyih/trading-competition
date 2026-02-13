"""Tests for book parsing and L1 projection."""

from __future__ import annotations

import pytest

from ritc_mm.api.models import BookResponse
from ritc_mm.data.book import parse_book_response, to_l1


def _order(order_id: int, action: str, price: float, qty: float, filled: float = 0.0) -> dict:
    return {
        "order_id": order_id,
        "period": 1,
        "tick": 10,
        "ticker": "SPNG",
        "type": "LIMIT",
        "quantity": qty,
        "action": action,
        "price": price,
        "quantity_filled": filled,
        "vwap": None,
        "status": "OPEN",
    }


def test_parse_book_aggregates_same_price_and_sorts() -> None:
    book = BookResponse.model_validate(
        {
            "bid": [
                _order(1, "BUY", 25.00, 100),
                _order(2, "BUY", 25.00, 200),
                _order(3, "BUY", 24.99, 50),
            ],
            "ask": [
                _order(4, "SELL", 25.02, 100),
                _order(5, "SELL", 25.01, 300),
                _order(6, "SELL", 25.01, 100, filled=25),
            ],
        }
    )

    l2 = parse_book_response("SPNG", book)

    assert [level.px for level in l2.bids] == [25.00, 24.99]
    assert l2.bids[0].sz == 300
    assert [level.px for level in l2.asks] == [25.01, 25.02]
    assert l2.asks[0].sz == 375


def test_parse_book_handles_empty_sides() -> None:
    book = BookResponse.model_validate({"bid": [], "ask": []})
    l2 = parse_book_response("SPNG", book)
    l1 = to_l1(l2)

    assert l2.bids == []
    assert l2.asks == []
    assert l1.bid_px is None
    assert l1.ask_px is None
    assert l1.mid is None
    assert l1.spread is None


def test_to_l1_computes_mid_and_spread_when_two_sided() -> None:
    book = BookResponse.model_validate(
        {
            "bid": [_order(1, "BUY", 24.99, 100)],
            "ask": [_order(2, "SELL", 25.01, 200)],
        }
    )
    l2 = parse_book_response("SPNG", book)
    l1 = to_l1(l2)

    assert l1.bid_px == 24.99
    assert l1.ask_px == 25.01
    assert l1.mid == 25.0
    assert l1.spread == pytest.approx(0.02)
