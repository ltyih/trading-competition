"""Configuration for Volatility Trading Algorithm - V4 Maximum Profit."""

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

# Week boundaries (tick ranges within a sub-heat)
WEEK_BOUNDARIES = {
    1: (1, 75),
    2: (76, 150),
    3: (151, 225),
    4: (226, 300),
}

# Mid-week forecast ticks
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
# V4 ALGORITHM PARAMETERS - MAXIMUM PROFIT
# =============================================================================
# Minimum vol edge to trigger trading (1% = very sensitive)
VOL_EDGE_THRESHOLD = 0.01

# Target net position (max = 1000, leave 50 buffer)
TARGET_NET_POSITION = 950

# Maximum edge for full position scaling (linear scale up to this)
FULL_EDGE_THRESHOLD = 0.08  # 8% edge = full position

# Maximum option orders per cycle (10 = aggressive building)
MAX_OPTION_ORDERS_PER_CYCLE = 10

# Minimum option price to trade (below this, commission > profit)
MIN_OPTION_PRICE = 0.05

# Risk-free rate
RISK_FREE_RATE = 0.0

# Main loop interval
LOOP_INTERVAL_SEC = 0.25  # Faster cycling for more responsive trading

# End-of-period position unwinding
UNWIND_START_TICK = 285  # Late unwind - maximize trading time
