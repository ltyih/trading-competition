# -*- coding: utf-8 -*-
"""
ALGO MARKET MAKING BOT - V12 AGGREGATE-LIMIT-AWARE REBATE HARVESTER
====================================================================
Complete rewrite from V11 based on analysis of 6 performance reports.

V11 FAILURE ANALYSIS:
- Run 1: -$5,907 (328k vol, no fines but adverse selection)
- Run 2: -$4,089 (302k vol)
- Run 3: -$1,335 (273k vol, improving)
- Run 4: -$2,990 (306k vol, SMMR first profitable: +$62)
- Run 5: -$2,272 (500k vol, SMMR +$3,335, WNTR +$798, but SPNG -$3,966)
- Run 6: -$1,722 (23k vol, stopped early)

KEY INSIGHT FROM DATA:
- WNTR and SMMR (highest rebates) ARE profitable
- SPNG and ATMN (lowest rebates) consistently LOSE money
- Passive:Active ratios are good (1.4-2.9x) = rebate capture works
- The losses come from: adverse selection on low-rebate stocks + position penalties

V12 STRATEGY:
1. AGGRESSIVE on WNTR/SMMR (tight spreads, large sizes, high priority)
2. CONSERVATIVE on ATMN/SPNG (wide spreads, small sizes, reduced priority)
3. RESPECT THE CLOSE: flatten positions before each 60-second market close
4. EARN REBATES: limit orders only, market orders only for emergency flatten
5. SURVIVE: avoid $10/share aggregate penalty at all costs
"""

import sys
import os
import time
import logging
from datetime import datetime
from collections import deque
from typing import Dict, List, Optional, Tuple

os.environ['PYTHONUNBUFFERED'] = '1'
_orig_print = print
_file_logger = None


def print(*args, **kwargs):
    kwargs.setdefault('flush', True)
    _orig_print(*args, **kwargs)
    if _file_logger:
        msg = " ".join(str(a) for a in args)
        _file_logger.info(msg)


from config import (
    TICKERS, REBATES, MARKET_ORDER_FEE,
    BASE_SPREAD, ORDER_SIZE, MAX_ORDER_SIZE,
    VOL_SIZE_MULT,
    SKEW_FACTOR,
    UTIL_NORMAL, UTIL_SKEW, UTIL_REDUCE, UTIL_EMERGENCY, UTIL_PANIC,
    DEFAULT_AGGREGATE_LIMIT, DEFAULT_GROSS_LIMIT, DEFAULT_NET_LIMIT,
    GROSS_LIMIT_BUFFER, NET_LIMIT_BUFFER,
    PER_STOCK_LIMIT_FRACTION, MIN_PER_STOCK_LIMIT,
    CYCLE_SLEEP, HEAT_DURATION, DAY_LENGTH,
    PRE_CLOSE_WIDEN_SEC, PRE_CLOSE_REDUCE_SEC,
    PRE_CLOSE_CANCEL_SEC, PRE_CLOSE_FLATTEN_SEC,
    POST_CLOSE_RECOVERY_SEC, POST_CLOSE_SPREAD_MULT, POST_CLOSE_SIZE_MULT,
    PRE_CLOSE_SPREAD_MULT, PRE_CLOSE_SIZE_MULT,
    VOL_LOOKBACK, VOL_LOW_THRESHOLD, VOL_HIGH_THRESHOLD, VOL_SPREAD_MULT,
    MIN_PRICE, MAX_PRICE,
    LOG_INTERVAL_TICKS, REQUOTE_THRESHOLD,
    IMBALANCE_THRESHOLD, IMBALANCE_SKEW_FACTOR,
    PENNY_IMPROVE,
    CIRCUIT_BREAKER_CAUTION, CIRCUIT_BREAKER_HALT,
    ADAPTIVE_INTERVAL, ADAPTIVE_MIN_MULT, ADAPTIVE_MAX_MULT,
    ADAPTIVE_TARGET_FILLS,
    ENABLE_LAYERED_QUOTES, LAYER2_SPREAD_MULT, LAYER2_SIZE_MULT,
    ASYM_REDUCE_MAX, ASYM_INCREASE_MIN, ASYM_KICK_IN,
    API_BASE_URL, API_KEY,
)
from api import RITApi

logger = logging.getLogger(__name__)

BANNER = """
================================================================================
  ALGO MARKET MAKING BOT V12 - AGGREGATE-LIMIT-AWARE REBATE HARVESTER
  Strategy: WNTR/SMMR aggressive, ATMN/SPNG conservative, flatten before close
  {url} | Key: {key}
================================================================================
"""


# ============================================================
# VOLATILITY TRACKER
# ============================================================

