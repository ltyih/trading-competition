"""
RIT Client API wrapper with auto-reconnection and auto-login capabilities.
Supports 24/7 operation with automatic recovery from disconnections.
"""
import requests
import subprocess
import time
import logging
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
import psutil

try:
    import pyautogui
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False

try:
    import pygetwindow as gw
    PYGETWINDOW_AVAILABLE = True
except ImportError:
    PYGETWINDOW_AVAILABLE = False

from config import (
    API_BASE_URL, API_KEY, API_HOST, API_PORT,
    RIT_SERVER, RIT_PORT, USERNAME, PASSWORD,
    MAX_RECONNECT_ATTEMPTS, RECONNECT_DELAY_SEC,
    BOOK_DEPTH_LIMIT, NEWS_LIMIT
)

logger = logging.getLogger(__name__)


class RITClientError(Exception):
    pass

class AuthenticationError(RITClientError):
    pass

class ConnectionError(RITClientError):
    pass


class RITClient:
    def __init__(self, api_key: str = None):
        self.base_url = API_BASE_URL
        self.api_key = api_key or API_KEY
        self.session = requests.Session()
        self._update_headers()
        self.is_connected = False
        self.last_tick = 0
        self.last_period = 0
        self.current_status = "UNKNOWN"
        self.reconnect_attempts = 0
        self.last_successful_call = None
        self.consecutive_failures = 0

    def _update_headers(self):
        if self.api_key:
            self.session.headers.update({'X-API-Key': self.api_key})

    def set_api_key(self, api_key: str):
        self.api_key = api_key
        self._update_headers()

    def _make_request(self, method: str, endpoint: str, params: Dict = None,
                      retry_on_fail: bool = True) -> Tuple[bool, Any]:
        url = f"{self.base_url}{endpoint}"
        try:
            if method.upper() == 'GET':
                response = self.session.get(url, params=params, timeout=10)
            elif method.upper() == 'POST':
                response = self.session.post(url, params=params, timeout=10)
            elif method.upper() == 'DELETE':
                response = self.session.delete(url, params=params, timeout=10)
            else:
                return False, f"Unsupported method: {method}"

            if response.status_code == 429:
                wait_time = float(response.json().get('wait', 1.0))
                logger.warning(f"Rate limited, waiting {wait_time}s")
                time.sleep(wait_time)
                return self._make_request(method, endpoint, params, retry_on_fail=False)

            if response.status_code == 401:
                self.is_connected = False
                self.consecutive_failures += 1
                logger.error("Authentication failed (401)")
                if retry_on_fail:
                    if self.attempt_reconnect():
                        return self._make_request(method, endpoint, params, retry_on_fail=False)
                return False, "Unauthorized"

            if response.ok:
                self.is_connected = True
                self.consecutive_failures = 0
                self.last_successful_call = datetime.now()
                return True, response.json()
            else:
                self.consecutive_failures += 1
                return False, f"HTTP {response.status_code}: {response.text}"

        except requests.exceptions.ConnectionError as e:
            self.is_connected = False
            self.consecutive_failures += 1
            if retry_on_fail:
                if self.attempt_reconnect():
                    return self._make_request(method, endpoint, params, retry_on_fail=False)
            return False, f"Connection error: {e}"
        except requests.exceptions.Timeout:
            self.consecutive_failures += 1
            return False, "Request timed out"
        except Exception as e:
            self.consecutive_failures += 1
            return False, str(e)

    def attempt_reconnect(self) -> bool:
        logger.info("Attempting to reconnect...")
        self.reconnect_attempts += 1
        backoff_delay = min(RECONNECT_DELAY_SEC * (2 ** min(self.reconnect_attempts - 1, 6)), 300)

        if self.reconnect_attempts > MAX_RECONNECT_ATTEMPTS:
            logger.warning(f"Reconnect attempt {self.reconnect_attempts} (still trying)...")

        if not self._is_rit_client_running():
            logger.warning("RIT Client not running, attempting to start...")
            if not self._start_rit_client():
                time.sleep(backoff_delay)
                return False

        time.sleep(3)

        for attempt in range(5):
            try:
                response = self.session.get(f"{self.base_url}/case", timeout=10)
                if response.status_code == 401:
                    logger.warning("API up but not authenticated - attempting auto-login")
                    if self._attempt_auto_login():
                        self.reconnect_attempts = 0
                        self.is_connected = True
                        return True
                    time.sleep(backoff_delay)
                    return False
                elif response.ok:
                    logger.info("Successfully reconnected!")
                    self.is_connected = True
                    self.reconnect_attempts = 0
                    return True
            except:
                time.sleep(3)

        time.sleep(backoff_delay)
        return False

    def _is_rit_client_running(self) -> bool:
        for proc in psutil.process_iter(['name']):
            try:
                if 'RIT' in proc.info['name'] or 'rit' in proc.info['name'].lower():
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False

    def _start_rit_client(self) -> bool:
        possible_paths = [
            r"C:\Program Files\Rotman Interactive Trader\RIT.exe",
            r"C:\Program Files (x86)\Rotman Interactive Trader\RIT.exe",
            r"C:\RIT\RIT.exe",
        ]
        for path in possible_paths:
            try:
                subprocess.Popen([path], shell=True)
                logger.info(f"Started RIT Client from: {path}")
                time.sleep(5)
                return True
            except:
                continue
        logger.error("Could not find or start RIT Client")
        return False

    def _attempt_auto_login(self) -> bool:
        logger.info(f"Attempting auto-login for user: {USERNAME}")
        if not PYAUTOGUI_AVAILABLE:
            logger.warning("pyautogui not available - waiting for manual login...")
            for i in range(60):
                time.sleep(1)
                if self._check_api_accessible():
                    logger.info("Manual login detected!")
                    return True
            return False

        try:
            time.sleep(3)
            rit_window = self._find_rit_window()
            if rit_window:
                try:
                    rit_window.activate()
                    time.sleep(1)
                except:
                    pass

            pyautogui.PAUSE = 0.5
            pyautogui.FAILSAFE = False

            if self._keyboard_based_login():
                time.sleep(5)
                if self._check_api_accessible():
                    logger.info("Keyboard-based login successful!")
                    return True

            for i in range(30):
                time.sleep(1)
                if self._check_api_accessible():
                    logger.info("Login successful!")
                    return True
            return False
        except Exception as e:
            logger.error(f"Auto-login failed: {e}")
            return False

    def _find_rit_window(self):
        if not PYGETWINDOW_AVAILABLE:
            return None
        try:
            for title in ['RIT', 'Rotman', 'Interactive Trader']:
                windows = gw.getWindowsWithTitle(title)
                if windows:
                    return windows[0]
        except:
            pass
        return None

    def _keyboard_based_login(self) -> bool:
        if not PYAUTOGUI_AVAILABLE:
            return False
        try:
            rit_window = self._find_rit_window()
            if rit_window:
                try:
                    rit_window.activate()
                    time.sleep(0.5)
                except:
                    pass

            pyautogui.hotkey('ctrl', 'a')
            pyautogui.typewrite(USERNAME, interval=0.02)
            pyautogui.press('tab')
            time.sleep(0.2)
            pyautogui.hotkey('ctrl', 'a')
            pyautogui.typewrite(PASSWORD, interval=0.02)
            pyautogui.press('tab')
            time.sleep(0.2)
            pyautogui.hotkey('ctrl', 'a')
            pyautogui.typewrite(RIT_SERVER, interval=0.02)
            pyautogui.press('tab')
            time.sleep(0.2)
            pyautogui.hotkey('ctrl', 'a')
            pyautogui.typewrite(str(RIT_PORT), interval=0.02)
            time.sleep(0.3)
            pyautogui.press('enter')
            logger.info("Submitted login credentials via keyboard")
            return True
        except Exception as e:
            logger.error(f"Keyboard-based login failed: {e}")
            return False

    def _check_api_accessible(self) -> bool:
        try:
            response = self.session.get(f"{self.base_url}/case", timeout=5)
            return response.ok
        except:
            return False

    # =============== API Methods ===============

    def get_case(self) -> Tuple[bool, Dict[str, Any]]:
        success, data = self._make_request('GET', '/case')
        if success:
            self.last_tick = data.get('tick', 0)
            self.last_period = data.get('period', 0)
            self.current_status = data.get('status', 'UNKNOWN')
        return success, data

    def get_trader(self) -> Tuple[bool, Dict[str, Any]]:
        return self._make_request('GET', '/trader')

    def get_limits(self) -> Tuple[bool, List[Dict[str, Any]]]:
        return self._make_request('GET', '/limits')

    def get_news(self, since: int = None, limit: int = NEWS_LIMIT) -> Tuple[bool, List[Dict[str, Any]]]:
        params = {'limit': limit}
        if since:
            params['since'] = since
        return self._make_request('GET', '/news', params)

    def get_securities(self, ticker: str = None) -> Tuple[bool, List[Dict[str, Any]]]:
        params = {}
        if ticker:
            params['ticker'] = ticker
        return self._make_request('GET', '/securities', params)

    def get_order_book(self, ticker: str, limit: int = BOOK_DEPTH_LIMIT) -> Tuple[bool, Dict[str, Any]]:
        return self._make_request('GET', '/securities/book',
                                  {'ticker': ticker, 'limit': limit})

    def get_securities_history(self, ticker: str, period: int = None,
                               limit: int = None) -> Tuple[bool, List[Dict[str, Any]]]:
        params = {'ticker': ticker}
        if period is not None:
            params['period'] = period
        if limit is not None:
            params['limit'] = limit
        return self._make_request('GET', '/securities/history', params)

    def get_time_and_sales(self, ticker: str, after: int = None,
                          period: int = None, limit: int = None) -> Tuple[bool, List[Dict[str, Any]]]:
        params = {'ticker': ticker}
        if after is not None:
            params['after'] = after
        if period is not None:
            params['period'] = period
        if limit is not None:
            params['limit'] = limit
        return self._make_request('GET', '/securities/tas', params)

    def get_tenders(self) -> Tuple[bool, List[Dict[str, Any]]]:
        return self._make_request('GET', '/tenders')

    def get_orders(self, status: str = None) -> Tuple[bool, List[Dict[str, Any]]]:
        params = {}
        if status:
            params['status'] = status
        return self._make_request('GET', '/orders', params)

    def submit_order(self, ticker: str, order_type: str, quantity: int,
                     action: str, price: float = None) -> Tuple[bool, Dict[str, Any]]:
        params = {
            'ticker': ticker, 'type': order_type,
            'quantity': quantity, 'action': action
        }
        if price is not None:
            params['price'] = price
        return self._make_request('POST', '/orders', params)

    def cancel_order(self, order_id: int) -> Tuple[bool, Dict[str, Any]]:
        return self._make_request('DELETE', f'/orders/{order_id}')

    def accept_tender(self, tender_id: int, price: float = None) -> Tuple[bool, Dict[str, Any]]:
        params = {}
        if price is not None:
            params['price'] = price
        return self._make_request('POST', f'/tenders/{tender_id}', params)

    def decline_tender(self, tender_id: int) -> Tuple[bool, Dict[str, Any]]:
        return self._make_request('DELETE', f'/tenders/{tender_id}')

    def get_assets(self, ticker: str = None) -> Tuple[bool, List[Dict[str, Any]]]:
        params = {}
        if ticker:
            params['ticker'] = ticker
        return self._make_request('GET', '/assets', params)

    def health_check(self) -> bool:
        success, data = self.get_case()
        return success


class RITClientManager:
    def __init__(self, api_key: str = None):
        self.client = RITClient(api_key)

    def wait_for_connection(self, timeout: int = 300) -> bool:
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.client.health_check():
                return True
            logger.info("Waiting for RIT Client connection...")
            time.sleep(5)
        return False
