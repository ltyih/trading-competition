"""Configuration for Volatility Trading Algorithm - V8 Maximum Aggression."""

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
# V8 ALGORITHM PARAMETERS - MAXIMUM AGGRESSION
# =============================================================================
# Minimum vol edge to initiate a position
VOL_EDGE_THRESHOLD = 0.005       # 0.5% - enter early

# Target option net position - PUSH CLOSE TO 1000 LIMIT
TARGET_NET_POSITION = 950        # 950 out of 1000 net limit

# Number of strikes to concentrate on (ATM-focused)
NUM_STRIKES = 5                  # 5 strikes = more capacity, still ATM-focused

# Edge for full position scaling (reach full size faster)
FULL_EDGE_THRESHOLD = 0.03      # 3% edge = full position (was 4%)

# Maximum option orders per cycle (build as fast as possible)
MAX_OPTION_ORDERS_PER_CYCLE = 30

# Minimum option price to trade
MIN_OPTION_PRICE = 0.01

# Risk-free rate
RISK_FREE_RATE = 0.0

# Main loop interval
LOOP_INTERVAL_SEC = 0.20        # Faster for more responsive hedging

# End-of-period position unwinding
UNWIND_START_TICK = 272          # Slightly later - maximize edge capture time

# =============================================================================
# V8 DELTA HEDGING - AGGRESSIVE BUT CONTROLLED
# =============================================================================
# With bigger position (950 contracts), gamma is ~50% higher.
# Need tighter hedge bands to avoid breaching delta limit.

# Hedge when delta exceeds this % of delta_limit
HEDGE_TRIGGER_PCT = 0.70         # 70% trigger (was 85%) - tighter for bigger position

# Hedge back to this % of limit
HEDGE_TARGET_PCT = 0.15          # 15% target (was 40%) - hedge closer to neutral

# Minimum hedge size
MIN_HEDGE_SIZE = 800             # Smaller min (was 1500) - more responsive

# Cooldown: minimum ticks between hedge trades
HEDGE_COOLDOWN_TICKS = 3         # 3 ticks (was 5) - faster hedging for bigger gamma

# =============================================================================
# V8 POSITION MANAGEMENT
# =============================================================================
# Build MAXIMUM position, hold, delta hedge only.

# No reversals after this tick
NO_REVERSAL_AFTER_TICK = 250     # Block reversals earlier (was 260) - protect gains

# Minimum edge to justify a reversal (higher = fewer false reversals)
MIN_REVERSAL_EDGE = 0.05         # 5% (was 4%) - only reverse on strong signal

# Cooldown after reversal
REVERSAL_COOLDOWN_TICKS = 35     # 35 ticks (was 30) - more protection
