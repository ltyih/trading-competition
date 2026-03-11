"""
RIT Client API wrapper with auto-reconnection and auto-login capabilities
Supports 24/7 operation with automatic recovery from disconnections.
"""
import requests
import subprocess
import time
import logging
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime
import psutil

# GUI automation imports with fallback
try:
    import pyautogui
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False
    logging.warning("pyautogui not available - GUI automation disabled")

try:
    import pygetwindow as gw
    PYGETWINDOW_AVAILABLE = True
except ImportError:
    PYGETWINDOW_AVAILABLE = False
    logging.warning("pygetwindow not available - window management disabled")

from config import (
    API_BASE_URL, API_KEY, API_HOST, API_PORT,
    RIT_SERVER, RIT_PORT, USERNAME, PASSWORD,
    MAX_RECONNECT_ATTEMPTS, RECONNECT_DELAY_SEC,
    BOOK_DEPTH_LIMIT, NEWS_LIMIT
)

logger = logging.getLogger(__name__)


class *REMOVED*lientError(Exception):
    """Custom exception for RIT Client errors."""
    pass


class AuthenticationError(*REMOVED*lientError):
    """Raised when authentication fails."""
    pass


class ConnectionError(*REMOVED*lientError):
    """Raised when connection fails."""
    pass


