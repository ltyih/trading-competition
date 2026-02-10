# -*- coding: utf-8 -*-
"""LT3 Tender Liquidity Bot — Configuration.

Centralised constants and parameters. All other modules import from here.
"""

# ========== API Connection ==========
API_KEY = {'X-API-Key': 'ZXCVB'}
BASE_URL = 'http://localhost:10005/v1'

# ========== Watchlist ==========
WATCHLIST = {'RITC', 'COMP'}

# ========== Trading Parameters ==========
COMMISSION_PER_SHARE = 0.02
MAX_ORDER_SIZE = {'RITC': 25000, 'COMP': 10000}
POSITION_LIMITS = {'net': 100000, 'gross': 250000}

# ========== Risk / Execution Knobs ==========
BOOK_LIMIT = 70
MIN_PROFIT_PER_SHARE = 0.00
MAX_DEPTH_RATIO = 0.25
MIN_NET_PROFIT_PER_SHARE = 0.01
MIN_TENDER_CONFIDENCE = 0.3

# ========== Timing ==========
SLEEP_SEC = 0.2
MAX_TICK = 600

# ========== TWAP Execution ==========
TWAP_BATCH_SIZE = 3000          # hard cap per slice before algorithmic caps
TWAP_TICK_INTERVAL = 1          # normal cadence (endgame ignores this)
TWAP_AGGRESSIVE_OFFSET = 0.01   # how far past best price to place limit
UNWIND_BASE_PARTICIPATION = 0.08   # early-stage participation vs visible depth
UNWIND_MAX_PARTICIPATION = 0.30    # late-stage participation vs visible depth
UNWIND_MIN_ORDER_SIZE = 1000      # avoid tiny slices on large positions
UNWIND_VIRTUAL_TICKS_FLOOR = 80    # non-endgame pacing horizon floor
UNWIND_VOL_LOOKBACK = 20           # mid-price points for realized volatility
UNWIND_VOL_LOW = 0.0008            # low-vol threshold (stdev of returns)
UNWIND_VOL_HIGH = 0.0020           # high-vol threshold (stdev of returns)
UNWIND_RISK_MULT_LOW = 0.8
UNWIND_RISK_MULT_MED = 1.0
UNWIND_RISK_MULT_HIGH = 1.3
SOFT_BREAKEVEN_SLIPPAGE = 0.02     # max allowed breach (price units)
SOFT_BREAKEVEN_URGENCY = 1.2       # min urgency to allow small breach
SOFT_BREAKEVEN_BATCH_FRACTION = 0.35
MARKETABLE_LIMIT_EPS = 0.01        # pseudo-marketable limit offset
UNWIND_TICKER_PROFILE = {
    # Sub-Heat 1 style:
    # COMP: high liquidity / medium vol -> unwind faster, tolerate more price drift.
    'COMP': {
        'risk_mult': 1.2,
        'base_participation': 0.12,
        'max_participation': 0.35,
        'normal_limit_min_clip': 1200,
        'soft_be_urgency': 0.9,
        'soft_be_slippage': 0.03,
        'soft_be_batch_fraction': 0.50,
        'marketable_limit_eps': 0.02,
    },
    # RITC: medium liquidity / low vol -> unwind smoother, stricter on price.
    'RITC': {
        'risk_mult': 0.8,
        'base_participation': 0.06,
        'max_participation': 0.22,
        'normal_limit_min_clip': 600,
        'soft_be_urgency': 1.6,
        'soft_be_slippage': 0.01,
        'soft_be_batch_fraction': 0.25,
        'marketable_limit_eps': 0.005,
    },
}

# ========== Auction Pricing ==========
AUCTION_PROFIT_MARGIN = 0.02    # target profit per share on auction bids
AUCTION_SAFETY_MARGIN = 0.01    # buffer beyond break-even

# ========== End-of-Case Safety ==========
ENDGAME_UNWIND_TICKS = 150      # when remaining ticks <= this, pace out remaining position
FINAL_FLATTEN_TICKS = 20        # final sprint window before case end
ENDGAME_MAX_SLICES_PER_TICK = 3  # max market-order slices per tick in endgame

# ========== Misc ==========
EPS = 1e-9
DEBUG_BOOK = False
