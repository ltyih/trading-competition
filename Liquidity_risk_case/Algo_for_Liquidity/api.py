# -*- coding: utf-8 -*-
"""Ultimate Liquidity Bot - RIT API Layer.

Mirrors the proven connection pattern from the volatility algo's rit_api.py.
"""

import time
import logging
import requests
from typing import Optional, Dict, Any, List

from config import API_BASE_URL, API_KEY, BOOK_DEPTH, MAX_ORDER_SIZE

logger = logging.getLogger(__name__)


class RITApi:
    """RIT REST API client - same pattern as volatility algo."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": API_KEY})
        self.base_url = API_BASE_URL

    def _get(self, endpoint: str, params: Dict = None) -> Optional[Any]:
        try:
            resp = self.session.get(f"{self.base_url}{endpoint}",
                                    params=params, timeout=5)
            if resp.status_code == 429:
                wait = float(resp.json().get("wait", 0.5))
                time.sleep(wait)
                return self._get(endpoint, params)
            if resp.ok:
                return resp.json()
            logger.warning("GET %s returned %d", endpoint, resp.status_code)
            return None
        except requests.exceptions.ConnectionError:
            return None
        except Exception as e:
            logger.error("GET %s error: %s", endpoint, e)
            return None

    def _post(self, endpoint: str, params: Dict = None) -> Optional[Any]:
        try:
            resp = self.session.post(f"{self.base_url}{endpoint}",
                                     params=params, timeout=5)
            if resp.status_code == 429:
                wait = float(resp.json().get("wait", 0.5))
                time.sleep(wait)
                return self._post(endpoint, params)
            if resp.ok:
                return resp.json()
            logger.warning("POST %s returned %d: %s",
                           endpoint, resp.status_code, resp.text[:200])
            return None
        except Exception as e:
            logger.error("POST %s error: %s", endpoint, e)
            return None

    def _delete(self, endpoint: str, params: Dict = None) -> Optional[Any]:
        try:
            resp = self.session.delete(f"{self.base_url}{endpoint}",
                                       params=params, timeout=5)
            if resp.ok:
                return resp.json()
            return None
        except Exception as e:
            logger.error("DELETE %s error: %s", endpoint, e)
            return None

    # ======================== Connection ========================

    def is_connected(self) -> bool:
        case = self.get_case()
        return case is not None

    def is_active(self) -> bool:
        status = self.get_status()
        return status in ("ACTIVE", "RUNNING")

    # ======================== Case ========================

    def get_case(self) -> Optional[Dict]:
        return self._get("/case")

    def get_tick(self) -> int:
        case = self.get_case()
        if case:
            return case.get("tick", 0)
        return 0

    def get_period(self) -> int:
        case = self.get_case()
        if case:
            return case.get("period", 0)
        return 0

    def get_status(self) -> str:
        case = self.get_case()
        if case:
            return case.get("status", "UNKNOWN")
        return "UNKNOWN"

    # ======================== Trader ========================

    def get_trader(self) -> Optional[Dict]:
        return self._get("/trader")

    def get_nlv(self) -> float:
        trader = self.get_trader()
        if trader:
            return trader.get("nlv", 0.0)
        return 0.0

    # ======================== Securities ========================

    def get_securities(self) -> Optional[List[Dict]]:
        return self._get("/securities")

    def get_security(self, ticker: str) -> Optional[Dict]:
        data = self._get("/securities", {"ticker": ticker})
        if isinstance(data, list) and data:
            return data[0]
        return data

    def get_position(self, ticker: str) -> int:
        sec = self.get_security(ticker)
        if sec:
            return int(sec.get("position", 0))
        return 0

    def get_all_positions(self) -> Dict[str, int]:
        securities = self.get_securities()
        if not securities:
            return {}
        return {
            sec["ticker"]: sec.get("position", 0)
            for sec in securities
            if sec.get("position", 0) != 0
        }

    # ======================== Order Book ========================

    def get_book(self, ticker: str, limit: int = BOOK_DEPTH) -> Dict[str, List]:
        data = self._get("/securities/book",
                         {"ticker": ticker, "limit": limit})
        if not data or not isinstance(data, dict):
            return {"bid": [], "ask": []}
        bids = data.get("bids", data.get("bid", []))
        asks = data.get("asks", data.get("ask", []))
        return {"bid": bids or [], "ask": asks or []}

    @staticmethod
    def available_qty(level: Dict[str, Any]) -> int:
        return max(0, int(level.get("quantity", 0))
                   - int(level.get("quantity_filled", 0)))

    def get_book_depth(self, book: Dict[str, List], side: str) -> int:
        key = "bid" if side.upper() == "SELL" else "ask"
        return sum(self.available_qty(lvl) for lvl in book.get(key, []))

    def get_best_price(self, book: Dict[str, List], side: str) -> float:
        key = "bid" if side.upper() == "SELL" else "ask"
        levels = book.get(key, [])
        if levels:
            return float(levels[0].get("price", 0.0))
        return 0.0

    def get_spread(self, book: Dict[str, List]) -> float:
        bids = book.get("bid", [])
        asks = book.get("ask", [])
        if bids and asks:
            bid = float(bids[0].get("price", 0.0))
            ask = float(asks[0].get("price", 0.0))
            if bid > 0 and ask > 0:
                return ask - bid
        return 0.0

    def get_mid_price(self, book: Dict[str, List]) -> float:
        bids = book.get("bid", [])
        asks = book.get("ask", [])
        if bids and asks:
            bid = float(bids[0].get("price", 0.0))
            ask = float(asks[0].get("price", 0.0))
            if bid > 0 and ask > 0:
                return (bid + ask) / 2.0
        return 0.0

    def walk_book(self, book: Dict[str, List], side: str,
                  quantity: int) -> Dict[str, Any]:
        """Walk the book to estimate execution price for given qty."""
        key = "bid" if side.upper() == "SELL" else "ask"
        levels = book.get(key, [])
        if not levels:
            return {"avg_price": 0.0, "filled": 0, "can_fill": False}

        remaining = int(quantity)
        total_cost = 0.0
        filled = 0
        ref_price = float(levels[0].get("price", 0.0))

        for lvl in levels:
            if remaining <= 0:
                break
            px = float(lvl.get("price", 0.0))
            avail = self.available_qty(lvl)
            if avail <= 0 or px <= 0:
                continue
            take = min(avail, remaining)
            total_cost += take * px
            filled += take
            remaining -= take

        if remaining > 0 and levels:
            worst_px = float(levels[-1].get("price", ref_price))
            penalty = 0.95 if side.upper() == "SELL" else 1.05
            total_cost += remaining * worst_px * penalty
            filled += remaining

        avg_price = total_cost / max(filled, 1)
        return {
            "avg_price": avg_price,
            "filled": filled,
            "can_fill": filled >= quantity,
            "ref_price": ref_price,
        }

    # ======================== Tenders ========================

    def get_tenders(self) -> List[Dict]:
        data = self._get("/tenders")
        return data if isinstance(data, list) else []

    def accept_tender(self, tender_id: int, price: float = None) -> bool:
        params = {}
        if price is not None:
            params["price"] = round(float(price), 2)
        result = self._post(f"/tenders/{int(tender_id)}", params)
        return result is not None

    def decline_tender(self, tender_id: int) -> bool:
        result = self._delete(f"/tenders/{int(tender_id)}")
        return result is not None

    # ======================== Orders ========================

    def submit_market_order(self, ticker: str, quantity: int,
                            action: str) -> Optional[Dict]:
        params = {
            "ticker": ticker,
            "type": "MARKET",
            "quantity": min(int(quantity), MAX_ORDER_SIZE),
            "action": action.upper(),
        }
        result = self._post("/orders", params)
        if result:
            logger.info("MARKET %s %d %s", action, quantity, ticker)
        return result

    def submit_limit_order(self, ticker: str, quantity: int,
                           action: str, price: float) -> Optional[Dict]:
        params = {
            "ticker": ticker,
            "type": "LIMIT",
            "quantity": min(int(quantity), MAX_ORDER_SIZE),
            "action": action.upper(),
            "price": round(price, 2),
        }
        result = self._post("/orders", params)
        if result:
            logger.info("LIMIT %s %d %s @ %.2f", action, quantity, ticker, price)
        return result

    def cancel_all_orders(self) -> Optional[Dict]:
        return self._post("/commands/cancel", {"all": 1})

    def get_orders(self, status: str = "OPEN") -> Optional[List[Dict]]:
        return self._get("/orders", {"status": status})

    def close(self):
        self.session.close()
