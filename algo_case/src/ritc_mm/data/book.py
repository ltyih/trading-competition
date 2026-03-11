"""Order book data structures and parsing helpers."""

from __future__ import annotations

from dataclasses import dataclass
import time

from *REMOVED*_mm.api.models import BookResponse, OrderResponse


@dataclass(frozen=True)
class BookSideLevel:
    """Aggregated depth level on one side of the book."""

    px: float
    sz: float


@dataclass(frozen=True)
class L1:
    """Top-of-book snapshot."""

    bid_px: float | None
    bid_sz: float | None
    ask_px: float | None
    ask_sz: float | None
    mid: float | None
    spread: float | None
    ts: float


@dataclass(frozen=True)
class L2Book:
    """Aggregated depth snapshot for a ticker."""

    bids: list[BookSideLevel]
    asks: list[BookSideLevel]
    ts: float
    ticker: str


def _aggregate_side(orders: list[OrderResponse], *, descending: bool) -> list[BookSideLevel]:
    """Aggregate same-price orders into levels sorted best-first."""
    levels_by_px: dict[float, float] = {}
    for order in orders:
        if order.price is None:
            continue
        open_qty = max(float(order.quantity) - float(order.quantity_filled), 0.0)
        if open_qty <= 0.0:
            continue
        levels_by_px[float(order.price)] = levels_by_px.get(float(order.price), 0.0) + open_qty

    prices = sorted(levels_by_px.keys(), reverse=descending)
    return [BookSideLevel(px=px, sz=levels_by_px[px]) for px in prices]


def parse_book_response(ticker: str, book: BookResponse) -> L2Book:
    """Convert API ``BookResponse`` into an aggregated ``L2Book``."""
    ts = time.time()
    bids = _aggregate_side(book.bid, descending=True)
    asks = _aggregate_side(book.ask, descending=False)
    return L2Book(bids=bids, asks=asks, ts=ts, ticker=ticker)


def to_l1(book: L2Book) -> L1:
    """Project an ``L2Book`` down to top-of-book fields."""
    bid = book.bids[0] if book.bids else None
    ask = book.asks[0] if book.asks else None

    bid_px = bid.px if bid else None
    bid_sz = bid.sz if bid else None
    ask_px = ask.px if ask else None
    ask_sz = ask.sz if ask else None

    if bid_px is not None and ask_px is not None:
        mid = (bid_px + ask_px) / 2.0
        spread = ask_px - bid_px
    else:
        mid = None
        spread = None

    return L1(
        bid_px=bid_px,
        bid_sz=bid_sz,
        ask_px=ask_px,
        ask_sz=ask_sz,
        mid=mid,
        spread=spread,
        ts=book.ts,
    )
