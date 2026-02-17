# -*- coding: utf-8 -*-
"""Algo Market Making Bot - V15 Avellaneda-Stoikov Configuration.

V15: AVELLANEDA-STOIKOV INSPIRED MARKET MAKING
================================================
Key changes from V14.1:
  - Correct rebate values from case document
  - AS-optimal spread and skew parameters
  - Much more aggressive volume (wider per-stock limits during trading)
  - Tighter close-time window (flatten in last 8 seconds, not 16)
  - Faster cycle time with smarter requoting
  - Calibrated gamma (risk aversion) and k (intensity) parameters
"""

# ========== API Connection ==========
API_HOST = "localhost"
API_PORT = 10000
API_BASE_URL = f"http://{API_HOST}:{API_PORT}/v1"
API_KEY = "AJDSYHVC"

# ========== Tickers (priority: highest rebate first) ==========
TICKERS = ["WNTR", "SMMR", "ATMN", "SPNG"]

# ========== Rebates & Fees (CORRECTED from case document) ==========
MARKET_ORDER_FEE = 0.02
REBATES = {
    "SPNG": 0.01,    # Case doc: $0.01
    "SMMR": 0.02,    # Case doc: $0.02
    "ATMN": 0.015,   # Case doc: $0.015
    "WNTR": 0.025,   # Case doc: $0.025
}

# ========== Penalties (from case document) ==========
AGGREGATE_PENALTY_PER_SHARE = 10.0   # $10/share at each market close
GROSS_NET_PENALTY_PER_SHARE = 5.0    # $5/share for exceeding intraday limits

# ========== Avellaneda-Stoikov Parameters ==========
# gamma (gamma) - risk aversion parameter
# Higher gamma = wider spreads, stronger inventory skew, less risk
# Lower gamma = tighter spreads, more volume, more risk
# We use different gamma for different phases of the trading day
AS_GAMMA_NORMAL = 0.05       # During active trading: moderate risk aversion
AS_GAMMA_HIGH_INV = 0.15     # When inventory is high: increase risk aversion
AS_GAMMA_PRE_CLOSE = 0.80    # Near close: very high risk aversion (force flatten)

# k - order arrival intensity parameter (from Lambda(delta) = A*exp(-k*delta))
# Higher k = orders more sensitive to price, tighter optimal spread
# Calibrate from observed fill rates
AS_K_PARAMETER = 1.5         # Estimated from typical fill rates

# sigma^2 estimation
AS_VOL_WINDOW = 15           # Ticks to estimate volatility
AS_VOL_DEFAULT = 0.03        # Default sigma per tick if not enough data

# ========== Spread Parameters ==========
# These serve as FLOORS - AS formula gives optimal spread, but we never go below these
SPREAD_MIN_PER_TICKER = {
    "WNTR": 0.01,   # Minimum 1c (high rebate covers tight spread risk)
    "SMMR": 0.01,   # Minimum 1c
    "ATMN": 0.01,   # Minimum 1c
    "SPNG": 0.02,   # Minimum 2c (lowest rebate, need spread protection)
}
SPREAD_MIN_ABSOLUTE = 0.01
SPREAD_MAX_ABSOLUTE = 0.25

# Market spread tracking
SPREAD_INSIDE_FACTOR = 0.85  # Quote inside market spread by this factor

# ========== Order Sizing: AGGRESSIVE for volume ==========
MAX_ORDER_SIZE = 10_000
BASE_ORDER_SIZE = {
    "WNTR": 6000,   # Highest rebate → biggest size
    "SMMR": 5000,
    "ATMN": 4000,
    "SPNG": 3000,   # Lowest rebate → smaller size
}

# ========== Position Limits ==========
DEFAULT_GROSS_LIMIT = 50000
DEFAULT_NET_LIMIT = 30000
CLOSE_TIME_LIMIT = 9000       # Aggregate limit at close (announced per heat)

# Per-stock: allow BIGGER positions during trading, flatten aggressively at close
PER_STOCK_TRADING_LIMIT = 15000   # During active trading: allow up to 15k/stock
PER_STOCK_CLOSE_LIMIT = 2000      # At close: need aggregate under close_limit