class VolatilityTracker:
    def __init__(self, lookback: int = VOL_LOOKBACK):
        self.lookback = lookback
        self._history: Dict[str, deque] = {}

    def update(self, ticker: str, tick: int, mid_price: float):
        if mid_price <= 0:
            return
        if ticker not in self._history:
            self._history[ticker] = deque(maxlen=200)
        self._history[ticker].append((tick, mid_price))

    def get_regime(self, ticker: str) -> str:
        hist = self._history.get(ticker)
        if not hist or len(hist) < 3:
            return "MEDIUM"
        recent = list(hist)[-self.lookback:]
        if len(recent) < 2:
            return "MEDIUM"
        changes = [abs(recent[i][1] - recent[i-1][1]) for i in range(1, len(recent))]
        avg_change = sum(changes) / len(changes) if changes else 0
        if avg_change < VOL_LOW_THRESHOLD:
            return "LOW"
        elif avg_change > VOL_HIGH_THRESHOLD:
            return "HIGH"
        return "MEDIUM"

    def get_recent_move(self, ticker: str, lookback: int = 5) -> float:
        """Get the total price move over last N observations."""
        hist = self._history.get(ticker)
        if not hist or len(hist) < lookback + 1:
            return 0.0
        recent = list(hist)[-lookback:]
        return recent[-1][1] - recent[0][1]


# ============================================================
# ORDER TRACKER
# ============================================================

class OrderTracker:
    """Track our live orders per ticker per side per layer."""
    def __init__(self):
        self._orders: Dict[str, Dict[str, Optional[Dict]]] = {}

    def _key(self, side: str, layer: int = 1) -> str:
        return f"{side}_L{layer}"

    def record_order(self, ticker: str, side: str, order_id: int,
                     price: float, size: int, layer: int = 1):
        if ticker not in self._orders:
            self._orders[ticker] = {}
        key = self._key(side, layer)
        self._orders[ticker][key] = {"id": order_id, "price": price, "size": size}

    def get_order(self, ticker: str, side: str, layer: int = 1) -> Optional[Dict]:
        return self._orders.get(ticker, {}).get(self._key(side, layer))

    def clear_ticker(self, ticker: str):
        self._orders[ticker] = {}

    def clear_all(self):
        for t in list(self._orders.keys()):
            self._orders[t] = {}

    def has_order(self, ticker: str, side: str, layer: int = 1) -> bool:
        return self.get_order(ticker, side, layer) is not None

    def has_any_order(self, ticker: str) -> bool:
        orders = self._orders.get(ticker, {})
        return any(v is not None for v in orders.values())

    def order_is_close_enough(self, ticker: str, side: str,
                               target_price: float, tolerance: float,
                               layer: int = 1) -> bool:
        existing = self.get_order(ticker, side, layer)
        if not existing:
            return False
        return abs(existing["price"] - target_price) <= tolerance


# ============================================================
# MARKET MAKER - V12 CORE
# ============================================================

