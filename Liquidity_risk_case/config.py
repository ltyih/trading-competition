"""
Configuration for RIT Data Collector
"""
import os
from pathlib import Path

# Base paths
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# RIT Server Configuration
RIT_SERVER = "flserver.rotman.utoronto.ca"
RIT_PORT = 16500

# RIT Client REST API Configuration (connects to local RIT Client)
API_HOST = "localhost"
API_PORT = 9998
API_BASE_URL = f"http://{API_HOST}:{API_PORT}/v1"

# Login Credentials
USERNAME = "UBCT-1"
PASSWORD = "target"

# API Key (will be retrieved from RIT Client after login)
# This should be updated after first successful connection
API_KEY = "AJDSYHVCES"  # Will be set dynamically or can be hardcoded after getting from RIT Client

# Data Collection Settings
POLL_INTERVAL_SEC = 0.5  # How often to poll for data
BOOK_DEPTH_LIMIT = 50  # Order book depth
NEWS_LIMIT = 50  # Number of news items to fetch
TAS_LIMIT = 100  # Time & sales limit per tick

# Auto-reconnect settings
MAX_RECONNECT_ATTEMPTS = 10
RECONNECT_DELAY_SEC = 5
HEALTH_CHECK_INTERVAL_SEC = 30

# Database settings
DB_NAME = "rit_data.db"
DB_PATH = DATA_DIR / DB_NAME

# Session-based storage (recommended for easier analysis)
# If True, creates a new database file for each session
SEPARATE_DB_PER_SESSION = True
SESSIONS_DIR = DATA_DIR / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

# CSV Export settings
EXPORT_CSV = True
CSV_EXPORT_INTERVAL_SEC = 300  # Export to CSV every 5 minutes

# Logging settings
LOG_LEVEL = "INFO"
LOG_FILE = LOGS_DIR / "data_collector.log"

# Securities to track - will be dynamically updated from case
# These are known securities from the Liquidity Risk Case
KNOWN_SECURITIES = {
    # Sub-heat 1
    "RITC", "COMP",
    # Sub-heat 2
    "TRNT", "MTRL",
    # Sub-heat 3
    "BLU", "RED", "GRN",
    # Sub-heat 4
    "WDY", "BZZ", "BNN",
    # Sub-heat 5
    "VNS", "MRS", "JPTR", "STRN",
    # From LT3 case (practice)
    "CRZY", "TAME"
}

# Commission rates by security (from case brief)
COMMISSIONS = {
    # Sub-heat 1
    "RITC": 0.02, "COMP": 0.02,
    # Sub-heat 2
    "TRNT": 0.01, "MTRL": 0.01,
    # Sub-heat 3
    "BLU": 0.04, "RED": 0.03, "GRN": 0.02,
    # Sub-heat 4
    "WDY": 0.02, "BZZ": 0.02, "BNN": 0.03,
    # Sub-heat 5
    "VNS": 0.02, "MRS": 0.02, "JPTR": 0.02, "STRN": 0.02,
    # Default
    "default": 0.02
}

# Trading limits
GROSS_LIMIT = 250000
NET_LIMIT = 150000
MAX_ORDER_SIZE = 10000
