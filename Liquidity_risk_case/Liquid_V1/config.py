# -*- coding: utf-8 -*-
"""LT3 Tender Liquidity Bot — Configuration.

Centralised constants and parameters. All other modules import from here.
"""

# ========== API Connection ==========
API_KEY = {'X-API-Key': 'ZXCVB'}
BASE_URL = 'http://localhost:10005/v1'

# ========== Watchlist ==========
WATCHLIST = {'*REMOVED*', 'COMP'}

# ========== Trading Parameters ==========
COMMISSION_PER_SHARE = 0.02
MAX_ORDER_SIZE = {'*REMOVED*': 25000, 'COMP': 10000}
POSITION_LIMITS = {'net': 100000, 'gross': 250000}

# ========== Risk / Execution Knobs ==========
BOOK_LIMIT = 70
MIN_PROFIT_PER_SHARE = 0.00
MAX_DEPTH_RATIO = 0.2
MIN_NET_PROFIT_PER_SHARE = 0.01
MIN_TENDER_CONFIDENCE = 0.3

# ========== Timing ==========
SLEEP_SEC = 0.1
MAX_TICK = 600

# ========== TWAP Execution ==========
TWAP_BATCH_SIZE = 3000          # hard cap per slice before algorithmic caps
TWAP_TICK_INTERVAL = 2          # normal cadence (endgame ignores this)
TWAP_AGGRESSIVE_OFFSET = 0.01   # how far past best price to place limit
UNWIND_BASE_PARTICIPATION = 0.08   # early-stage participation vs visible depth
UNWIND_MAX_PARTICIPATION = 0.30    # late-stage participation vs visible depth
UNWIND_MIN_ORDER_SIZE = 1000      # avoid tiny slices on large positions

# ========== Auction Pricing ==========
AUCTION_PROFIT_MARGIN = 0.02    # target profit per share on auction bids
AUCTION_SAFETY_MARGIN = 0.01    # buffer beyond break-even

# ========== End-of-Case Safety ==========
ENDGAME_UNWIND_TICKS = 100      # when remaining ticks <= this, pace out remaining position

# ========== Misc ==========
EPS = 1e-9
DEBUG_BOOK = False