class *REMOVED*lient:
    """
    RIT Client REST API wrapper with auto-reconnection.

    This client connects to the local RIT Client application's REST API.
    The RIT Client must be running and logged in for the API to work.
    """

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

        # Track last known good state
        self.last_successful_call = None
        self.consecutive_failures = 0

    def _update_headers(self):
        """Update session headers with API key."""
        if self.api_key:
            self.session.headers.update({'X-API-Key': self.api_key})

    def set_api_key(self, api_key: str):
        """Set the API key."""
        self.api_key = api_key
        self._update_headers()

    def _make_request(self, method: str, endpoint: str, params: Dict = None,
                      retry_on_fail: bool = True) -> Tuple[bool, Any]:
        """
        Make an API request with error handling.

        Returns: (success: bool, data: Any)
        """
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

            # Handle rate limiting
            if response.status_code == 429:
                wait_time = float(response.json().get('wait', 1.0))
                logger.warning(f"Rate limited, waiting {wait_time}s")
                time.sleep(wait_time)
                return self._make_request(method, endpoint, params, retry_on_fail=False)

            # Handle unauthorized (logged out or bad API key)
            if response.status_code == 401:
                self.is_connected = False
                self.consecutive_failures += 1
                logger.error("Authentication failed (401) - client may be logged out")

                if retry_on_fail:
                    # Try to reconnect
                    if self.attempt_reconnect():
                        return self._make_request(method, endpoint, params, retry_on_fail=False)

                return False, "Unauthorized - need to re-login"

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
            logger.error(f"Connection error: {e}")

            if retry_on_fail:
                if self.attempt_reconnect():
                    return self._make_request(method, endpoint, params, retry_on_fail=False)

            return False, f"Connection error: {e}"

        except requests.exceptions.Timeout:
            self.consecutive_failures += 1
            logger.error("Request timed out")
            return False, "Request timed out"

        except Exception as e:
            self.consecutive_failures += 1
            logger.error(f"Unexpected error: {e}")
            return False, str(e)

    def attempt_reconnect(self) -> bool:
        """
        Attempt to reconnect to the RIT Client.

        This checks if:
        1. RIT Client process is running
        2. API is accessible
        3. User is logged in

        If not, it attempts to restart/re-login.

        For 24/7 operation, this will continue trying with exponential backoff.
        """
        logger.info("Attempting to reconnect...")
        self.reconnect_attempts += 1

        # Calculate backoff delay (exponential with max cap at 5 minutes)
        backoff_delay = min(RECONNECT_DELAY_SEC * (2 ** min(self.reconnect_attempts - 1, 6)), 300)

        # Log warning if many attempts but continue trying for 24/7 operation
        if self.reconnect_attempts > MAX_RECONNECT_ATTEMPTS:
            logger.warning(f"Reconnect attempt {self.reconnect_attempts} (still trying for 24/7 operation)...")

        # Check if RIT Client is running
        if not self._is_rit_client_running():
            logger.warning("RIT Client not running, attempting to start...")
            if not self._start_rit_client():
                logger.error("Failed to start RIT Client")
                time.sleep(backoff_delay)
                return False

        # Wait for client to be ready
        time.sleep(3)

        # Try to ping the API
        for attempt in range(5):
            try:
                response = self.session.get(f"{self.base_url}/case", timeout=10)
                if response.status_code == 401:
                    # API is up but needs login
                    logger.warning("API accessible but not authenticated - attempting auto-login")
                    if self._attempt_auto_login():
                        self.reconnect_attempts = 0  # Reset on success
                        self.is_connected = True
                        return True
                    else:
                        # Don't return False immediately, will retry in outer loop
                        logger.warning("Auto-login attempt failed, will retry...")
                        time.sleep(backoff_delay)
                        return False
                elif response.ok:
                    logger.info("Successfully reconnected!")
                    self.is_connected = True
                    self.reconnect_attempts = 0  # Reset on success
                    return True
            except requests.exceptions.ConnectionError:
                logger.debug(f"Connection attempt {attempt + 1}/5 failed, retrying...")
                time.sleep(3)
            except Exception as e:
                logger.debug(f"Connection attempt {attempt + 1}/5 error: {e}")
                time.sleep(3)

        time.sleep(backoff_delay)
        return False

    def _is_rit_client_running(self) -> bool:
        """Check if RIT Client process is running."""
        for proc in psutil.process_iter(['name']):
            try:
                if 'RIT' in proc.info['name'] or 'rit' in proc.info['name'].lower():
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False

    def _start_rit_client(self) -> bool:
        """
        Attempt to start the RIT Client application.

        Note: This requires the RIT Client to be installed and the path to be known.
        """
        logger.info("Attempting to start RIT Client...")

        # Common RIT Client paths on Windows
        possible_paths = [
            r"C:\Program Files\*REMOVED* Interactive Trader\RIT.exe",
            r"C:\Program Files (x86)\*REMOVED* Interactive Trader\RIT.exe",
            r"C:\RIT\RIT.exe",
        ]

        for path in possible_paths:
            try:
                subprocess.Popen([path], shell=True)
                logger.info(f"Started RIT Client from: {path}")
                time.sleep(5)  # Wait for client to initialize
                return True
            except Exception as e:
                continue

        logger.error("Could not find or start RIT Client")
        return False

    def _attempt_auto_login(self) -> bool:
        """
        Attempt automatic login using GUI automation.

        This uses pyautogui and pygetwindow to interact with the RIT Client login window.
        Supports 24/7 operation with robust error handling.
        """
        logger.info(f"Attempting auto-login for user: {USERNAME}")

        # Check if GUI automation is available
        if not PYAUTOGUI_AVAILABLE:
            logger.warning("pyautogui not available - waiting for manual login...")
            # Wait and poll for manual login
            for i in range(60):
                time.sleep(1)
                if self._check_api_accessible():
                    logger.info("Manual login detected - connection restored!")
                    return True
                if i % 15 == 0 and i > 0:
                    logger.info(f"Waiting for manual login... ({i}/60s)")
            return False

        try:
            # Give the login window time to appear
            time.sleep(3)

            # Try to find and focus the RIT Client window
            rit_window = self._find_rit_window()
            if rit_window:
                logger.info("Found RIT Client window, attempting to focus...")
                try:
                    rit_window.activate()
                    time.sleep(1)
                except Exception as e:
                    logger.warning(f"Could not activate window: {e}")

            # Configure pyautogui for reliability
            pyautogui.PAUSE = 0.5
            pyautogui.FAILSAFE = False  # Disable failsafe for 24/7 operation

            # Method 1: Try keyboard-based login FIRST (most reliable)
            logger.info("Attempting keyboard-based login...")
            if self._keyboard_based_login():
                time.sleep(5)
                if self._check_api_accessible():
                    logger.info("Keyboard-based login successful!")
                    return True
                logger.info("Credentials entered, waiting for connection...")

            # Method 2: Wait and poll for successful login
            logger.info("Waiting for login to complete...")
            max_wait = 30  # Wait up to 30 seconds
            for i in range(max_wait):
                time.sleep(1)
                if self._check_api_accessible():
                    logger.info("Login successful!")
                    return True
                if i % 10 == 0 and i > 0:
                    logger.info(f"Still waiting for login... ({i}/{max_wait}s)")

            logger.warning("Auto-login could not complete. Will retry...")
            return False

        except Exception as e:
            logger.error(f"Auto-login failed: {e}")
            return False

    def _find_rit_window(self):
        """Find the RIT Client window."""
        if not PYGETWINDOW_AVAILABLE:
            return None

        try:
            # Look for windows with RIT in the title
            windows = gw.getWindowsWithTitle('RIT')
            if not windows:
                windows = gw.getWindowsWithTitle('*REMOVED*')
            if not windows:
                windows = gw.getWindowsWithTitle('Interactive Trader')
            if windows:
                return windows[0]
        except Exception as e:
            logger.debug(f"Could not find RIT window: {e}")
        return None

    def _try_click_connect_button(self) -> bool:
        """Try to find and click the Connect button in RIT Client."""
        if not PYAUTOGUI_AVAILABLE:
            return False

        try:
            # Look for the Connect button on screen
            connect_btn = pyautogui.locateOnScreen('connect_button.png', confidence=0.8)
            if connect_btn:
                pyautogui.click(pyautogui.center(connect_btn))
                logger.info("Clicked Connect button")
                return True
        except Exception as e:
            logger.debug(f"Could not find Connect button: {e}")

        # Try clicking at common button positions (fallback)
        try:
            rit_window = self._find_rit_window()
            if rit_window:
                # Click near common Connect button positions
                center_x = rit_window.left + rit_window.width // 2
                center_y = rit_window.top + rit_window.height // 2
                # Try clicking Connect button area (usually near center-bottom of login dialog)
                pyautogui.click(center_x, center_y + 50)
                return True
        except Exception as e:
            logger.debug(f"Fallback click failed: {e}")

        return False

    def _keyboard_based_login(self) -> bool:
        """Attempt login using keyboard input."""
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

            # RIT Login dialog field order: Trader ID, Password, Server, Port
            logger.info(f"Typing Trader ID: {USERNAME}")
            pyautogui.hotkey('ctrl', 'a')  # Select all
            pyautogui.typewrite(USERNAME, interval=0.02)
            pyautogui.press('tab')
            time.sleep(0.2)

            # Password field
            logger.info("Typing Password...")
            pyautogui.hotkey('ctrl', 'a')
            pyautogui.typewrite(PASSWORD, interval=0.02)
            pyautogui.press('tab')
            time.sleep(0.2)

            # Server field
            logger.info(f"Typing Server: {RIT_SERVER}")
            pyautogui.hotkey('ctrl', 'a')
            pyautogui.typewrite(RIT_SERVER, interval=0.02)
            pyautogui.press('tab')
            time.sleep(0.2)

            # Port field
            logger.info(f"Typing Port: {RIT_PORT}")
            pyautogui.hotkey('ctrl', 'a')
            pyautogui.typewrite(str(RIT_PORT), interval=0.02)

            # Press Enter to submit
            time.sleep(0.3)
            pyautogui.press('enter')
            logger.info("Submitted login credentials via keyboard")
            return True

        except Exception as e:
            logger.error(f"Keyboard-based login failed: {e}")
            return False

    def _check_api_accessible(self) -> bool:
        """Quick check if API is accessible and authenticated."""
        try:
            response = self.session.get(f"{self.base_url}/case", timeout=5)
            return response.ok
        except:
            return False

    # =============== API Methods ===============

    def get_case(self) -> Tuple[bool, Dict[str, Any]]:
        """Get current case information."""
        success, data = self._make_request('GET', '/case')
        if success:
            self.last_tick = data.get('tick', 0)
            self.last_period = data.get('period', 0)
            self.current_status = data.get('status', 'UNKNOWN')
        return success, data

    def get_trader(self) -> Tuple[bool, Dict[str, Any]]:
        """Get trader information."""
        return self._make_request('GET', '/trader')

    def get_limits(self) -> Tuple[bool, List[Dict[str, Any]]]:
        """Get trading limits."""
        return self._make_request('GET', '/limits')

    def get_news(self, since: int = None, limit: int = NEWS_LIMIT) -> Tuple[bool, List[Dict[str, Any]]]:
        """Get news items."""
        params = {'limit': limit}
        if since:
            params['since'] = since
        return self._make_request('GET', '/news', params)

    def get_securities(self, ticker: str = None) -> Tuple[bool, List[Dict[str, Any]]]:
        """Get securities information."""
        params = {}
        if ticker:
            params['ticker'] = ticker
        return self._make_request('GET', '/securities', params)

    def get_order_book(self, ticker: str, limit: int = BOOK_DEPTH_LIMIT) -> Tuple[bool, Dict[str, Any]]:
        """Get order book for a security."""
        return self._make_request('GET', '/securities/book',
                                  {'ticker': ticker, 'limit': limit})

    def get_securities_history(self, ticker: str, period: int = None,
                               limit: int = None) -> Tuple[bool, List[Dict[str, Any]]]:
        """Get OHLC price history."""
        params = {'ticker': ticker}
        if period is not None:
            params['period'] = period
        if limit is not None:
            params['limit'] = limit
        return self._make_request('GET', '/securities/history', params)

    def get_time_and_sales(self, ticker: str, after: int = None,
                          period: int = None, limit: int = None) -> Tuple[bool, List[Dict[str, Any]]]:
        """Get time & sales data."""
        params = {'ticker': ticker}
        if after is not None:
            params['after'] = after
        if period is not None:
            params['period'] = period
        if limit is not None:
            params['limit'] = limit
        return self._make_request('GET', '/securities/tas', params)

    def get_tenders(self) -> Tuple[bool, List[Dict[str, Any]]]:
        """Get active tender offers."""
        return self._make_request('GET', '/tenders')

    def get_orders(self, status: str = None) -> Tuple[bool, List[Dict[str, Any]]]:
        """Get orders."""
        params = {}
        if status:
            params['status'] = status
        return self._make_request('GET', '/orders', params)

    def get_order(self, order_id: int) -> Tuple[bool, Dict[str, Any]]:
        """Get a specific order."""
        return self._make_request('GET', f'/orders/{order_id}')

    def submit_order(self, ticker: str, order_type: str, quantity: int,
                     action: str, price: float = None) -> Tuple[bool, Dict[str, Any]]:
        """Submit a new order."""
        params = {
            'ticker': ticker,
            'type': order_type,
            'quantity': quantity,
            'action': action
        }
        if price is not None:
            params['price'] = price
        return self._make_request('POST', '/orders', params)

    def cancel_order(self, order_id: int) -> Tuple[bool, Dict[str, Any]]:
        """Cancel an order."""
        return self._make_request('DELETE', f'/orders/{order_id}')

    def accept_tender(self, tender_id: int, price: float = None) -> Tuple[bool, Dict[str, Any]]:
        """Accept a tender offer."""
        params = {}
        if price is not None:
            params['price'] = price
        return self._make_request('POST', f'/tenders/{tender_id}', params)

    def decline_tender(self, tender_id: int) -> Tuple[bool, Dict[str, Any]]:
        """Decline a tender offer."""
        return self._make_request('DELETE', f'/tenders/{tender_id}')

    def get_assets(self, ticker: str = None) -> Tuple[bool, List[Dict[str, Any]]]:
        """Get available assets."""
        params = {}
        if ticker:
            params['ticker'] = ticker
        return self._make_request('GET', '/assets', params)

    def health_check(self) -> bool:
        """
        Perform a health check on the connection.

        Returns True if the API is accessible and authenticated.
        """
        success, data = self.get_case()
        return success


