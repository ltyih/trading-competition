# -*- coding: utf-8 -*-
"""Algo Market Making Bot - V14.1 Configuration.

V14.1: RESTORED V12.1 PARAMETERS + V14 CODE IMPROVEMENTS
==========================================================
V14 caused -$1M P&L due to:
  - BASE_SPREAD all=0.01 (instant adverse selection)
  - PRE_CLOSE_WIDEN_SEC=30 (halved active trading time!)
  - SKEW_FACTOR=0.02 (10x too high)
  - SPREAD_MARKET_FACTOR=0.50 (too tight)

V12.1 made $35k profit with safe parameters.
V14.1 = V12.1 parameters + V14 code fixes (pending tracking, trend fix, simpler spread).
"""

# ========== API Connection ==========
API_HOST = "localhost"
API_PORT = 10000
API_BASE_URL = f"http://{API_HOST}:{API_PORT}/v1"
API_KEY = "AJDSYHVC"

# ========== Tickers (priority: highest rebate first) ==========
TICKERS = ["WNTR", "SMMR", "ATMN", "SPNG"]

# ========== Rebates & Fees ==========
MARKET_ORDER_FEE = 0.02
REBATES = {
    "SPNG": 0.03,
    "SMMR": 0.04,
    "ATMN": 0.035,
    "WNTR": 0.045,
}

# ========== Penalties ==========
AGGREGATE_PENALTY_PER_SHARE = 10.0
GROSS_NET_PENALTY_PER_SHARE = 5.0

# ========== Spread: Tuned from V12 data ==========
# Market median spreads: WNTR=4c, SMMR=5c, ATMN=7c, SPNG=11c
# We quote INSIDE these to ensure fills
BASE_SPREAD = {
    "WNTR": 0.02,   # 2c - tight to maximize fills (rebate covers adverse selection)
    "SMMR": 0.03,   # 3c - inside market median of 5c
    "ATMN": 0.04,   # 4c - inside market median of 7c
    "SPNG": 0.05,   # 5c - inside market median of 11c
}
SPREAD_MARKET_FACTOR = 0.80   # V12.1 used market spread as reference
SPREAD_MIN_ABSOLUTE = 0.01
SPREAD_MAX_ABSOLUTE = 0.30    # V12.1 didn't have this but keep as safety

# ========== Order Sizing: 3-4x BIGGER than V12 ==========
MAX_ORDER_SIZE = 10_000
ORDER_SIZE = {
    "WNTR": 5000,   # Highest rebate, most volume potential
    "SMMR": 4500,   # Second highest rebate
    "ATMN": 3000,   # Decent rebate
    "SPNG": 2000,   # Lowest rebate
}
VOL_SIZE_MULT = {"LOW": 1.3, "MEDIUM": 1.0, "HIGH": 0.5}

# ========== Inventory Skew ==========
SKEW_FACTOR = 0.002   # V12.1 value (V14's 0.02 was 10x too high!)

# ========== TREND DETECTION (V14 addition - used by main.py) ==========
TREND_FAST_ALPHA = 0.35
TREND_SLOW_ALPHA = 0.08
TREND_SKEW_FACTOR = 0.012
TREND_MIN_SIGNAL = 0.05

# ========== DAY-OPEN MOMENTUM (V14 addition - used by main.py) ==========
MOMENTUM_THRESHOLD = 0.002
MOMENTUM_SKEW_FACTOR = 0.03
MOMENTUM_DECAY = 0.92
MOMENTUM_DURATION_SEC = 20
MOMENTUM_SIZE_BOOST = 1.5

# ========== Position Limits ==========
DEFAULT_GROSS_LIMIT = 50000
DEFAULT_NET_LIMIT = 30000
CLOSE_TIME_LIMIT = 9000

