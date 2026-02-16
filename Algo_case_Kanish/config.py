# -*- coding: utf-8 -*-
"""Algo Market Making Bot - V12 Configuration.

V12: AGGREGATE-LIMIT-AWARE REBATE HARVESTER
=============================================
ROOT CAUSES OF V11 LOSSES (identified from 6 performance reports):
1. Aggregate position limit penalty ($10/share) at EVERY market close not managed
2. DEFAULT_GROSS_LIMIT=10k when actual=50k -> limit_scale=5x -> oversized orders
3. Penny improvement causes adverse selection on SPNG/ATMN
4. Pre-close flattening starts too late (second 55 vs 45-50)
5. Adaptive spreads tighten to razor-thin -> adverse selection
6. SPNG/ATMN spreads too tight for their low rebates

V12 KEY CHANGES:
- Proper aggregate limit tracking with $10/share penalty awareness
- Minute-by-minute lifecycle: open -> trade -> pre-close -> flatten -> lockdown
- Much wider spreads on SPNG/ATMN, tighter on WNTR/SMMR (rebate-proportional)
- Remove penny improvement (causes adverse selection)
- Conservative but consistent: target $5-10k/heat x 12 heats
- Flatten to ZERO before every market close if aggregate > 80%
"""

# ========== API Connection ==========
API_HOST = "localhost"
API_PORT = 10000
API_BASE_URL = f"http://{API_HOST}:{API_PORT}/v1"
API_KEY = "AJDSYHVC"

# ========== Tickers (priority: highest rebate first) ==========
TICKERS = ["WNTR", "SMMR", "ATMN", "SPNG"]

# ========== Rebates & Fees (USER CONFIRMED THESE ARE CORRECT) ==========
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

# ========== Spread: REBATE-PROPORTIONAL ==========
BASE_SPREAD = {
    "WNTR": 0.02,
    "SMMR": 0.03,
    "ATMN": 0.04,
    "SPNG": 0.06,
}

# ========== Order Sizing ==========
MAX_ORDER_SIZE = 10_000
ORDER_SIZE = {
    "WNTR": 2000,
    "SMMR": 1800,
    "ATMN": 1200,
    "SPNG": 800,
}
VOL_SIZE_MULT = {"LOW": 1.3, "MEDIUM": 1.0, "HIGH": 0.5}

# ========== Inventory Skew ==========
SKEW_FACTOR = 0.003

# ========== Position Limits ==========
DEFAULT_GROSS_LIMIT = 50000
DEFAULT_NET_LIMIT = 30000
DEFAULT_AGGREGATE_LIMIT = 50000

PER_STOCK_LIMIT_FRACTION = 0.25
MIN_PER_STOCK_LIMIT = 1500

# ========== Aggregate Utilization Thresholds ==========
UTIL_NORMAL = 0.50
UTIL_SKEW = 0.65
UTIL_REDUCE = 0.75
UTIL_EMERGENCY = 0.85
UTIL_PANIC = 0.92

GROSS_LIMIT_BUFFER = 0.90
NET_LIMIT_BUFFER = 0.85

# ========== Timing ==========
CYCLE_SLEEP = 0.05
HEAT_DURATION = 300
DAY_LENGTH = 60

# ========== Market Close Protocol ==========
PRE_CLOSE_WIDEN_SEC = 45
PRE_CLOSE_REDUCE_SEC = 48
PRE_CLOSE_CANCEL_SEC = 52
PRE_CLOSE_FLATTEN_SEC = 53
POST_CLOSE_RECOVERY_SEC = 5
POST_CLOSE_SPREAD_MULT = 2.0
POST_CLOSE_SIZE_MULT = 0.4
PRE_CLOSE_SPREAD_MULT = 1.5
PRE_CLOSE_SIZE_MULT = 0.3

# ========== Volatility ==========
VOL_LOOKBACK = 10
VOL_LOW_THRESHOLD = 0.008
VOL_HIGH_THRESHOLD = 0.04
VOL_SPREAD_MULT = {"LOW": 0.85, "MEDIUM": 1.0, "HIGH": 1.8}

# ========== Price Sanity ==========
MIN_PRICE = 10.01
MAX_PRICE = 39.99

# ========== Requoting ==========
REQUOTE_THRESHOLD = 0.40

# ========== Order Book ==========
IMBALANCE_THRESHOLD = 0.60
IMBALANCE_SKEW_FACTOR = 0.002
PENNY_IMPROVE = False

# ========== Circuit Breaker ==========
CIRCUIT_BREAKER_CAUTION = -5000
CIRCUIT_BREAKER_HALT = -15000

# ========== Adaptive Spread ==========
ADAPTIVE_INTERVAL = 15
ADAPTIVE_MIN_MULT = 0.80
ADAPTIVE_MAX_MULT = 1.60
ADAPTIVE_TARGET_FILLS = 10

# ========== Logging ==========
LOG_INTERVAL_TICKS = 3

# ========== Layered Quoting ==========
ENABLE_LAYERED_QUOTES = True
LAYER2_SPREAD_MULT = 2.0
LAYER2_SIZE_MULT = 0.4

# ========== Asymmetric Sizing ==========
ASYM_REDUCE_MAX = 1.8
ASYM_INCREASE_MIN = 0.3
ASYM_KICK_IN = 0.15