# ========== Utilization Thresholds (of gross_limit) ==========
# More aggressive: let positions build during trading, flatten before close
UTIL_NORMAL = 0.60       # < 30k: full quoting
UTIL_SKEW = 0.75         # 37.5k: begin skewing
UTIL_REDUCE = 0.85       # 42.5k: reduce sizes
UTIL_EMERGENCY = 0.92    # 46k: stop increasing
UTIL_PANIC = 0.96        # 48k: panic flatten

GROSS_LIMIT_BUFFER = 0.93
NET_LIMIT_BUFFER = 0.88

# ========== Timing ==========
CYCLE_SLEEP = 0.025       # 25ms - faster cycling = more requotes = more fills
HEAT_DURATION = 300
DAY_LENGTH = 60

# ========== Market Close Protocol (TIGHT - maximize trading time) ==========
PRE_CLOSE_WIDEN_SEC = 52       # Second 52: widen spreads slightly
PRE_CLOSE_CANCEL_SEC = 55      # Second 55: cancel all, start flattening
PRE_CLOSE_FLATTEN_SEC = 55     # Second 55: market-order flatten
CLOSE_TARGET_UTILIZATION = 0.60  # Target 60% of close_limit at close

# Post-close recovery (new day)
POST_CLOSE_RECOVERY_SEC = 2    # 2 seconds to let price settle after open
POST_CLOSE_SPREAD_MULT = 2.0   # Wide spreads during recovery
POST_CLOSE_SIZE_MULT = 0.3     # Small sizes during recovery

# ========== Volatility Estimation ==========
VOL_LOOKBACK = 12
VOL_LOW_THRESHOLD = 0.005
VOL_HIGH_THRESHOLD = 0.03
VOL_SPREAD_MULT = {"LOW": 0.80, "MEDIUM": 1.0, "HIGH": 1.6}
VOL_SIZE_MULT = {"LOW": 1.3, "MEDIUM": 1.0, "HIGH": 0.5}

# ========== Jump/News Detection ==========
# When price jumps > threshold between ticks, widen spread temporarily
JUMP_THRESHOLD = 0.15          # 15c jump = probable news
JUMP_WIDEN_MULT = 3.0          # 3x spread during jump
JUMP_DECAY_RATE = 0.85         # Decay widening each tick
JUMP_MIN_SIGNAL = 0.05         # Below this, ignore

# ========== Price Sanity ==========
MIN_PRICE = 10.01
MAX_PRICE = 39.99

# ========== Requoting ==========
# Only requote if price moved significantly (avoid unnecessary cancel/replace)
REQUOTE_THRESHOLD = 0.4        # 40% of half-spread = ~1-2c

# ========== Order Book Analysis ==========
IMBALANCE_THRESHOLD = 0.55
IMBALANCE_SKEW_FACTOR = 0.003

# ========== Circuit Breaker ==========
CIRCUIT_BREAKER_CAUTION = -25000
CIRCUIT_BREAKER_HALT = -60000

# ========== Layered Quoting ==========
ENABLE_LAYERED_QUOTES = True
NUM_LAYERS = 3
LAYER_CONFIG = {
    1: {"spread_mult": 1.0, "size_mult": 1.0},
    2: {"spread_mult": 1.8, "size_mult": 0.5},
    3: {"spread_mult": 3.0, "size_mult": 0.3},
}

# ========== Asymmetric Sizing (inventory-aware) ==========
ASYM_REDUCE_BOOST = 2.0       # Boost reducing-side orders by 2x
ASYM_INCREASE_FLOOR = 0.2     # Floor for increasing-side orders
ASYM_KICK_IN_FRAC = 0.15      # Start asymmetry at 15% of per-stock limit

# ========== Adaptive Spread Tuning ==========
ADAPTIVE_INTERVAL = 12         # Ticks between adaptations
ADAPTIVE_MIN_MULT = 0.70       # Can tighten to 70% of base
ADAPTIVE_MAX_MULT = 1.50       # Can widen to 150% of base
ADAPTIVE_TARGET_FILLS = 20     # Target fills per adaptation window

# ========== Logging ==========
LOG_INTERVAL_TICKS = 5