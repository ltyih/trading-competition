# -*- coding: utf-8 -*-
"""Algo Market Making Bot - V12.1 Configuration.

V12.1: AGGRESSIVE CAPACITY UTILIZATION
========================================
V12 RESULTS: +$20,073 (first profitable run!)
Gap to winner: $64k - $20k = $44k

ROOT CAUSE OF GAP (from logged data):
- Intraday gross limit = 50,000 → we used only 12% average (6,048 shares)
- Close-time limit drops to 9,000 → we flatten correctly (0 fines)
- 88% of intraday capacity WASTED
- Winner likely runs at 30-35k intraday, flattens before close

V12.1 STRATEGY:
1. RUN HOT during seconds 5-38: target 25,000-35,000 aggregate position
2. FLATTEN HARD during seconds 38-52: get under 7,000 for close
3. 3 LAYERS of quotes per side per stock for maximum volume
4. WNTR/SMMR: largest sizes (best rebate economics)
5. Flattening cost: ~25k shares × $0.02 = $500/day × 5 days = $2,500
   Revenue from 3-5x volume: $40k-80k total. NET: +$37k-77k

CLOSE-TIME DYNAMICS (from logged data):
- At second 0 (close): gross_limit shown as 9,000
- During trading: gross_limit = 50,000
- Day 1 close: agg=5,075/9,000 (56%) ← room for more
- Day 2 close: agg=7,340/9,000 (82%) ← near limit
- Day 3 close: agg=1,712/9,000 (19%) ← over-flattened, wasted
- Day 4 close: agg=7,086/9,000 (79%) ← good
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
# Market median spreads were: WNTR=4¢, SMMR=5¢, ATMN=7¢, SPNG=11¢
# We quote INSIDE these to ensure fills
BASE_SPREAD = {
    "WNTR": 0.02,   # 2¢ - tight to maximize fills (rebate covers adverse selection)
    "SMMR": 0.03,   # 3¢ - inside market median of 5¢
    "ATMN": 0.04,   # 4¢ - inside market median of 7¢
    "SPNG": 0.05,   # 5¢ - inside market median of 11¢
}

# ========== Order Sizing: 3-4x BIGGER than V12 ==========
MAX_ORDER_SIZE = 10_000
ORDER_SIZE = {
    "WNTR": 5000,   # Was 2000 → 5000 (highest rebate, most volume potential)
    "SMMR": 4500,   # Was 1800 → 4500 (second highest rebate)
    "ATMN": 3000,   # Was 1200 → 3000 (decent rebate)
    "SPNG": 2000,   # Was 800  → 2000 (lowest rebate but $42/1000 shares!)
}
VOL_SIZE_MULT = {"LOW": 1.3, "MEDIUM": 1.0, "HIGH": 0.5}

# ========== Inventory Skew ==========
SKEW_FACTOR = 0.002   # Slightly less skew (was 0.003) - let positions build more

# ========== Position Limits ==========
DEFAULT_GROSS_LIMIT = 50000    # Confirmed from logged data
DEFAULT_NET_LIMIT = 30000
CLOSE_TIME_LIMIT = 9000       # NEW: separate close-time limit from logged data

# Per-stock limits during active trading
PER_STOCK_LIMIT_FRACTION = 0.25   # Each stock can hold up to 25% of gross limit
# = 12,500 per stock at 50k gross (allows big positions during trading)
MIN_PER_STOCK_LIMIT = 3000
MAX_PER_STOCK_LIMIT = 12500

# ========== Utilization Thresholds ==========
# V12.1: Based on INTRADAY 50k limit, not close-time 9k limit
# These control quoting decisions during active trading
UTIL_NORMAL = 0.50       # < 25k: full quoting
UTIL_SKEW = 0.65         # 32.5k: begin skewing
UTIL_REDUCE = 0.75       # 37.5k: reduce sizes
UTIL_EMERGENCY = 0.85    # 42.5k: stop increasing, flatten
UTIL_PANIC = 0.92        # 46k: panic flatten

GROSS_LIMIT_BUFFER = 0.88     # Stay at 88% of intraday gross limit = 44k
NET_LIMIT_BUFFER = 0.85

# ========== Timing ==========
CYCLE_SLEEP = 0.04        # 40ms = 25 cycles/sec (faster than V12's 50ms)
HEAT_DURATION = 300
DAY_LENGTH = 60

# ========== Market Close Protocol (V12.1: EARLIER START) ==========
# The close-time limit is 9,000. We need to go from ~30k to <7k.
# That's ~23k shares to flatten in ~14 seconds. Very doable with market orders.
PRE_CLOSE_WIDEN_SEC = 44       # Second 44: widen spreads (was 36 - too early!)
PRE_CLOSE_REDUCE_SEC = 46     # Second 46: begin passive flattening
PRE_CLOSE_CANCEL_SEC = 50     # Second 50: cancel ALL orders
PRE_CLOSE_FLATTEN_SEC = 51    # Second 51: market-order flatten
# Seconds 43-59: aggressive market-order flattening to get under close limit
CLOSE_TARGET_UTILIZATION = 0.70  # Target 70% of close-time limit = 6,300 shares

POST_CLOSE_RECOVERY_SEC = 1      # Was 5 → trade from second 1 (saves 4 sec/day × 5 days)
POST_CLOSE_SPREAD_MULT = 1.5     # Slightly wider just for second 0
POST_CLOSE_SIZE_MULT = 0.5       # Half size just for second 0 (was 0.3)
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
REQUOTE_THRESHOLD = 0.35   # Requote more aggressively to stay competitive

# ========== Order Book ==========
IMBALANCE_THRESHOLD = 0.60
IMBALANCE_SKEW_FACTOR = 0.002
PENNY_IMPROVE = False      # Still disabled

# ========== Circuit Breaker ==========
CIRCUIT_BREAKER_CAUTION = -20000    # Was -8000 (too aggressive, caused flat periods)
CIRCUIT_BREAKER_HALT = -50000       # Was -25000 (had no recovery, permanent halt!)

# ========== Adaptive Spread ==========
ADAPTIVE_INTERVAL = 15
ADAPTIVE_MIN_MULT = 0.75   # Allow slightly tighter spreads (was 0.80)
ADAPTIVE_MAX_MULT = 1.60
ADAPTIVE_TARGET_FILLS = 15  # Higher target (more aggressive)

# ========== Logging ==========
LOG_INTERVAL_TICKS = 3

# ========== Layered Quoting (3 layers now!) ==========
ENABLE_LAYERED_QUOTES = True
NUM_LAYERS = 3
LAYER2_SPREAD_MULT = 1.8    # Layer 2: 1.8x base spread
LAYER2_SIZE_MULT = 0.6      # Layer 2: 60% of base size
LAYER3_SPREAD_MULT = 3.0    # Layer 3: 3x base spread (catches big moves)
LAYER3_SIZE_MULT = 0.4      # Layer 3: 40% of base size

# ========== Asymmetric Sizing ==========
ASYM_REDUCE_MAX = 2.0       # Boost reducing side up to 2x (was 1.8)
ASYM_INCREASE_MIN = 0.25    # Reduce increasing side to 0.25x (was 0.3)
ASYM_KICK_IN = 0.12         # Start earlier (was 0.15)