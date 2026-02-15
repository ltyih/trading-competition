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
RIT_SERVER_PORT = 16520
USERNAME = "UBCT-4"
PASSWORD = "target"

# Local RIT Client REST API (our algo connects here)
API_HOST = "localhost"
API_PORT = 10000         # Default RIT Client API port - check your RIT Client settings
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
POSITION_TICKS = [1, 75, 150, 225]

# Start unwinding all positions (must be flat by 300)
UNWIND_START_TICK = 270

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
# Hedge when |delta| exceeds this fraction of delta_limit.
# Paper says "only hedge when reaching delta limit."
# We trigger at 88% to avoid actually breaching (penalty).
HEDGE_TRIGGER_PCT = 0.88

# Hedge back to zero delta (as paper recommends)
HEDGE_TARGET_DELTA = 0

# Minimum shares to bother hedging
MIN_HEDGE_SIZE = 200

# Cooldown between hedge trades (ticks) to prevent oscillation
HEDGE_COOLDOWN_TICKS = 5

# =============================================================================
# EXECUTION
# =============================================================================
# Max option orders per cycle (for building/closing positions)
MAX_ORDERS_PER_CYCLE = 20

# Main loop speed
LOOP_INTERVAL_SEC = 0.10

# Minimum option price to trade (avoid penny options)
MIN_OPTION_PRICE = 0.01

# Maximum straddles per position (leave headroom: 400*2=800 < 1000 net limit)
MAX_STRADDLES = 400
