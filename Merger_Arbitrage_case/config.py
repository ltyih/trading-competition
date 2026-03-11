"""
Configuration for RIT Merger Arbitrage Case Data Collector

Tracks 10 securities (5 targets + 5 acquirers) across 5 M&A deals.
News-driven case where deal probabilities are the primary signal.
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
# RIT CONNECTION
# =============================================================================
RIT_SERVER = "flserver.*REMOVED*.utoronto.ca"
RIT_PORT = 16540

API_HOST = "localhost"
API_PORT = 9998
API_BASE_URL = f"http://{API_HOST}:{API_PORT}/v1"

# Login Credentials
USERNAME = "UBCT-4"
PASSWORD = "target"

# API Key - update after getting from RIT Client
API_KEY = "AJDSYHVCES"

# =============================================================================
# DATA COLLECTION SETTINGS
# =============================================================================
POLL_INTERVAL_SEC = 0.5       # Poll every 0.5 seconds
BOOK_DEPTH_LIMIT = 50         # Order book depth
NEWS_LIMIT = 100              # Fetch more news - NEWS IS CRITICAL in this case
TAS_LIMIT = 100               # Time & sales limit

# Auto-reconnect
MAX_RECONNECT_ATTEMPTS = 10
RECONNECT_DELAY_SEC = 5
HEALTH_CHECK_INTERVAL_SEC = 30

# =============================================================================
# DATABASE SETTINGS
# =============================================================================
DB_NAME = "merger_arb_data.db"
DB_PATH = DATA_DIR / DB_NAME
SEPARATE_DB_PER_SESSION = True
SESSIONS_DIR = DATA_DIR / "sessions"
SESSIONS_DIR.mkdir(exist_ok=True)

# CSV/Excel Export
EXPORT_CSV = False
CSV_EXPORT_INTERVAL_SEC = 300

# Logging
LOG_LEVEL = "INFO"
LOG_FILE = LOGS_DIR / "data_collector.log"

# =============================================================================
# MERGER ARBITRAGE CASE - SECURITIES & DEAL STRUCTURES
# =============================================================================

# All tradeable securities (5 targets + 5 acquirers)
KNOWN_SECURITIES = {
    "TGX", "PHR",   # D1: Targenix / Pharmaco (All-cash)
    "BYL", "CLD",   # D2: ByteLayer / CloudSys (Stock-for-stock)
    "GGD", "PNR",   # D3: GreenGrid / PetroNorth (Mixed)
    "FSR", "ATB",   # D4: FinSure / Atlas Bank (All-cash)
    "SPK", "EEC",   # D5: SolarPeak / EastEnergy (Stock-for-stock)
}

# Deal definitions with all parameters from the case package
DEALS = {
    "D1": {
        "name": "Pharmaceuticals",
        "target": "TGX", "acquirer": "PHR",
        "structure": "all-cash",
        "deal_terms": {"cash": 50.0, "ratio": 0.0},
        "target_start": 43.70, "acquirer_start": 47.50,
        "initial_prob": 0.70,
        "sensitivity_multiplier": 1.00,
    },
    "D2": {
        "name": "Cloud Software",
        "target": "BYL", "acquirer": "CLD",
        "structure": "stock-for-stock",
        "deal_terms": {"cash": 0.0, "ratio": 0.75},
        "target_start": 43.50, "acquirer_start": 79.30,
        "initial_prob": 0.55,
        "sensitivity_multiplier": 1.05,
    },
    "D3": {
        "name": "Energy / Infrastructure",
        "target": "GGD", "acquirer": "PNR",
        "structure": "mixed",
        "deal_terms": {"cash": 33.0, "ratio": 0.20},
        "target_start": 31.50, "acquirer_start": 59.80,
        "initial_prob": 0.50,
        "sensitivity_multiplier": 1.10,
    },
    "D4": {
        "name": "Banking",
        "target": "FSR", "acquirer": "ATB",
        "structure": "all-cash",
        "deal_terms": {"cash": 40.0, "ratio": 0.0},
        "target_start": 30.50, "acquirer_start": 62.20,
        "initial_prob": 0.38,
        "sensitivity_multiplier": 1.30,
    },
    "D5": {
        "name": "Renewable Energy",
        "target": "SPK", "acquirer": "EEC",
        "structure": "stock-for-stock",
        "deal_terms": {"cash": 0.0, "ratio": 1.20},
        "target_start": 52.80, "acquirer_start": 48.00,
        "initial_prob": 0.45,
        "sensitivity_multiplier": 1.15,
    },
}

# Reverse lookup: ticker -> deal_id
TICKER_TO_DEAL = {}
for deal_id, deal in DEALS.items():
    TICKER_TO_DEAL[deal["target"]] = deal_id
    TICKER_TO_DEAL[deal["acquirer"]] = deal_id

# News category multipliers (from case package)
CATEGORY_MULTIPLIERS = {
    "REG": 1.25,
    "FIN": 1.00,
    "SHR": 0.90,
    "ALT": 1.40,
    "PRC": 0.70,
}

# Baseline impact by direction and severity (probability points)
NEWS_IMPACT = {
    "positive": {"small": 0.03, "medium": 0.07, "large": 0.14},
    "negative": {"small": -0.04, "medium": -0.09, "large": -0.18},
    "ambiguous": {"small": 0.00, "medium": 0.00, "large": 0.00},
}

# Commission rate
COMMISSION_PER_SHARE = 0.02

# Trading limits (from case package)
GROSS_LIMIT = 100000
NET_LIMIT = 50000
MAX_ORDER_SIZE = 5000

# =============================================================================
# ENHANCED DATA COLLECTION
# =============================================================================
ANON_TRADER_IDS = {"ANON", "anon", "MM", "mm", "MARKET", "market", None, ""}
SAVE_TICK_SNAPSHOTS = True
TRACK_PLAYER_ORDERS = True
PLAYER_ORDER_BOOK_DEPTH = 20

# Deal spread tracking - compute every poll cycle
TRACK_DEAL_SPREADS = True
