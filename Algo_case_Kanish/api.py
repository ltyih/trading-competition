# -*- coding: utf-8 -*-
"""Algo Market Making Bot - RIT API Layer. V11."""

import time
import logging
import requests

from config import API_BASE_URL, API_KEY, MAX_ORDER_SIZE

logger = logging.getLogger(__name__)
MAX_RETRIES = 3


class RITApi:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": API_KEY})
        self.base_url = API_BASE_URL
        self.consecutive_errors = 0

    def _get(self, endpoint, params=None, _retry=0):
        try:
            resp = self.session.get(f"{self.base_url}{endpoint}", params=params, timeout=3)
            if resp.status_code == 429:
                if _retry >= MAX_RETRIES:
                    return None
                wait = float(resp.json().get("wait", 0.15))
                time.sleep(min(wait, 0.5))
                return self._get(endpoint, params, _retry + 1)
            if resp.ok:
                self.consecutive_errors = 0
                return resp.json()
            return None
        except requests.exceptions.ConnectionError:
            self.consecutive_errors += 1
            return None
        except Exception as e:
            self.consecutive_errors += 1
            logger.error("GET %s error: %s", endpoint, e)
            return None

    def _post(self, endpoint, params=None, _retry=0):
        try:
            resp = self.session.post(f"{self.base_url}{endpoint}", params=params, timeout=3)
            if resp.status_code == 429:
                if _retry >= MAX_RETRIES:
                    return None
                wait = float(resp.json().get("wait", 0.15))
                time.sleep(min(wait, 0.5))
                return self._post(endpoint, params, _retry + 1)
            if resp.ok:
                self.consecutive_errors = 0
                return resp.json()
            return None
        except Exception as e:
            self.consecutive_errors += 1
            logger.error("POST %s error: %s", endpoint, e)
            return None

    def _delete(self, endpoint, params=None, _retry=0):
        try:
            resp = self.session.delete(f"{self.base_url}{endpoint}", params=params, timeout=3)
            if resp.status_code == 429:
                if _retry >= MAX_RETRIES:
                    return None
                wait = float(resp.json().get("wait", 0.15))
                time.sleep(min(wait, 0.5))
                return self._delete(endpoint, params, _retry + 1)
            if resp.ok:
                self.consecutive_errors = 0
                return resp.json()
            return None
        except Exception as e:
            self.consecutive_errors += 1
            return None

    def is_healthy(self):
        return self.consecutive_errors < 5

    def get_case(self):
        return self._get("/case")

    def get_tick(self):
        case = self.get_case()
        return case.get("tick", 0) if case else 0

    def get_status(self):
        case = self.get_case()
        return case.get("status", "UNKNOWN") if case else "UNKNOWN"

    def is_connected(self):
        return self.get_case() is not None

    def get_trader(self):
        return self._get("/trader")

    def get_nlv(self):
        trader = self.get_trader()
        return trader.get("nlv", 0.0) if trader else 0.0

    def get_securities(self):
        return self._get("/securities")

    def get_security(self, ticker):
        data = self._get("/securities", {"ticker": ticker})
        if isinstance(data, list) and data:
            return data[0]
        return data

    def get_limits(self):
        return self._get("/limits")

    def get_book(self, ticker, limit=10):
        data = self._get("/securities/book", {"ticker": ticker, "limit": limit})
        if not data or not isinstance(data, dict):
            return {"bids": [], "asks": []}
        bids = data.get("bids", data.get("bid", []))
        asks = data.get("asks", data.get("ask", []))
        return {"bids": bids or [], "asks": asks or []}

    def get_mid_price(self, book):
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if bids and asks:
            bid = float(bids[0].get("price", 0.0))
            ask = float(asks[0].get("price", 0.0))
            if bid > 0 and ask > 0:
                return round((bid + ask) / 2.0, 2)
        return 0.0

    def get_best_bid(self, book):
        bids = book.get("bids", [])
        return float(bids[0].get("price", 0.0)) if bids else 0.0

    def get_best_ask(self, book):
        asks = book.get("asks", [])
        return float(asks[0].get("price", 0.0)) if asks else 0.0

    def get_spread(self, book):
        bid = self.get_best_bid(book)
        ask = self.get_best_ask(book)
        return ask - bid if bid > 0 and ask > 0 else 0.0

    def submit_limit_order(self, ticker, quantity, action, price):
        if quantity <= 0 or price <= 0:
            return None
        return self._post("/orders", {
            "ticker": ticker, "type": "LIMIT",
            "quantity": min(int(quantity), MAX_ORDER_SIZE),
            "action": action.upper(), "price": round(price, 2),
        })

    def submit_market_order(self, ticker, quantity, action):
        if quantity <= 0:
            return None
        result = self._post("/orders", {
            "ticker": ticker, "type": "MARKET",
            "quantity": min(int(quantity), MAX_ORDER_SIZE),
            "action": action.upper(),
        })
        if result:
            logger.info("MARKET %s %d %s", action, quantity, ticker)
        return result

    def cancel_all_orders(self):
        return self._post("/commands/cancel", {"all": 1})

    def cancel_ticker_orders(self, ticker):
        return self._post("/commands/cancel", {"ticker": ticker})

    def cancel_order(self, order_id):
        return self._delete(f"/orders/{order_id}")

    def get_open_orders(self, ticker=None):
        params = {"status": "OPEN"}
        if ticker:
            params["ticker"] = ticker
        data = self._get("/orders", params)
        return data if isinstance(data, list) else []

    def get_news(self):
        data = self._get("/news")
        return data if isinstance(data, list) else []

    def close(self):
        self.session.close()