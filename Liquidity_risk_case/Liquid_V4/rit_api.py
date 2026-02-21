# -*- coding: utf-8 -*-
"""LT3 Tender Liquidity Bot — RIT API layer.

Thin HTTP wrapper around the RIT REST API.
"""

from config import BASE_URL, BOOK_LIMIT


class ApiException(Exception):
    pass


def synthetic_book_from_quotes(bid: float, bid_size: int,
                                ask: float, ask_size: int) -> dict:
    """Fallback when /securities/book returns empty.

    Uses top-of-book quotes from /securities to create a 1-level book.
    """
    book = {'bid': [], 'ask': []}
    if bid and bid > 0 and bid_size and bid_size > 0:
        book['bid'].append({'price': float(bid), 'quantity': int(bid_size),
                            'quantity_filled': 0})
    if ask and ask > 0 and ask_size and ask_size > 0:
        book['ask'].append({'price': float(ask), 'quantity': int(ask_size),
                            'quantity_filled': 0})
    return book


# ========== Core API functions ==========

def get_tick(session) -> int:
    resp = session.get(f'{BASE_URL}/case')
    if resp.ok:
        return int(resp.json().get('tick', 0))
    raise ApiException('Failed to get tick')


def get_securities(session) -> list:
    resp = session.get(f'{BASE_URL}/securities')
    if resp.ok:
        return resp.json()
    raise ApiException('Failed to get securities data')


def get_order_book(session, ticker: str, limit: int = BOOK_LIMIT) -> dict:
    """Get full order book. Normalises bids/asks vs bid/ask key variants."""
    resp = session.get(f'{BASE_URL}/securities/book',
                       params={'ticker': ticker, 'limit': limit})
    if not resp.ok:
        raise ApiException(f'Failed to get order book for {ticker}')

    book = resp.json()
    if isinstance(book, dict) and ('bids' in book or 'asks' in book):
        return {
            'bid': book.get('bids', []) or [],
            'ask': book.get('asks', []) or [],
        }
    return {
        'bid': book.get('bid', []) if isinstance(book, dict) else [],
        'ask': book.get('ask', []) if isinstance(book, dict) else [],
    }


# ---- Tender endpoints (RIT Client REST API v1.0.3) ----

def get_tender_offers(session) -> list:
    resp = session.get(f'{BASE_URL}/tenders')
    if resp.ok:
        return resp.json()
    return []


def accept_tender(session, tender_id: int, price: float = None) -> bool:
    params = {}
    if price is not None:
        params['price'] = float(price)
    resp = session.post(f'{BASE_URL}/tenders/{int(tender_id)}', params=params)
    return resp.ok


def decline_tender(session, tender_id: int) -> bool:
    resp = session.delete(f'{BASE_URL}/tenders/{int(tender_id)}')
    return resp.ok


def get_position(session, ticker: str) -> int:
    """Get current position for a single ticker. Returns 0 on error."""
    resp = session.get(f'{BASE_URL}/securities', params={'ticker': ticker})
    if resp.ok:
        data = resp.json()
        if isinstance(data, list) and data:
            return int(data[0].get('position', 0))
        if isinstance(data, dict):
            return int(data.get('position', 0))
    return 0