class *REMOVED*lientManager:
    """
    Manager class for handling RIT Client lifecycle and monitoring.

    This class provides higher-level management including:
    - Continuous monitoring
    - Automatic recovery
    - Status reporting
    """

    def __init__(self, api_key: str = None):
        self.client = *REMOVED*lient(api_key)
        self.is_monitoring = False
        self.status_callbacks = []

    def add_status_callback(self, callback):
        """Add a callback to be called when status changes."""
        self.status_callbacks.append(callback)

    def _notify_status_change(self, status: str, message: str):
        """Notify all callbacks of a status change."""
        for callback in self.status_callbacks:
            try:
                callback(status, message)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    def wait_for_connection(self, timeout: int = 300) -> bool:
        """
        Wait for a successful connection to the RIT Client.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if connected, False if timeout
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            if self.client.health_check():
                self._notify_status_change("CONNECTED", "Successfully connected to RIT Client")
                return True

            logger.info("Waiting for RIT Client connection...")
            time.sleep(5)

        self._notify_status_change("TIMEOUT", "Connection timeout exceeded")
        return False

    def get_status(self) -> Dict[str, Any]:
        """Get current status of the client."""
        return {
            'is_connected': self.client.is_connected,
            'last_tick': self.client.last_tick,
            'last_period': self.client.last_period,
            'current_status': self.client.current_status,
            'consecutive_failures': self.client.consecutive_failures,
            'last_successful_call': self.client.last_successful_call.isoformat()
                if self.client.last_successful_call else None,
            'reconnect_attempts': self.client.reconnect_attempts
        }


if __name__ == "__main__":
    # Test the client
    logging.basicConfig(level=logging.INFO)

    # You need to provide your API key here or get it from RIT Client
    client = *REMOVED*lient(api_key="YOUR_API_KEY_HERE")

    print("Testing RIT Client connection...")
    success, data = client.get_case()

    if success:
        print(f"Connected! Case: {data.get('name')}, Tick: {data.get('tick')}")
    else:
        print(f"Connection failed: {data}")