class MarketMaker:
    def __init__(self, api: RITApi):
        self.api = api
        self.vol_tracker = VolatilityTracker()
        self.order_tracker = OrderTracker()

        # State
        self.positions: Dict[str, int] = {t: 0 for t in TICKERS}
        self.mid_prices: Dict[str, float] = {}
        self.book_imbalance: Dict[str, float] = {t: 0.0 for t in TICKERS}
        self.market_spread: Dict[str, float] = {t: 0.10 for t in TICKERS}

        # Limits - V12: properly separated
        self.gross_limit = DEFAULT_GROSS_LIMIT
        self.net_limit = DEFAULT_NET_LIMIT
        self.aggregate_limit = DEFAULT_AGGREGATE_LIMIT  # This is the critical one
        self.per_stock_limit: Dict[str, int] = {t: MIN_PER_STOCK_LIMIT for t in TICKERS}

        # P&L tracking
        self.start_nlv = 0.0
        self.current_pnl = 0.0
        self.circuit_breaker_active = False
        self.circuit_breaker_halt = False

        # Adaptive spreads
        self.adaptive_mult: Dict[str, float] = {t: 1.0 for t in TICKERS}
        self.fill_count: Dict[str, int] = {t: 0 for t in TICKERS}
        self.last_adaptive_tick = 0

        # Stats
        self.orders_placed = 0
        self.orders_cancelled = 0
        self.total_volume_traded = 0
        self.market_orders_sent = 0
        self.passive_fills_est = 0

        # Position tracking for fill detection
        self.last_known_positions: Dict[str, int] = {t: 0 for t in TICKERS}

        # Pre-close price memory (for post-news analysis)
        self.pre_close_prices: Dict[str, float] = {}

        # Track if we're in lockdown
        self.in_lockdown = False

    # ============================================================
    # STATE UPDATES
    # ============================================================

    def update_state(self, tick: int):
        """Fetch current positions and prices from API."""
        securities = self.api.get_securities()
        if not securities:
            return

        for sec in securities:
            t = sec.get("ticker", "")
            if t not in TICKERS:
                continue

            old_pos = self.positions.get(t, 0)
            self.positions[t] = sec.get("position", 0)

            # Track fills
            pos_change = abs(self.positions[t] - old_pos)
            if pos_change > 0:
                self.fill_count[t] = self.fill_count.get(t, 0) + 1
                self.total_volume_traded += pos_change

            bid = sec.get("bid", 0) or 0
            ask = sec.get("ask", 0) or 0
            if bid > 0 and ask > 0:
                mid = (bid + ask) / 2.0
                self.mid_prices[t] = mid
                self.vol_tracker.update(t, tick, mid)
                self.market_spread[t] = ask - bid

    def update_limits(self):
        """Fetch and parse limits from API.

        V12 CRITICAL: We need to identify both the gross/net limits AND the
        aggregate position limit. The aggregate limit has the worst penalty
        ($10/share vs $5/share) and is checked at market close.
        """
        limits = self.api.get_limits()
        if not limits:
            return

        for lim in limits:
            name = lim.get("name", "").lower()

            # Try to identify aggregate limit
            gl = lim.get("gross_limit", 0)
            nl = lim.get("net_limit", 0)

            if gl and isinstance(gl, (int, float)) and gl > 0:
                self.gross_limit = int(gl)
            if nl and isinstance(nl, (int, float)) and nl > 0:
                self.net_limit = int(nl)

            # The aggregate limit may be reported differently
            # Try multiple field names
            for field in ["gross_limit", "aggregate_limit", "position_limit"]:
                val = lim.get(field, 0)
                if val and isinstance(val, (int, float)) and val > 0:
                    # Use the smaller of gross and what we find as aggregate
                    if field == "gross_limit":
                        self.aggregate_limit = int(val)

        # Compute per-stock limits based on aggregate limit and rebate weighting
        total_rebate = sum(REBATES[t] for t in TICKERS)
        for t in TICKERS:
            weight = REBATES[t] / total_rebate
            allocated = int(self.aggregate_limit * PER_STOCK_LIMIT_FRACTION * weight / 0.25)
            allocated = max(MIN_PER_STOCK_LIMIT, min(allocated,
                            int(self.aggregate_limit * PER_STOCK_LIMIT_FRACTION)))
            self.per_stock_limit[t] = allocated

    def update_pnl(self):
        """Track P&L and trigger circuit breakers."""
        nlv = self.api.get_nlv()
        if nlv is not None and self.start_nlv > 0:
            self.current_pnl = nlv - self.start_nlv

        if self.current_pnl <= CIRCUIT_BREAKER_HALT:
            if not self.circuit_breaker_halt:
                print(f"    [CB HALT] PnL=${self.current_pnl:,.2f} <= ${CIRCUIT_BREAKER_HALT:,}")
                self.circuit_breaker_halt = True
                self.api.cancel_all_orders()
                self.order_tracker.clear_all()
        elif self.current_pnl <= CIRCUIT_BREAKER_CAUTION:
            if not self.circuit_breaker_active:
                print(f"    [CB CAUTION] PnL=${self.current_pnl:,.2f}")
                self.circuit_breaker_active = True
        else:
            if self.circuit_breaker_active:
                print(f"    [CB CLEAR] PnL=${self.current_pnl:,.2f}")
                self.circuit_breaker_active = False

    def update_adaptive_spreads(self, tick: int):
        """Adjust spreads based on fill rate. V12: more conservative than V11."""
        if tick - self.last_adaptive_tick < ADAPTIVE_INTERVAL:
            return

        for ticker in TICKERS:
            fills = self.fill_count.get(ticker, 0)
            if fills < 2:
                # Not enough fills - tighten slightly
                self.adaptive_mult[ticker] *= 0.95  # V12: gentler than V11's 0.92
            elif fills > ADAPTIVE_TARGET_FILLS * 2:
                # Too many fills - might be adverse selection, widen
                self.adaptive_mult[ticker] *= 1.08
            elif fills > ADAPTIVE_TARGET_FILLS:
                # Good fill rate, slight widen for safety
                self.adaptive_mult[ticker] *= 1.02

            self.adaptive_mult[ticker] = max(
                ADAPTIVE_MIN_MULT, min(ADAPTIVE_MAX_MULT, self.adaptive_mult[ticker]))
            self.fill_count[ticker] = 0
        self.last_adaptive_tick = tick

    # ============================================================
    # POSITION CALCULATIONS
    # ============================================================

    def compute_aggregate(self) -> int:
        """Sum of absolute positions across all stocks.
        This is what's checked at market close with $10/share penalty."""
        return sum(abs(p) for p in self.positions.values())

    def compute_utilization(self) -> float:
        """Aggregate position as fraction of aggregate limit."""
        if self.aggregate_limit <= 0:
            return 0.0
        return self.compute_aggregate() / self.aggregate_limit

    def compute_gross(self) -> int:
        """Gross position (same as aggregate in this case)."""
        return sum(abs(p) for p in self.positions.values())

    def compute_net(self) -> int:
        """Net position across all stocks."""
        return abs(sum(self.positions.values()))

    def compute_book_imbalance(self, book: Dict) -> float:
        bids = book.get("bids", book.get("bid", []))
        asks = book.get("asks", book.get("ask", []))
        bid_vol = sum(o.get("quantity", 0) - o.get("quantity_filled", 0) for o in (bids or [])[:5])
        ask_vol = sum(o.get("quantity", 0) - o.get("quantity_filled", 0) for o in (asks or [])[:5])
        total = bid_vol + ask_vol
        if total < 100:
            return 0.0
        return (bid_vol - ask_vol) / total

    # ============================================================
    # SPREAD COMPUTATION
    # ============================================================

    def compute_spread(self, ticker: str, second_in_day: int, layer: int = 1) -> float:
        """Compute spread for a given ticker, time, and layer."""
        base = BASE_SPREAD.get(ticker, 0.04)

        # Layer 2 is wider
        if layer == 2:
            base *= LAYER2_SPREAD_MULT

        # Volatility regime
        regime = self.vol_tracker.get_regime(ticker)
        vol_mult = VOL_SPREAD_MULT.get(regime, 1.0)

        # Adaptive (learned from fill rate)
        adaptive = self.adaptive_mult.get(ticker, 1.0)

        # Time of day
        time_mult = 1.0
        if second_in_day < POST_CLOSE_RECOVERY_SEC:
            time_mult = POST_CLOSE_SPREAD_MULT
        elif second_in_day >= PRE_CLOSE_WIDEN_SEC:
            time_mult = PRE_CLOSE_SPREAD_MULT

        # Circuit breaker: widen spreads when losing money
        cb_mult = 1.5 if self.circuit_breaker_active else 1.0

        spread = base * vol_mult * adaptive * time_mult * cb_mult

        # Minimum spread: at least the rebate to ensure profitability
        min_spread = max(0.02, REBATES.get(ticker, 0.02) * 0.5)
        return max(min_spread, round(spread, 4))

    # ============================================================
    # ORDER SIZING - V12: AGGREGATE-AWARE
    # ============================================================

    def compute_order_size(self, ticker: str, side: str, utilization: float,
                           second_in_day: int, layer: int = 1) -> int:
        """V12: Conservative sizing that respects aggregate limit."""
        base_size = ORDER_SIZE.get(ticker, 1000)
        pos = self.positions.get(ticker, 0)
        per_stock_limit = self.per_stock_limit.get(ticker, MIN_PER_STOCK_LIMIT)

        # Layer 2 is smaller
        layer_mult = LAYER2_SIZE_MULT if layer == 2 else 1.0

        # ASYMMETRIC SIZING
        asym_mult = 1.0
        asym_threshold = per_stock_limit * ASYM_KICK_IN
        if abs(pos) > asym_threshold:
            pos_frac = min(abs(pos) / per_stock_limit, 1.0)
            if (pos > 0 and side == "SELL") or (pos < 0 and side == "BUY"):
                # REDUCING position: boost size
                asym_mult = 1.0 + pos_frac * (ASYM_REDUCE_MAX - 1.0)
            elif (pos > 0 and side == "BUY") or (pos < 0 and side == "SELL"):
                # INCREASING position: reduce size
                asym_mult = max(ASYM_INCREASE_MIN, 1.0 - pos_frac * (1.0 - ASYM_INCREASE_MIN))

        # Volatility
        regime = self.vol_tracker.get_regime(ticker)
        vol_mult = VOL_SIZE_MULT.get(regime, 1.0)

        # Utilization-based reduction (V12: much more aggressive reduction)
        if utilization > UTIL_PANIC:
            util_mult = 0.0  # Don't place new orders at all
        elif utilization > UTIL_EMERGENCY:
            util_mult = 0.15
        elif utilization > UTIL_REDUCE:
            util_mult = 0.4
        elif utilization > UTIL_SKEW:
            util_mult = 0.7
        else:
            util_mult = 1.0

        # Time of day
        time_mult = 1.0
        if second_in_day < POST_CLOSE_RECOVERY_SEC:
            time_mult = POST_CLOSE_SIZE_MULT
        elif second_in_day >= PRE_CLOSE_WIDEN_SEC:
            time_mult = PRE_CLOSE_SIZE_MULT

        # Circuit breaker
        cb_mult = 0.3 if self.circuit_breaker_active else 1.0

        size = int(base_size * layer_mult * asym_mult *
                   vol_mult * util_mult * time_mult * cb_mult)

        # Hard cap: don't push past per-stock limit on increasing side
        if (pos > 0 and side == "BUY") or (pos < 0 and side == "SELL"):
            room = max(0, per_stock_limit - abs(pos))
            size = min(size, room)

        # Gross limit room
        gross = self.compute_gross()
        gross_room = max(0, int(self.gross_limit * GROSS_LIMIT_BUFFER) - gross)
        if (pos > 0 and side == "BUY") or (pos < 0 and side == "SELL") or pos == 0:
            size = min(size, gross_room)

        # Aggregate limit room (even more conservative)
        agg_room = max(0, int(self.aggregate_limit * UTIL_REDUCE) - gross)
        if (pos > 0 and side == "BUY") or (pos < 0 and side == "SELL") or pos == 0:
            size = min(size, agg_room)

        return max(0, min(size, MAX_ORDER_SIZE))

    # ============================================================
    # INVENTORY SKEW
    # ============================================================

    def compute_skew(self, ticker: str, utilization: float) -> float:
        """Avellaneda-Stoikov style inventory skew."""
        pos = self.positions.get(ticker, 0)
        per_stock_limit = self.per_stock_limit.get(ticker, MIN_PER_STOCK_LIMIT)
        if per_stock_limit <= 0:
            return 0.0

        normalized = pos / per_stock_limit
        skew = -normalized * SKEW_FACTOR

        # Increase skew when utilization is high
        if utilization > UTIL_REDUCE:
            skew *= 2.0
        elif utilization > UTIL_SKEW:
            skew *= 1.5

        # Book imbalance (gentle)
        imbalance = self.book_imbalance.get(ticker, 0.0)
        if abs(imbalance) > IMBALANCE_THRESHOLD:
            skew += imbalance * IMBALANCE_SKEW_FACTOR

        return round(skew, 4)

    # ============================================================
    # QUOTING DECISIONS
    # ============================================================

    def should_quote_side(self, ticker: str, side: str, utilization: float) -> bool:
        """Determine if we should quote a given side for a ticker."""
        pos = self.positions.get(ticker, 0)
        per_stock_limit = self.per_stock_limit.get(ticker, MIN_PER_STOCK_LIMIT)

        if self.circuit_breaker_halt:
            return False

        if self.in_lockdown:
            return False

        # Block at 90% of per-stock limit for increasing side only
        block_threshold = int(per_stock_limit * 0.90)
        if side == "BUY" and pos >= block_threshold:
            return False
        if side == "SELL" and pos <= -block_threshold:
            return False

        # At high utilization, only allow reducing side
        if utilization > UTIL_EMERGENCY:
            if (pos > 0 and side == "BUY") or (pos < 0 and side == "SELL"):
                return False
            if pos == 0:
                return False

        # Gross limit check
        gross = self.compute_gross()
        gross_max = int(self.gross_limit * GROSS_LIMIT_BUFFER)
        if gross >= gross_max:
            if (pos > 0 and side == "BUY") or (pos < 0 and side == "SELL"):
                return False
            if pos == 0:
                return False

        # Net limit check
        net = self.compute_net()
        net_max = int(self.net_limit * NET_LIMIT_BUFFER)
        if net >= net_max:
            net_dir = sum(self.positions.values())
            if (net_dir > 0 and side == "BUY") or (net_dir < 0 and side == "SELL"):
                return False

        return True

    # ============================================================
    # MAIN QUOTING ENGINE - V12 WITH LAYERED QUOTES
    # ============================================================

    def quote_ticker(self, ticker: str, tick: int, second_in_day: int,
                     utilization: float):
        """Place/update quotes for a single ticker."""
        mid = self.mid_prices.get(ticker, 25.0)
        if mid < MIN_PRICE or mid > MAX_PRICE:
            return

        skew = self.compute_skew(ticker, utilization)
        quote_buy = self.should_quote_side(ticker, "BUY", utilization)
        quote_sell = self.should_quote_side(ticker, "SELL", utilization)

        if not quote_buy and not quote_sell:
            # Nothing to do, cancel existing
            if self.order_tracker.has_any_order(ticker):
                self.api.cancel_ticker_orders(ticker)
                self.order_tracker.clear_ticker(ticker)
            return

        # Get market data
        book = self.api.get_book(ticker, limit=5)
        best_bid = self.api.get_best_bid(book)
        best_ask = self.api.get_best_ask(book)
        if best_bid > 0 and best_ask > 0:
            self.book_imbalance[ticker] = self.compute_book_imbalance(book)
            self.market_spread[ticker] = best_ask - best_bid
            # Update mid from book if available
            mid = (best_bid + best_ask) / 2.0

        # ---- LAYER 1: Primary quotes ----
        spread1 = self.compute_spread(ticker, second_in_day, layer=1)
        half1 = spread1 / 2.0
        buy_size1 = self.compute_order_size(ticker, "BUY", utilization, second_in_day, layer=1) if quote_buy else 0
        sell_size1 = self.compute_order_size(ticker, "SELL", utilization, second_in_day, layer=1) if quote_sell else 0

        bid1 = round(mid - half1 + skew, 2)
        ask1 = round(mid + half1 + skew, 2)

        # Safety: bid must be below ask
        if bid1 >= ask1:
            bid1 = round(mid - 0.02, 2)
            ask1 = round(mid + 0.02, 2)
        bid1 = max(MIN_PRICE, min(bid1, MAX_PRICE - 0.02))
        ask1 = max(bid1 + 0.01, min(ask1, MAX_PRICE))

        # ---- LAYER 2: Secondary quotes (wider, smaller) ----
        bid2 = 0.0
        ask2 = 0.0
        buy_size2 = 0
        sell_size2 = 0

        if ENABLE_LAYERED_QUOTES and second_in_day >= POST_CLOSE_RECOVERY_SEC and second_in_day < PRE_CLOSE_WIDEN_SEC:
            spread2 = self.compute_spread(ticker, second_in_day, layer=2)
            half2 = spread2 / 2.0
            buy_size2 = self.compute_order_size(ticker, "BUY", utilization, second_in_day, layer=2) if quote_buy else 0
            sell_size2 = self.compute_order_size(ticker, "SELL", utilization, second_in_day, layer=2) if quote_sell else 0

            bid2 = round(mid - half2 + skew, 2)
            ask2 = round(mid + half2 + skew, 2)

            if bid2 >= bid1:
                bid2 = round(bid1 - 0.02, 2)
            if ask2 <= ask1:
                ask2 = round(ask1 + 0.02, 2)

            bid2 = max(MIN_PRICE, bid2)
            ask2 = min(MAX_PRICE, ask2)

        # ---- SMART CANCEL AND REPLACE ----
        tolerance = half1 * REQUOTE_THRESHOLD
        need_cancel = False

        for side, target, size in [("BUY", bid1, buy_size1), ("SELL", ask1, sell_size1)]:
            if size >= 100:
                if not self.order_tracker.order_is_close_enough(ticker, side, target, tolerance, layer=1):
                    need_cancel = True
                    break
            elif self.order_tracker.has_order(ticker, side, layer=1):
                need_cancel = True
                break

        if not need_cancel and ENABLE_LAYERED_QUOTES:
            for side, target, size in [("BUY", bid2, buy_size2), ("SELL", ask2, sell_size2)]:
                if size >= 100:
                    if not self.order_tracker.order_is_close_enough(ticker, side, target, tolerance * 2, layer=2):
                        need_cancel = True
                        break

        if need_cancel or not self.order_tracker.has_any_order(ticker):
            self.api.cancel_ticker_orders(ticker)
            self.order_tracker.clear_ticker(ticker)
            self.orders_cancelled += 1

            # Place Layer 1
            if quote_buy and buy_size1 >= 100:
                result = self.api.submit_limit_order(ticker, buy_size1, "BUY", bid1)
                if result and isinstance(result, dict):
                    self.order_tracker.record_order(
                        ticker, "BUY", result.get("order_id", 0), bid1, buy_size1, layer=1)
                    self.orders_placed += 1

            if quote_sell and sell_size1 >= 100:
                result = self.api.submit_limit_order(ticker, sell_size1, "SELL", ask1)
                if result and isinstance(result, dict):
                    self.order_tracker.record_order(
                        ticker, "SELL", result.get("order_id", 0), ask1, sell_size1, layer=1)
                    self.orders_placed += 1

            # Place Layer 2
            if ENABLE_LAYERED_QUOTES and second_in_day >= POST_CLOSE_RECOVERY_SEC and second_in_day < PRE_CLOSE_WIDEN_SEC:
                if quote_buy and buy_size2 >= 100:
                    result = self.api.submit_limit_order(ticker, buy_size2, "BUY", bid2)
                    if result and isinstance(result, dict):
                        self.order_tracker.record_order(
                            ticker, "BUY", result.get("order_id", 0), bid2, buy_size2, layer=2)
                        self.orders_placed += 1

                if quote_sell and sell_size2 >= 100:
                    result = self.api.submit_limit_order(ticker, sell_size2, "SELL", ask2)
                    if result and isinstance(result, dict):
                        self.order_tracker.record_order(
                            ticker, "SELL", result.get("order_id", 0), ask2, sell_size2, layer=2)
                        self.orders_placed += 1

    # ============================================================
    # PROACTIVE POSITION REDUCTION
    # ============================================================

    def reduce_large_positions(self, tick: int):
        """Reduce positions approaching per-stock or aggregate limits.
        V12: Uses passive orders when possible to earn rebates."""
        for ticker in TICKERS:
            pos = self.positions.get(ticker, 0)
            per_stock_limit = self.per_stock_limit.get(ticker, MIN_PER_STOCK_LIMIT)
            threshold = int(per_stock_limit * 0.70)

            if abs(pos) < threshold:
                continue

            mid = self.mid_prices.get(ticker, 25.0)
            excess = abs(pos) - threshold

            if abs(pos) > per_stock_limit * 0.90:
                # Emergency: market order
                mkt_size = min(excess, int(per_stock_limit * 0.20))
                mkt_size = max(100, min(mkt_size, MAX_ORDER_SIZE))
                side = "SELL" if pos > 0 else "BUY"
                self.api.submit_market_order(ticker, mkt_size, side)
                self.market_orders_sent += 1
                print(f"    [REDUCE-MKT] {ticker}: {side} {mkt_size} (pos={pos})")
            else:
                # Passive: aggressive limit order
                reduce_size = min(excess, int(per_stock_limit * 0.20))
                reduce_size = max(100, min(reduce_size, MAX_ORDER_SIZE))
                if pos > 0:
                    price = round(mid + 0.01, 2)
                    self.api.submit_limit_order(ticker, reduce_size, "SELL", price)
                else:
                    price = round(mid - 0.01, 2)
                    self.api.submit_limit_order(ticker, reduce_size, "BUY", price)

    # ============================================================
    # MARKET CLOSE PROTOCOL - V12 CORE (CRITICAL)
    # ============================================================

    def pre_close_handler(self, tick: int, second_in_day: int):
        """The most important function in the algorithm.

        The aggregate position limit is checked at each market close (every 60s).
        Penalty is $10/share for EVERY share above the limit.
        A 5,000 share overshoot = $50,000 penalty. This wipes hours of profit.

        Timeline:
        - Second 45-47: Widen spreads, reduce sizes (handled by spread/size functions)
        - Second 48-51: Begin passive flattening (limit orders near mid)
        - Second 52: CANCEL ALL ORDERS
        - Second 53-60: Market-order flatten if aggregate > safe threshold
        """

        aggregate = self.compute_aggregate()
        utilization = aggregate / self.aggregate_limit if self.aggregate_limit > 0 else 0

        if second_in_day >= PRE_CLOSE_FLATTEN_SEC:
            # Phase 3: AGGRESSIVE FLATTEN
            # Target: get aggregate below 80% of limit (safer: 60%)
            target_aggregate = int(self.aggregate_limit * 0.60)

            if aggregate > target_aggregate:
                # Sort by absolute position, flatten largest first
                sorted_positions = sorted(
                    [(t, self.positions[t]) for t in TICKERS if abs(self.positions[t]) > 0],
                    key=lambda x: abs(x[1]),
                    reverse=True
                )

                remaining = aggregate - target_aggregate
                for ticker, pos in sorted_positions:
                    if remaining <= 0:
                        break
                    to_reduce = min(abs(pos), remaining)
                    to_reduce = max(100, min(to_reduce, MAX_ORDER_SIZE))
                    side = "SELL" if pos > 0 else "BUY"
                    self.api.submit_market_order(ticker, to_reduce, side)
                    self.market_orders_sent += 1
                    remaining -= to_reduce
                    print(f"    [PRE-CLOSE FLATTEN] {ticker}: MKT {side} {to_reduce} "
                          f"(pos={pos}, agg={aggregate}, lim={self.aggregate_limit})")

        elif second_in_day >= PRE_CLOSE_CANCEL_SEC:
            # Phase 2: CANCEL ALL ORDERS
            if not self.in_lockdown:
                self.api.cancel_all_orders()
                self.order_tracker.clear_all()
                self.in_lockdown = True
                print(f"    [LOCKDOWN] T{tick} sec={second_in_day} "
                      f"agg={aggregate}/{self.aggregate_limit} ({utilization:.0%})")

        elif second_in_day >= PRE_CLOSE_REDUCE_SEC:
            # Phase 1: PASSIVE FLATTEN - place aggressive limit orders to reduce
            if utilization > 0.50:
                for ticker in TICKERS:
                    pos = self.positions.get(ticker, 0)
                    if abs(pos) < 200:
                        continue
                    mid = self.mid_prices.get(ticker, 25.0)
                    reduce_size = min(abs(pos), 3000)
                    reduce_size = max(100, min(reduce_size, MAX_ORDER_SIZE))
                    if pos > 0:
                        # Sell at or slightly below mid for fast fill
                        price = round(mid - 0.01, 2)
                        self.api.submit_limit_order(ticker, reduce_size, "SELL", price)
                    else:
                        price = round(mid + 0.01, 2)
                        self.api.submit_limit_order(ticker, reduce_size, "BUY", price)

        # Save pre-close prices for post-news analysis
        if second_in_day == PRE_CLOSE_CANCEL_SEC:
            for t in TICKERS:
                self.pre_close_prices[t] = self.mid_prices.get(t, 25.0)

    # ============================================================
    # EMERGENCY HANDLERS
    # ============================================================

    def emergency_flatten(self, target_util: float = 0.70):
        """Emergency flatten when aggregate is too high during trading."""
        current = self.compute_aggregate()
        target = int(self.aggregate_limit * target_util)
        if current <= target:
            return

        self.api.cancel_all_orders()
        self.order_tracker.clear_all()

        excess = current - target
        sorted_positions = sorted(
            [(t, self.positions[t]) for t in TICKERS if abs(self.positions[t]) > 0],
            key=lambda x: abs(x[1]),
            reverse=True
        )

        remaining = excess
        for ticker, pos in sorted_positions:
            if remaining <= 0:
                break
            to_reduce = min(abs(pos), remaining)
            to_reduce = max(100, min(to_reduce, MAX_ORDER_SIZE))
            side = "SELL" if pos > 0 else "BUY"
            self.api.submit_market_order(ticker, to_reduce, side)
            self.market_orders_sent += 1
            remaining -= to_reduce
            print(f"    [EMERGENCY FLATTEN] {ticker}: MKT {side} {to_reduce}")

    def panic_flatten(self):
        """PANIC: flatten everything immediately."""
        self.api.cancel_all_orders()
        self.order_tracker.clear_all()
        for ticker in TICKERS:
            pos = self.positions.get(ticker, 0)
            if abs(pos) >= 100:
                side = "SELL" if pos > 0 else "BUY"
                qty = min(abs(pos), MAX_ORDER_SIZE)
                self.api.submit_market_order(ticker, qty, side)
                self.market_orders_sent += 1
                print(f"    [PANIC] {ticker}: MKT {side} {qty}")

    # ============================================================
    # MAIN LOOP - V12
    # ============================================================

    def run(self):
        print(BANNER.format(url=API_BASE_URL, key=API_KEY))

        # Initialize limits
        self.update_limits()
        print(f"  Aggregate limit: {self.aggregate_limit:,}")
        print(f"  Gross limit: {self.gross_limit:,}")
        print(f"  Net limit: {self.net_limit:,}")
        for t in TICKERS:
            print(f"  {t}: per_stock={self.per_stock_limit[t]:,}, "
                  f"base_size={ORDER_SIZE[t]}, rebate=${REBATES[t]}, "
                  f"spread={BASE_SPREAD[t]}")

        nlv = self.api.get_nlv()
        if nlv is not None:
            self.start_nlv = nlv
            print(f"  Starting NLV: ${nlv:,.2f}")

        last_tick = -1
        last_log_tick = -999
        last_day = -1

        while True:
            try:
                case = self.api.get_case()
                if not case:
                    time.sleep(0.5)
                    continue
                status = case.get("status", "")
                if status != "ACTIVE":
                    if status == "STOPPED":
                        print("\n  [SIMULATION ENDED]")
                        self.update_pnl()
                        print(f"  Final P&L: ${self.current_pnl:,.2f}")
                        print(f"  Orders: +{self.orders_placed}/-{self.orders_cancelled}")
                        print(f"  Market orders: {self.market_orders_sent}")
                        print(f"  Est. volume traded: {self.total_volume_traded:,}")
                        break
                    time.sleep(0.5)
                    continue

                tick = case.get("tick", 0)
                if tick == last_tick:
                    time.sleep(CYCLE_SLEEP * 0.3)
                    continue

                last_tick = tick
                second_in_day = tick % DAY_LENGTH
                current_day = tick // DAY_LENGTH

                # New day detected - reset lockdown
                if current_day != last_day:
                    if last_day >= 0:
                        print(f"\n  === DAY {current_day + 1} (tick {tick}) ===")
                    self.in_lockdown = False
                    last_day = current_day

                if self.circuit_breaker_halt:
                    time.sleep(1.0)
                    continue

                # UPDATE STATE
                self.update_state(tick)
                if tick % 5 == 0:
                    self.update_limits()
                if tick % 3 == 0:
                    self.update_pnl()
                self.update_adaptive_spreads(tick)

                utilization = self.compute_utilization()
                aggregate = self.compute_aggregate()
                net = self.compute_net()

                # ============================================
                # PHASE-BASED TRADING LOGIC
                # ============================================

                # PHASE: PRE-CLOSE (second >= 45)
                if second_in_day >= PRE_CLOSE_WIDEN_SEC:
                    self.pre_close_handler(tick, second_in_day)
                    time.sleep(CYCLE_SLEEP)
                    continue

                # PHASE: POST-CLOSE RECOVERY (second < 5)
                # Don't quote, just observe. Prices may be jumping from news.
                if second_in_day < POST_CLOSE_RECOVERY_SEC:
                    # Cancel any stale orders from before close
                    if self.order_tracker.has_any_order(TICKERS[0]):
                        self.api.cancel_all_orders()
                        self.order_tracker.clear_all()
                    time.sleep(CYCLE_SLEEP)
                    continue

                # PHASE: ACTIVE TRADING (second 5-44)
                self.in_lockdown = False

                # Emergency handling based on utilization
                if utilization > UTIL_PANIC:
                    self.panic_flatten()
                    time.sleep(CYCLE_SLEEP)
                    continue
                elif utilization > UTIL_EMERGENCY:
                    self.emergency_flatten(target_util=UTIL_REDUCE)

                # Proactive position reduction
                if tick % 3 == 0:
                    self.reduce_large_positions(tick)

                # MAIN QUOTING: quote all 4 tickers
                for ticker in TICKERS:
                    self.quote_ticker(ticker, tick, second_in_day, utilization)

                # LOGGING
                if tick - last_log_tick >= LOG_INTERVAL_TICKS:
                    pos_str = " | ".join(
                        f"{t}:{int(self.positions.get(t,0)):+d}" for t in TICKERS)
                    pnl_str = f"${self.current_pnl:+,.0f}" if self.start_nlv > 0 else "N/A"
                    regime_str = " ".join(
                        f"{t[0]}:{self.vol_tracker.get_regime(t)[0]}" for t in TICKERS)
                    print(f"  T{tick:03d} d{second_in_day:02d} | "
                          f"pos=[{pos_str}] | "
                          f"agg={aggregate:,}/{self.aggregate_limit:,}({utilization:.0%}) | "
                          f"net={net:,} | "
                          f"pnl={pnl_str} | vol={regime_str} | "
                          f"ord+{self.orders_placed}/-{self.orders_cancelled} | "
                          f"mkt={self.market_orders_sent} | "
                          f"tvol={self.total_volume_traded:,}")
                    last_log_tick = tick

                time.sleep(CYCLE_SLEEP)

            except KeyboardInterrupt:
                print("\n  [INTERRUPTED]")
                self.api.cancel_all_orders()
                break
            except Exception as e:
                logger.exception(f"Error: {e}")
                time.sleep(0.5)


