"""
Configuration for Volatility Algorithm V10 - Liam's Optimal Straddle Method.

Based on "Optimisation method for options" by Liam Yih (Aug 2025).
Uses mathematical optimization (eq 5) to find the optimal number of
delta-hedged straddles per week.
"""

# =============================================================================
# RIT API
# =============================================================================
# RIT Server (for GUI login - not used by algo directly)
RIT_SERVER = "flserver.rotman.utoronto.ca"
RIT_SERVER_PORT = 14950
USERNAME = "UBCT-4"
PASSWORD = "target"

# Local RIT Client REST API (our algo connects here)
API_HOST = "localhost"
API_PORT = 9999      # Default RIT Client API port - check your RIT Client settings
API_BASE_URL = f"http://{API_HOST}:{API_PORT}/v1"
API_KEY = "AJDSYHVC"     # Confirmed working

# =============================================================================
# SECURITIES
# =============================================================================
UNDERLYING_TICKER = "RTM"
STRIKE_PRICES = [45, 46, 47, 48, 49, 50, 51, 52, 53, 54]
OPTIONS_MULTIPLIER = 100

# =============================================================================
# CASE TIMING
# =============================================================================
TICKS_PER_SUBHEAT = 300
TICKS_PER_WEEK = 75
TRADING_DAYS_PER_YEAR = 240
TRADING_DAYS_PER_MONTH = 20

# Ticks at which to take new straddle positions (start of each week)
POSITION_TICKS = [1, 74, 149, 224]

# Scheduled gradual close windows for weeks 1-4.
# Close starts at each start tick and must be complete by the deadline tick.
WEEKLY_CLOSE_START_TICKS = [35, 111, 186, 255]
WEEKLY_CLOSE_DEADLINE_TICKS = [40, 116, 191, 260]

# =============================================================================
# TRADING LIMITS (case rules - do not change)
# =============================================================================
RTM_GROSS_LIMIT = 50000
RTM_NET_LIMIT = 50000
RTM_MAX_TRADE_SIZE = 10000

OPTIONS_GROSS_LIMIT = 2500
OPTIONS_NET_LIMIT = 1000
OPTIONS_MAX_TRADE_SIZE = 100

RISK_FREE_RATE = 0.0

# =============================================================================
# DELTA HEDGING
# =============================================================================
# Target absolute delta after hedge as a fraction of delta limit (0.0 to 1.0).
# Examples: 0.0 -> flat delta, 0.5 -> hedge back to 50% of delta limit.
HEDGE_TARGET_DELTA = 0.0

# Re-hedge evaluation interval in ticks.
# 1 means evaluate/hedge every tick.
HEDGE_INTERVAL_TICKS = 0.5

# Maximum RTM shares to trade per hedge action.
HEDGE_MAX_SHARES_PER_HEDGE = 10000

# Minimum shares to bother hedging
MIN_HEDGE_SIZE = 0

# =============================================================================
# EXECUTION
# =============================================================================
# Max option orders per cycle (for building/closing positions)
MAX_ORDERS_PER_CYCLE = 100

# Main loop speed
LOOP_INTERVAL_SEC = 0.10

# Minimum option price to trade (avoid penny options)
MIN_OPTION_PRICE = 0.01

# Iron butterfly wing distance from ATM strike for net-relief overlays.
# If ATM strike is X, wings are at X + width (call) and X - width (put).
IRON_FLY_WING_WIDTH = 3

# Maximum straddles per position (leave headroom: 400*2=800 < 1000 net limit)
MAX_STRADDLES = 875

# Force max position when absolute IV-RV gap exceeds this threshold (decimal).
# Example: 0.03 = 3 vol points.
FORCED_N_VOL_GAP_THRESHOLD = 0.03

