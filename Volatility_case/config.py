"""
Configuration for RIT Volatility Case Data Collector

Enhanced for options trading with Greeks tracking, implied volatility,
and portfolio delta monitoring.
"""
import os
import socket
import getpass
import uuid
from pathlib import Path

# Base paths
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# =============================================================================
# COLLECTOR IDENTIFICATION
# =============================================================================
def get_collector_id() -> str:
    """
    Generate a unique collector ID combining:
    - Machine hostname
    - Username
    - Short UUID for this session
    """
    hostname = socket.gethostname()
    hostname = "".join(c if c.isalnum() else "_" for c in hostname)[:20]

    username = getpass.getuser()
    username = "".join(c if c.isalnum() else "_" for c in username)[:15]

    session_uuid = uuid.uuid4().hex[:8]
    return f"{hostname}_{username}_{session_uuid}"

COLLECTOR_ID = get_collector_id()
COLLECTOR_HOSTNAME = socket.gethostname()
COLLECTOR_USERNAME = getpass.getuser()
COLLECTOR_NAME = os.environ.get("RIT_COLLECTOR_NAME", None)

# =============================================================================
# RIT SERVER CONFIGURATION
# =============================================================================
RIT_SERVER = "flserver.rotman.utoronto.ca"
RIT_PORT = 14950

# RIT Client REST API Configuration
API_HOST = "localhost"
API_PORT = 10000
API_BASE_URL = f"http://{API_HOST}:{API_PORT}/v1"

# Login Credentials
USERNAME = "UBCT-4"
PASSWORD = "target"

# API Key
API_KEY = "AJDSYHVC"

# =============================================================================
# DATA COLLECTION SETTINGS
# =============================================================================
POLL_INTERVAL_SEC = 0.5  # How often to poll for data
BOOK_DEPTH_LIMIT = 50    # Order book depth
NEWS_LIMIT = 50          # Number of news items to fetch
TAS_LIMIT = 100          # Time & sales limit per tick

# Auto-reconnect settings
MAX_RECONNECT_ATTEMPTS = 10
RECONNECT_DELAY_SEC = 5
HEALTH_CHECK_INTERVAL_SEC = 30

# Database settings
DB_NAME = "volatility_data.db"
DB_PATH = DATA_DIR / DB_NAME

# Session-based storage
SEPARATE_DB_PER_SESSION = True
SESSIONS_DIR = DATA_DIR / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

# CSV Export settings
EXPORT_CSV = False
CSV_EXPORT_INTERVAL_SEC = 300

# Logging settings
LOG_LEVEL = "INFO"
LOG_FILE = LOGS_DIR / "data_collector.log"

# =============================================================================
# VOLATILITY CASE SECURITIES
# =============================================================================
# Underlying ETF
UNDERLYING_TICKER = "RTM"

# Strike prices for options (45-54)
STRIKE_PRICES = [45, 46, 47, 48, 49, 50, 51, 52, 53, 54]

# Generate all option tickers
CALL_OPTIONS = [f"RTM1C{strike}" for strike in STRIKE_PRICES]
PUT_OPTIONS = [f"RTM1P{strike}" for strike in STRIKE_PRICES]

# All securities to track
KNOWN_SECURITIES = {UNDERLYING_TICKER} | set(CALL_OPTIONS) | set(PUT_OPTIONS)

# Options metadata
OPTIONS_METADATA = {}
for strike in STRIKE_PRICES:
    OPTIONS_METADATA[f"RTM1C{strike}"] = {
        "type": "CALL",
        "strike": strike,
        "underlying": UNDERLYING_TICKER
    }
    OPTIONS_METADATA[f"RTM1P{strike}"] = {
        "type": "PUT",
        "strike": strike,
        "underlying": UNDERLYING_TICKER
    }

# =============================================================================
# VOLATILITY CASE PARAMETERS
# =============================================================================
# Case structure
TICKS_PER_SUB_HEAT = 300  # 300 seconds (5 minutes)
WEEKS_PER_SUB_HEAT = 4
TICKS_PER_WEEK = 75  # 300 / 4

# Week boundaries (tick ranges)
WEEK_BOUNDARIES = {
    1: (1, 75),
    2: (76, 150),
    3: (151, 225),
    4: (226, 300)
}

# Mid-week volatility forecast ticks
MID_WEEK_TICKS = [37, 112, 187, 262]  # Mid-point of each week

# Trading year assumptions (from case brief)
TRADING_DAYS_PER_YEAR = 240
TRADING_DAYS_PER_MONTH = 20

# =============================================================================
# TRADING LIMITS (from case brief)
# =============================================================================
# RTM ETF limits
RTM_GROSS_LIMIT = 50000  # shares
RTM_NET_LIMIT = 50000    # shares
RTM_MAX_TRADE_SIZE = 10000  # shares per order

# Options limits
OPTIONS_GROSS_LIMIT = 2500  # contracts
OPTIONS_NET_LIMIT = 1000    # contracts
OPTIONS_MAX_TRADE_SIZE = 100  # contracts per order

# Contract multiplier (each option contract = 100 shares)
OPTIONS_MULTIPLIER = 100

# Transaction fees
RTM_TRANSACTION_FEE = 0.01  # $0.01 per share
OPTIONS_TRANSACTION_FEE = 1.00  # $1.00 per contract

# Default delta limit (will be updated from news)
DEFAULT_DELTA_LIMIT = 10000
DEFAULT_PENALTY_RATE = 0.01  # 1%

# =============================================================================
# ENHANCED DATA COLLECTION SETTINGS
# =============================================================================
# Filter settings for player limit orders
ANON_TRADER_IDS = {"ANON", "anon", "MM", "mm", "MARKET", "market", None, ""}

# Tick snapshot settings
SAVE_TICK_SNAPSHOTS = True
TRACK_PLAYER_ORDERS = True
PLAYER_ORDER_BOOK_DEPTH = 20

# Options-specific tracking
TRACK_GREEKS = True
TRACK_IMPLIED_VOLATILITY = True
TRACK_PORTFOLIO_DELTA = True

# Greeks calculation settings (if not provided by API)
RISK_FREE_RATE = 0.0  # Typically 0 for short-term options in RIT
CALCULATE_GREEKS_IF_MISSING = True

# =============================================================================
# NEWS PARSING PATTERNS
# =============================================================================
import re

# Patterns for parsing volatility announcements from news
VOLATILITY_PATTERNS = {
    "current_vol": re.compile(
        r"realized volatility.*?(?:this week|for this week).*?(\d+(?:\.\d+)?)\s*%",
        re.IGNORECASE
    ),
    "forecast_vol_range": re.compile(
        r"next week.*?between\s*(\d+(?:\.\d+)?)\s*%?\s*and\s*(\d+(?:\.\d+)?)\s*%",
        re.IGNORECASE
    ),
    "delta_limit": re.compile(
        r"delta limit.*?(\d+(?:,\d+)?)",
        re.IGNORECASE
    ),
    "penalty_rate": re.compile(
        r"penalty.*?(\d+(?:\.\d+)?)\s*%",
        re.IGNORECASE
    )
}
