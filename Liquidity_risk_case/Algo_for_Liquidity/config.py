# -*- coding: utf-8 -*-
"""Ultimate Liquidity Bot - Configuration.

Uses same API connection pattern as the working volatility algo.
"""

# ========== API Connection (matching volatility algo pattern) ==========
API_HOST = "localhost"
API_PORT = 9999
API_BASE_URL = f"http://{API_HOST}:{API_PORT}/v1"
API_KEY = "LPJEV4YS"

# ========== Position Limits ==========
NET_LIMIT = 150_000       # Buffer below 150K hard limit
GROSS_LIMIT = 250_000     # Buffer below 250K hard limit
MAX_ORDER_SIZE = 10_000   # RIT hard cap per order

# ========== Timing ==========
SLEEP_SEC = 0.08          # Faster than V2 (0.2) for more ticks/sec
MAX_TICK = 600
ENDGAME_TICKS = 130       # Switch to endgame with 130 ticks left
FINAL_SPRINT_TICKS = 25   # Pure flatten sprint in last 25 ticks
ENDGAME_MAX_SLICES = 5    # Max order slices per tick in endgame
BOOK_DEPTH = 100          # Order book depth to fetch

# ========== Tender Acceptance (AGGRESSIVE) ==========
MIN_PROFIT_PER_SHARE = 0.01   # Accept almost any profitable tender
MAX_DEPTH_RATIO = 0.75         # Accept even if liquidity is thin
MIN_CONFIDENCE = 0..075          # Very low confidence threshold
AUCTION_AGGRESSION = 0.005     # Tight margin on auction bids to WIN

# ========== Almgren-Chriss Parameters ==========
AC_GRADIENT_LOW_VOL = 0.5     # Less front-loaded for calm markets
AC_GRADIENT_MED_VOL = 0.85      # Balanced
AC_GRADIENT_HIGH_VOL = 0.9     # More front-loaded for volatile markets
AC_TAU = 1                     # Trade every tick
AC_MIN_HORIZON = 5             # Minimum ticks for AC schedule
AC_FALLBACK_TWAP = True        # Fall back to TWAP if AC fails

# ========== Execution Tuning ==========
MARKETABLE_LIMIT_EPS = 0.01    # Offset for pseudo-marketable limits
NORMAL_MAX_PARTICIPATION = 0.30  # Max fraction of visible depth per order
MIN_BATCH_SIZE = 500           # Don't send tiny orders
IMMEDIATE_UNWIND_THRESHOLD = 2000  # Positions this small: market order

# ========== Sub-Heat Configurations ==========
SUBHEAT_CONFIG = {
    1: {
        'tickers': {'RITC', 'COMP'},
        'commissions': {'RITC': 0.02, 'COMP': 0.02},
        'start_prices': {'RITC': 50, 'COMP': 40},
        'volatility': {'RITC': 'LOW', 'COMP': 'MEDIUM'},
        'liquidity': {'RITC': 'MEDIUM', 'COMP': 'HIGH'},
        'tender_window': 30,
    },
    2: {
        'tickers': {'TRNT', 'MTRL'},
        'commissions': {'TRNT': 0.01, 'MTRL': 0.01},
        'start_prices': {'TRNT': 15, 'MTRL': 30},
        'volatility': {'TRNT': 'HIGH', 'MTRL': 'LOW'},
        'liquidity': {'TRNT': 'MEDIUM', 'MTRL': 'LOW'},
        'tender_window': 30,
    },
    3: {
        'tickers': {'BLU', 'RED', 'GRN'},
        'commissions': {'BLU': 0.04, 'RED': 0.03, 'GRN': 0.02},
        'start_prices': {'BLU': 10, 'RED': 25, 'GRN': 30},
        'volatility': {'BLU': 'HIGH', 'RED': 'LOW', 'GRN': 'MEDIUM'},
        'liquidity': {'BLU': 'HIGH', 'RED': 'MEDIUM', 'GRN': 'MEDIUM'},
        'tender_window': 30,
    },
    4: {
        'tickers': {'WDY', 'BZZ', 'BNN'},
        'commissions': {'WDY': 0.02, 'BZZ': 0.02, 'BNN': 0.03},
        'start_prices': {'WDY': 12, 'BZZ': 18, 'BNN': 24},
        'volatility': {'WDY': 'MEDIUM', 'BZZ': 'HIGH', 'BNN': 'MEDIUM'},
        'liquidity': {'WDY': 'HIGH', 'BZZ': 'MEDIUM', 'BNN': 'MEDIUM'},
        'tender_window': 20,
    },
    5: {
        'tickers': {'VNS', 'MRS', 'JPTR', 'STRN'},
        'commissions': {'VNS': 0.02, 'MRS': 0.02, 'JPTR': 0.02, 'STRN': 0.02},
        'start_prices': {'VNS': 20, 'MRS': 75, 'JPTR': 35, 'STRN': 50},
        'volatility': {'VNS': 'HIGH', 'MRS': 'MEDIUM', 'JPTR': 'LOW', 'STRN': 'HIGH'},
        'liquidity': {'VNS': 'MEDIUM', 'MRS': 'HIGH', 'JPTR': 'MEDIUM', 'STRN': 'MEDIUM'},
        'tender_window': 20,
    },
}

VOL_TO_GRADIENT = {
    'LOW': AC_GRADIENT_LOW_VOL,
    'MEDIUM': AC_GRADIENT_MED_VOL,
    'HIGH': AC_GRADIENT_HIGH_VOL,
}

EXECUTION_PROFILES = {
    ('LOW', 'HIGH'):    {'participation': 0.25, 'limit_eps': 0.005, 'be_slack': 0.01},
    ('LOW', 'MEDIUM'):  {'participation': 0.18, 'limit_eps': 0.005, 'be_slack': 0.01},
    ('LOW', 'LOW'):     {'participation': 0.12, 'limit_eps': 0.003, 'be_slack': 0.005},
    ('MEDIUM', 'HIGH'): {'participation': 0.28, 'limit_eps': 0.01,  'be_slack': 0.02},
    ('MEDIUM', 'MEDIUM'):{'participation': 0.20, 'limit_eps': 0.01, 'be_slack': 0.015},
    ('MEDIUM', 'LOW'):  {'participation': 0.14, 'limit_eps': 0.008, 'be_slack': 0.01},
    ('HIGH', 'HIGH'):   {'participation': 0.30, 'limit_eps': 0.02,  'be_slack': 0.03},
    ('HIGH', 'MEDIUM'): {'participation': 0.22, 'limit_eps': 0.015, 'be_slack': 0.025},
    ('HIGH', 'LOW'):    {'participation': 0.15, 'limit_eps': 0.01,  'be_slack': 0.02},
}

# ========== Pre-calculated Volatility (per-tick) ==========
# Calculate from historical data: std_dev of (price[t] - price[t-1]) / price[t-1]
# If None, will use dynamic calculation from live mid-price history
STATIC_VOLATILITY = {
    # Example format - replace with your calculated values:
    # 'RITC': 0.0005,   # LOW volatility
    # 'COMP': 0.0012,   # MEDIUM volatility
    # 'TRNT': 0.0025,   # HIGH volatility
    # Add your tickers here with pre-calculated volatility values
}

EPS = 1e-9
