"""Configuration for Volatility Trading Algorithm - V7 Profit Maximizer."""

# =============================================================================
# RIT API CONFIGURATION
# =============================================================================
API_HOST = "localhost"
API_PORT = 9998
API_BASE_URL = f"http://{API_HOST}:{API_PORT}/v1"
API_KEY = "AJDSYHVCES"

# =============================================================================
# SECURITIES
# =============================================================================
UNDERLYING_TICKER = "RTM"
STRIKE_PRICES = [45, 46, 47, 48, 49, 50, 51, 52, 53, 54]
CALL_TICKERS = [f"RTM1C{k}" for k in STRIKE_PRICES]
PUT_TICKERS = [f"RTM1P{k}" for k in STRIKE_PRICES]
ALL_OPTION_TICKERS = CALL_TICKERS + PUT_TICKERS

# =============================================================================
# CASE TIMING
# =============================================================================
TICKS_PER_SUBHEAT = 300
WEEKS_PER_SUBHEAT = 4
TICKS_PER_WEEK = 75
TRADING_DAYS_PER_YEAR = 240
TRADING_DAYS_PER_MONTH = 20

WEEK_BOUNDARIES = {
    1: (1, 75),
    2: (76, 150),
    3: (151, 225),
    4: (226, 300),
}

MID_WEEK_TICKS = [37, 112, 187, 262]

# =============================================================================
# TRADING LIMITS (from case brief)
# =============================================================================
RTM_GROSS_LIMIT = 50000
RTM_NET_LIMIT = 50000
RTM_MAX_TRADE_SIZE = 10000

OPTIONS_GROSS_LIMIT = 2500
OPTIONS_NET_LIMIT = 1000
OPTIONS_MAX_TRADE_SIZE = 100
OPTIONS_MULTIPLIER = 100

RTM_FEE_PER_SHARE = 0.01
OPTIONS_FEE_PER_CONTRACT = 1.00

# =============================================================================
# V7 ALGORITHM PARAMETERS
# =============================================================================
# Minimum vol edge to initiate a position
VOL_EDGE_THRESHOLD = 0.005       # 0.5% - enter early, edge compounds over time

# Target option net position - USE THE LIMIT (net limit = 1000)
# We build straddles: buy N calls + N puts at each strike
# Gross = 2*N (each call + put counted), Net = 0 (balanced long)
# So we can go up to gross=2500 → 1250 straddles across strikes
# But net limit = 1000, so if direction=LONG, net = +1000
# For short vol, net = -1000
TARGET_NET_POSITION = 900        # Stay just under 1000 net limit

# Number of strikes to concentrate on (ATM ± this many)
NUM_STRIKES = 4                  # 4 strikes centered on ATM = highest vega

# Edge for full position scaling
FULL_EDGE_THRESHOLD = 0.04      # 4% edge = full position

# Maximum option orders per cycle (fast build)
MAX_OPTION_ORDERS_PER_CYCLE = 20

# Minimum option price to trade
MIN_OPTION_PRICE = 0.02

# Risk-free rate
RISK_FREE_RATE = 0.0

# Main loop interval - fast enough but not too fast
LOOP_INTERVAL_SEC = 0.25

# End-of-period position unwinding
UNWIND_START_TICK = 270          # Start earlier for cleaner unwind

# =============================================================================
# V7 DELTA HEDGING - SINGLE PATH, NO OSCILLATION
# =============================================================================
# CRITICAL FIX: Only ONE hedging mechanism, MARKET orders only,
# CANCEL all RTM orders before every new hedge

# Only hedge when delta exceeds this % of delta_limit
HEDGE_TRIGGER_PCT = 0.60         # 60% of limit before hedging

# Hedge back to this % of limit (same sign as current delta)
HEDGE_TARGET_PCT = 0.15          # 15% - leave room for natural movement

# Minimum hedge size to avoid small costly trades
MIN_HEDGE_SIZE = 800

# Cooldown: minimum ticks between hedge trades
HEDGE_COOLDOWN_TICKS = 2         # Wait 2 ticks between hedges

# =============================================================================
# V7 POSITION MANAGEMENT
# =============================================================================
# Once position is built, NEVER touch options until unwind
# Only delta hedge with RTM

# No reversals after this tick
NO_REVERSAL_AFTER_TICK = 150     # Earlier cutoff - reversals destroy profit

# Minimum edge to justify a reversal
MIN_REVERSAL_EDGE = 0.06         # 6% - very high bar for reversal

# Cooldown after reversal
REVERSAL_COOLDOWN_TICKS = 30