# ============================================================
# STARTUP
# ============================================================

def wait_for_connection(api: RITApi):
    print("  Waiting for RIT connection...")
    while True:
        if api.get_case():
            print("  Connected!")
            return
        time.sleep(1.0)


def wait_for_active(api: RITApi) -> bool:
    print("  Waiting for ACTIVE status...")
    while True:
        case = api.get_case()
        if case:
            status = case.get("status", "")
            if status == "ACTIVE":
                print("  Simulation is ACTIVE!")
                return True
            elif status == "STOPPED":
                print("  Simulation STOPPED.")
                return False
        time.sleep(0.5)


def setup_logging():
    global _file_logger
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    _file_logger = logging.getLogger("file_logger")
    _file_logger.setLevel(logging.INFO)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    fh = logging.FileHandler(os.path.join(log_dir, f"mm_v12_{ts}.log"))
    fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _file_logger.addHandler(fh)


def main():
    setup_logging()
    api = RITApi()
    wait_for_connection(api)

    while True:
        if not wait_for_active(api):
            time.sleep(2)
            continue

        mm = MarketMaker(api)

        try:
            mm.run()
        except Exception as e:
            logger.exception(f"Fatal: {e}")
            try:
                api.cancel_all_orders()
            except Exception:
                pass

        print("\n  Waiting for next simulation...\n")
        time.sleep(3)


if __name__ == "__main__":
    main()