PER_STOCK_LIMIT_FRACTION = 0.25   # V12.1: 25% of gross limit = 12,500/stock
MIN_PER_STOCK_LIMIT = 3000
MAX_PER_STOCK_LIMIT = 12500       # V12.1 value (V14's 10000 was too restrictive)

# ========== Utilization Thresholds ==========
UTIL_NORMAL = 0.50       # < 25k: full quoting
UTIL_SKEW = 0.65         # 32.5k: begin skewing
UTIL_REDUCE = 0.75       # 37.5k: reduce sizes
UTIL_EMERGENCY = 0.85    # 42.5k: stop increasing, flatten
UTIL_PANIC = 0.92        # 46k: panic flatten

GROSS_LIMIT_BUFFER = 0.88
NET_LIMIT_BUFFER = 0.85

# ========== Timing ==========
CYCLE_SLEEP = 0.04        # V12.1: 40ms (V14's 25ms may hit rate limits)
HEAT_DURATION = 300
DAY_LENGTH = 60

# ========== Market Close Protocol ==========
PRE_CLOSE_WIDEN_SEC = 44       # V12.1 value! (V14's 30 halved trading time!)
PRE_CLOSE_REDUCE_SEC = 46     # Second 46: begin passive flattening
PRE_CLOSE_CANCEL_SEC = 50     # Second 50: cancel ALL orders
PRE_CLOSE_FLATTEN_SEC = 51    # Second 51: market-order flatten
CLOSE_TARGET_UTILIZATION = 0.70

POST_CLOSE_RECOVERY_SEC = 1
POST_CLOSE_SPREAD_MULT = 1.5
POST_CLOSE_SIZE_MULT = 0.5
PRE_CLOSE_SPREAD_MULT = 1.5
PRE_CLOSE_SIZE_MULT = 0.3

# ========== Volatility ==========
VOL_LOOKBACK = 10
VOL_LOW_THRESHOLD = 0.008
VOL_HIGH_THRESHOLD = 0.04
VOL_SPREAD_MULT = {"LOW": 0.85, "MEDIUM": 1.0, "HIGH": 1.8}

# ========== Dynamic Volatility Spread Widening ==========
RECENT_MOVE_LOOKBACK = 3
RECENT_MOVE_WIDEN_THRESHOLD = 0.04
RECENT_MOVE_WIDEN_MULT = 2.0

# ========== Price Sanity ==========
MIN_PRICE = 10.01
MAX_PRICE = 39.99

# ========== Requoting ==========
REQUOTE_THRESHOLD = 0.35   # V12.1 value (V14's 0.15 was too aggressive)

# ========== Order Book ==========
IMBALANCE_THRESHOLD = 0.60
IMBALANCE_SKEW_FACTOR = 0.002   # V12.1 value
PENNY_IMPROVE = False

# ========== Circuit Breaker ==========
CIRCUIT_BREAKER_CAUTION = -20000
CIRCUIT_BREAKER_HALT = -50000

# ========== Adaptive Spread ==========
ADAPTIVE_INTERVAL = 15      # V12.1 value
ADAPTIVE_MIN_MULT = 0.75    # V12.1 value
ADAPTIVE_MAX_MULT = 1.60
ADAPTIVE_TARGET_FILLS = 15   # V12.1 value

# ========== Logging ==========
LOG_INTERVAL_TICKS = 3

# ========== Layered Quoting ==========
ENABLE_LAYERED_QUOTES = True
NUM_LAYERS = 3
LAYER2_SPREAD_MULT = 1.8
LAYER2_SIZE_MULT = 0.6      # V12.1: 60% (V14 had 50%)
LAYER3_SPREAD_MULT = 3.0
LAYER3_SIZE_MULT = 0.4      # V12.1: 40% (V14 had 30%)

# ========== Asymmetric Sizing ==========
ASYM_REDUCE_MAX = 2.0       # V12.1 value
ASYM_INCREASE_MIN = 0.25    # V12.1 value
ASYM_KICK_IN = 0.12         # V12.1 value
