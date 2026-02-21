"""RIT API client for the volatility trading algorithm."""

import time
import logging
import requests
from typing import Optional, Dict, Any, List, Tuple

from config import API_BASE_URL, API_KEY

logger = logging.getLogger(__name__)


class RITApi:
    """Thin wrapper around the RIT REST API for trading."""

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

    # ======================== Data Endpoints ========================

    def get_case(self) -> Optional[Dict]:
        return self._get("/case")

    def get_tick(self) -> int:
        """Get current tick number."""
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

    def get_trader(self) -> Optional[Dict]:
        return self._get("/trader")

    def get_securities(self) -> Optional[List[Dict]]:
        return self._get("/securities")

    def get_security(self, ticker: str) -> Optional[Dict]:
        data = self._get("/securities", {"ticker": ticker})
        if isinstance(data, list) and data:
            return data[0]
        return data

    def get_news(self, since: int = 0, limit: int = 50) -> Optional[List[Dict]]:
        params = {"limit": limit}
        if since > 0:
            params["since"] = since
        return self._get("/news", params)

    def get_limits(self) -> Optional[List[Dict]]:
        return self._get("/limits")

    def get_orders(self, status: str = "OPEN") -> Optional[List[Dict]]:
        return self._get("/orders", {"status": status})

    # ======================== Trading Endpoints ========================

    def submit_market_order(self, ticker: str, quantity: int,
                            action: str) -> Optional[Dict]:
        """Submit a market order. action = 'BUY' or 'SELL'."""
        params = {
            "ticker": ticker,
            "type": "MARKET",
            "quantity": quantity,
            "action": action.upper(),
        }
        result = self._post("/orders", params)
        if result:
            logger.info("MARKET %s %d %s -> order_id=%s",
                        action, quantity, ticker,
                        result.get("order_id", "?"))
        return result

    def submit_limit_order(self, ticker: str, quantity: int,
                           action: str, price: float) -> Optional[Dict]:
        """Submit a limit order."""
        params = {
            "ticker": ticker,
            "type": "LIMIT",
            "quantity": quantity,
            "action": action.upper(),
            "price": round(price, 2),
        }
        result = self._post("/orders", params)
        if result:
            logger.info("LIMIT %s %d %s @ %.2f -> order_id=%s",
                        action, quantity, ticker, price,
                        result.get("order_id", "?"))
        return result

    def cancel_order(self, order_id: int) -> Optional[Dict]:
        return self._delete(f"/orders/{order_id}")

    def cancel_all_orders(self) -> Optional[Dict]:
        return self._post("/commands/cancel", {"all": 1})

    def cancel_orders_for_ticker(self, ticker: str) -> Optional[Dict]:
        return self._post("/commands/cancel", {"ticker": ticker})

    # ======================== Convenience ========================

    def is_connected(self) -> bool:
        case = self.get_case()
        return case is not None

    def is_active(self) -> bool:
        status = self.get_status()
        return status in ("ACTIVE", "RUNNING")

    def get_positions(self) -> Dict[str, int]:
        """Get all current positions as {ticker: quantity}."""
        securities = self.get_securities()
        if not securities:
            return {}
        return {
            sec["ticker"]: sec.get("position", 0)
            for sec in securities
            if sec.get("position", 0) != 0
        }

    def get_nlv(self) -> float:
        trader = self.get_trader()
        if trader:
            return trader.get("nlv", 0.0)
        return 0.0
