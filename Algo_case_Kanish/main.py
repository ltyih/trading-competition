# -*- coding: utf-8 -*-
"""
ALGO MARKET MAKING BOT - V13 TREND-FOLLOWING MARKET MAKER
===========================================================

THREE PILLARS OF V13:
1. TREND CAPTURE: EMA crossover detects price direction → skew quotes to
   accumulate inventory in the profitable direction
2. MOMENTUM TRADING: detect inter-day price jumps → ride momentum
3. TIGHT INVENTORY: ±5k per stock cap + 25x stronger skew → kills adverse selection

DATA-DRIVEN INSIGHTS:
- Your ±23k positions caused -$142k adverse selection (80% of losses)
- SPNG earned most ($7,817) despite lowest rebate (widest spreads, $2.85 range)
- WNTR LOST money ($-1,456) despite highest rebate (too narrow, high adverse sel.)
- Market spreads are 1-2 cents; intraday ranges are $0.50-$2.85
- Stocks are negatively correlated (diversification benefit)
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
    VOL_SIZE_MULT, SPREAD_MARKET_FACTOR, SPREAD_MIN_ABSOLUTE, SPREAD_MAX_ABSOLUTE,
    SKEW_FACTOR,
    TREND_FAST_ALPHA, TREND_SLOW_ALPHA, TREND_SKEW_FACTOR, TREND_MIN_SIGNAL,
    MOMENTUM_THRESHOLD, MOMENTUM_SKEW_FACTOR, MOMENTUM_DECAY,
    MOMENTUM_DURATION_SEC, MOMENTUM_SIZE_BOOST,
    RECENT_MOVE_LOOKBACK, RECENT_MOVE_WIDEN_THRESHOLD, RECENT_MOVE_WIDEN_MULT,
    UTIL_NORMAL, UTIL_SKEW, UTIL_REDUCE, UTIL_EMERGENCY, UTIL_PANIC,
    DEFAULT_GROSS_LIMIT, DEFAULT_NET_LIMIT, CLOSE_TIME_LIMIT,
    GROSS_LIMIT_BUFFER, NET_LIMIT_BUFFER,
    PER_STOCK_LIMIT_FRACTION, MIN_PER_STOCK_LIMIT, MAX_PER_STOCK_LIMIT,
    CYCLE_SLEEP, HEAT_DURATION, DAY_LENGTH,
    PRE_CLOSE_WIDEN_SEC, PRE_CLOSE_REDUCE_SEC,
    PRE_CLOSE_CANCEL_SEC, PRE_CLOSE_FLATTEN_SEC,
    CLOSE_TARGET_UTILIZATION,
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
    ENABLE_LAYERED_QUOTES, NUM_LAYERS,
    LAYER2_SPREAD_MULT, LAYER2_SIZE_MULT,
    LAYER3_SPREAD_MULT, LAYER3_SIZE_MULT,
    ASYM_REDUCE_MAX, ASYM_INCREASE_MIN, ASYM_KICK_IN,
    API_BASE_URL, API_KEY,
)
from api import RITApi

logger = logging.getLogger(__name__)

BANNER = """
================================================================================
  ALGO MARKET MAKING BOT V14 - VOLUME + REBATE MACHINE
  Tight spreads | Big sizes | Pending-aware limits | Simple spread calc
  Per-stock limit: {psl:,} | Gross: {gl:,} | Close: {cl:,}
  Cycle: {cycle}ms | Layers: {layers} | Skew: {skew}
  {url} | Key: {key}
================================================================================
"""


# ============================================================
# TREND TRACKER - EMA CROSSOVER + DAY-OPEN MOMENTUM
# ============================================================

class TrendTracker:
    """Detects intraday trends and inter-day momentum.

    Uses dual EMA crossover for trend direction and tracks
    price jumps at day boundaries for momentum signals.
    """

    def __init__(self):
        # EMA state per ticker
        self.fast_ema: Dict[str, float] = {}
        self.slow_ema: Dict[str, float] = {}

        # Price history for recent-move detection
        self.price_history: Dict[str, deque] = {}

        # Day-open momentum
        self.prev_day_close: Dict[str, float] = {}
        self.day_open_price: Dict[str, float] = {}
        self.momentum: Dict[str, float] = {}

        # State
        self.last_day: int = -1
        self.initialized: Dict[str, bool] = {}

    def on_new_day(self, day: int, current_prices: Dict[str, float]):
        """Called when a new trading day starts. Stores closes, resets EMAs."""
        if day > 0:
            # Store previous day closes and compute momentum
            for ticker in TICKERS:
                prev_close = self.prev_day_close.get(ticker, 0)
                new_price = current_prices.get(ticker, 0)

                if prev_close > 0 and new_price > 0:
                    jump = new_price - prev_close
                    jump_frac = jump / prev_close

                    # Convert to momentum signal: clamp to [-1, +1]
                    if abs(jump_frac) > MOMENTUM_THRESHOLD:
                        self.momentum[ticker] = max(-1.0, min(1.0, jump_frac * 20))
                    else:
                        self.momentum[ticker] = 0.0
                else:
                    self.momentum[ticker] = 0.0

        # Reset EMAs for new day (fresh start, no stale signal from old day)
        for ticker in TICKERS:
            mid = current_prices.get(ticker, 0)
            if mid > 0:
                self.fast_ema[ticker] = mid
                self.slow_ema[ticker] = mid
                self.initialized[ticker] = False
                self.day_open_price[ticker] = mid

        self.last_day = day

    def on_day_end(self, current_prices: Dict[str, float]):
        """Called before day transition. Store closing prices."""
        for ticker in TICKERS:
            mid = current_prices.get(ticker, 0)
            if mid > 0:
                self.prev_day_close[ticker] = mid

    def update(self, ticker: str, mid: float):
        """Update EMAs with latest price."""
        if mid <= 0:
            return

        # Price history for recent-move detection
        if ticker not in self.price_history:
            self.price_history[ticker] = deque(maxlen=30)
        self.price_history[ticker].append(mid)

        # Initialize EMAs on first valid price
        if ticker not in self.fast_ema or self.fast_ema[ticker] <= 0:
            self.fast_ema[ticker] = mid
            self.slow_ema[ticker] = mid
            self.initialized[ticker] = False
            return

        # Update EMAs
        self.fast_ema[ticker] = (TREND_FAST_ALPHA * mid +
                                  (1 - TREND_FAST_ALPHA) * self.fast_ema[ticker])
        self.slow_ema[ticker] = (TREND_SLOW_ALPHA * mid +
                                  (1 - TREND_SLOW_ALPHA) * self.slow_ema[ticker])

        # Mark as initialized after a few updates
        if not self.initialized.get(ticker, False):
            hist = self.price_history.get(ticker, deque())
            if len(hist) >= 5:
                self.initialized[ticker] = True

    def get_trend(self, ticker: str) -> float:
        """Get trend signal: -1 (bearish) to +1 (bullish).

        Based on EMA crossover: (fast - slow) / slow, scaled.
        Returns 0 if not enough data or signal below noise threshold.
        """
        if not self.initialized.get(ticker, False):
            return 0.0

        fast = self.fast_ema.get(ticker, 0)
        slow = self.slow_ema.get(ticker, 0)
        if slow <= 0:
            return 0.0

        raw_signal = (fast - slow) / slow * 10  # Scale ×10 for nuanced range

        if abs(raw_signal) < TREND_MIN_SIGNAL:
            return 0.0

        return max(-1.0, min(1.0, raw_signal))

    def get_momentum(self, ticker: str) -> float:
        """Get day-open momentum signal: -1 to +1."""
        return self.momentum.get(ticker, 0.0)

    def decay_momentum(self):
        """Decay momentum signals each tick."""
        for t in list(self.momentum.keys()):
            self.momentum[t] *= MOMENTUM_DECAY
            if abs(self.momentum[t]) < 0.01:
                self.momentum[t] = 0.0

    def get_recent_move(self, ticker: str) -> float:
        """Get absolute price move over recent ticks (for volatility-based widening)."""
        hist = self.price_history.get(ticker, deque())
        if len(hist) < RECENT_MOVE_LOOKBACK + 1:
            return 0.0
        recent = list(hist)
        return abs(recent[-1] - recent[-RECENT_MOVE_LOOKBACK - 1])

    def get_trend_direction_aligned(self, ticker: str, side: str) -> bool:
        """Check if a trade side aligns with the current trend."""
        trend = self.get_trend(ticker)
        if trend > TREND_MIN_SIGNAL and side == "BUY":
            return True
        if trend < -TREND_MIN_SIGNAL and side == "SELL":
            return True
        return False


# ============================================================
# VOLATILITY TRACKER (kept from V12.1)
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
        changes = [abs(recent[i][1] - recent[i - 1][1]) for i in range(1, len(recent))]
        avg_change = sum(changes) / len(changes) if changes else 0
        if avg_change < VOL_LOW_THRESHOLD:
            return "LOW"
        elif avg_change > VOL_HIGH_THRESHOLD:
            return "HIGH"
        return "MEDIUM"


# ============================================================
# ORDER TRACKER (kept from V12.1)
# ============================================================

class OrderTracker:
    def __init__(self):
        self._orders: Dict[str, Dict[str, Optional[Dict]]] = {}

    def _key(self, side: str, layer: int = 1) -> str:
        return f"{side}_L{layer}"

    def record_order(self, ticker: str, side: str, order_id: int,
                     price: float, size: int, layer: int = 1):
        if ticker not in self._orders:
            self._orders[ticker] = {}
        self._orders[ticker][self._key(side, layer)] = {
            "id": order_id, "price": price, "size": size}

    def get_order(self, ticker: str, side: str, layer: int = 1) -> Optional[Dict]:
        return self._orders.get(ticker, {}).get(self._key(side, layer))

    def clear_ticker(self, ticker: str):
        self._orders[ticker] = {}

    def clear_all(self):
        for t in list(self._orders.keys()):
            self._orders[t] = {}

    def pending_exposure(self, ticker: str, side: str) -> int:
        """Sum of all pending order sizes for this ticker+side across layers."""
        total = 0
        orders = self._orders.get(ticker, {})
        for key, order in orders.items():
            if order and side in key:
                total += order.get("size", 0)
        return total

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
# MARKET MAKER V13
# ============================================================

class MarketMaker:
    def __init__(self, api: RITApi):
        self.api = api
        self.trend_tracker = TrendTracker()
        self.vol_tracker = VolatilityTracker()
        self.order_tracker = OrderTracker()

        self.positions: Dict[str, int] = {t: 0 for t in TICKERS}
        self.mid_prices: Dict[str, float] = {}
        self.book_imbalance: Dict[str, float] = {t: 0.0 for t in TICKERS}
        self.market_spread: Dict[str, float] = {t: 0.10 for t in TICKERS}

        # Limits
        self.gross_limit = DEFAULT_GROSS_LIMIT
        self.close_limit = CLOSE_TIME_LIMIT
        self.net_limit = DEFAULT_NET_LIMIT
        self.per_stock_limit: Dict[str, int] = {}
        self._compute_per_stock_limits()

        # P&L
        self.start_nlv = 0.0
        self.current_pnl = 0.0
        self.circuit_breaker_active = False
        self.circuit_breaker_halt = False

        # Adaptive
        self.adaptive_mult: Dict[str, float] = {t: 1.0 for t in TICKERS}
        self.fill_count: Dict[str, int] = {t: 0 for t in TICKERS}
        self.last_adaptive_tick = 0

        # Stats
        self.orders_placed = 0
        self.orders_cancelled = 0
        self.total_volume_traded = 0
        self.market_orders_sent = 0

        self.in_lockdown = False

    def _compute_per_stock_limits(self):
        """Compute per-stock limits. V13: opportunity-weighted, capped at 5k."""
        # Weight by a blend of rebate and spread opportunity
        # Higher spread = more opportunity
        weights = {
            "WNTR": 1.0,   # Narrow spreads
            "SMMR": 1.0,   # Narrow spreads
            "ATMN": 1.2,   # Medium spreads, good range
            "SPNG": 1.4,   # Widest spreads, biggest range
        }
        total_w = sum(weights[t] for t in TICKERS)
        for t in TICKERS:
            fraction = weights[t] / total_w
            allocated = int(self.gross_limit * PER_STOCK_LIMIT_FRACTION * fraction / 0.25)
            allocated = max(MIN_PER_STOCK_LIMIT, min(allocated, MAX_PER_STOCK_LIMIT))
            self.per_stock_limit[t] = allocated

    # ============================================================
    # STATE UPDATES
    # ============================================================

    def update_state(self, tick: int):
        securities = self.api.get_securities()
        if not securities:
            return
        for sec in securities:
            t = sec.get("ticker", "")
            if t not in TICKERS:
                continue
            old_pos = self.positions.get(t, 0)
            self.positions[t] = sec.get("position", 0)
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
                self.trend_tracker.update(t, mid)
                self.market_spread[t] = ask - bid

    def update_limits(self):
        limits = self.api.get_limits()
        if not limits:
            return
        for lim in limits:
            gl = lim.get("gross_limit", 0)
            nl = lim.get("net_limit", 0)
            if gl and isinstance(gl, (int, float)) and gl > 0:
                current_gl = int(gl)
                if current_gl < 20000:
                    self.close_limit = current_gl
                else:
                    self.gross_limit = current_gl
            if nl and isinstance(nl, (int, float)) and nl > 0:
                self.net_limit = int(nl)
        self._compute_per_stock_limits()

    def update_pnl(self):
        nlv = self.api.get_nlv()
        if nlv is not None and self.start_nlv > 0:
            self.current_pnl = nlv - self.start_nlv
        if self.current_pnl <= CIRCUIT_BREAKER_HALT:
            if not self.circuit_breaker_halt:
                print(f"    [CB HALT] PnL=${self.current_pnl:,.2f}")
                self.circuit_breaker_halt = True
                self.api.cancel_all_orders()
                self.order_tracker.clear_all()
        elif self.current_pnl <= CIRCUIT_BREAKER_CAUTION:
            if not self.circuit_breaker_active:
                print(f"    [CB CAUTION] PnL=${self.current_pnl:,.2f}")
                self.circuit_breaker_active = True
            if self.circuit_breaker_halt:
                print(f"    [CB HALT->CAUTION] PnL recovered to ${self.current_pnl:,.2f}")
                self.circuit_breaker_halt = False
        else:
            if self.circuit_breaker_active:
                self.circuit_breaker_active = False
            if self.circuit_breaker_halt:
                print(f"    [CB HALT->CLEAR] PnL recovered to ${self.current_pnl:,.2f}")
                self.circuit_breaker_halt = False

    def update_adaptive_spreads(self, tick: int):
        if tick - self.last_adaptive_tick < ADAPTIVE_INTERVAL:
            return
        for ticker in TICKERS:
            fills = self.fill_count.get(ticker, 0)
            if fills < 3:
                self.adaptive_mult[ticker] *= 0.95
            elif fills > ADAPTIVE_TARGET_FILLS * 2:
                self.adaptive_mult[ticker] *= 1.08
            elif fills > ADAPTIVE_TARGET_FILLS:
                self.adaptive_mult[ticker] *= 1.02
            self.adaptive_mult[ticker] = max(
                ADAPTIVE_MIN_MULT, min(ADAPTIVE_MAX_MULT, self.adaptive_mult[ticker]))
            self.fill_count[ticker] = 0
        self.last_adaptive_tick = tick

    # ============================================================
    # POSITION CALCULATIONS
    # ============================================================

    def compute_aggregate(self) -> int:
        return sum(abs(p) for p in self.positions.values())

    def compute_utilization(self) -> float:
        if self.gross_limit <= 0:
            return 0.0
        return self.compute_aggregate() / self.gross_limit

    def compute_net(self) -> int:
        return abs(sum(self.positions.values()))

    def compute_book_imbalance(self, book: Dict) -> float:
        bids = book.get("bids", book.get("bid", []))
        asks = book.get("asks", book.get("ask", []))
        bid_vol = sum(o.get("quantity", 0) - o.get("quantity_filled", 0)
                      for o in (bids or [])[:5])
        ask_vol = sum(o.get("quantity", 0) - o.get("quantity_filled", 0)
                      for o in (asks or [])[:5])
        total = bid_vol + ask_vol
        if total < 100:
            return 0.0
        return (bid_vol - ask_vol) / total

    # ============================================================
    # SPREAD COMPUTATION - V13: MARKET-ADAPTIVE + VOLATILITY-AWARE
    # ============================================================

    def compute_spread(self, ticker: str, second_in_day: int, layer: int = 1) -> float:
        """Compute spread: simple, competitive, no compounding.

        V14: max(base, market*factor) × vol_mult × time_mult only.
        Removed 5 multiplicative factors that compounded to 3-5x wider than intended.
        """
        mkt_spread = self.market_spread.get(ticker, 0.05)
        base = BASE_SPREAD.get(ticker, 0.01)

        # Core spread: competitive with market
        spread = max(base, mkt_spread * SPREAD_MARKET_FACTOR)

        # Layer multipliers
        if layer == 2:
            spread *= LAYER2_SPREAD_MULT
        elif layer == 3:
            spread *= LAYER3_SPREAD_MULT

        # Only two modifiers: volatility regime and time
        regime = self.vol_tracker.get_regime(ticker)
        vol_mult = VOL_SPREAD_MULT.get(regime, 1.0)
        spread *= vol_mult

        # Time: widen near close, post-recovery
        if second_in_day < POST_CLOSE_RECOVERY_SEC:
            spread *= POST_CLOSE_SPREAD_MULT
        elif second_in_day >= PRE_CLOSE_WIDEN_SEC:
            spread *= PRE_CLOSE_SPREAD_MULT

        return max(SPREAD_MIN_ABSOLUTE, min(SPREAD_MAX_ABSOLUTE, round(spread, 4)))

    # ============================================================
    # ORDER SIZING - V13: TREND-AWARE + MOMENTUM BOOST
    # ============================================================

    def compute_order_size(self, ticker: str, side: str, utilization: float,
                           second_in_day: int, layer: int = 1) -> int:
        base_size = ORDER_SIZE.get(ticker, 2500)
        pos = self.positions.get(ticker, 0)
        per_stock_limit = self.per_stock_limit.get(ticker, MIN_PER_STOCK_LIMIT)

        # Layer sizing
        if layer == 2:
            layer_mult = LAYER2_SIZE_MULT
        elif layer == 3:
            layer_mult = LAYER3_SIZE_MULT
        else:
            layer_mult = 1.0

        # ============================================================
        # ASYMMETRIC SIZING (V13: trend + inventory combined)
        # ============================================================
        asym_mult = 1.0
        asym_threshold = per_stock_limit * ASYM_KICK_IN

        # Inventory-based asymmetry (reduce toward flat)
        if abs(pos) > asym_threshold:
            pos_frac = min(abs(pos) / per_stock_limit, 1.0)
            if (pos > 0 and side == "SELL") or (pos < 0 and side == "BUY"):
                # Reducing side: boost
                asym_mult = 1.0 + pos_frac * (ASYM_REDUCE_MAX - 1.0)
            elif (pos > 0 and side == "BUY") or (pos < 0 and side == "SELL"):
                # Increasing side: restrict
                asym_mult = max(ASYM_INCREASE_MIN, 1.0 - pos_frac * (1.0 - ASYM_INCREASE_MIN))

        # Trend-based sizing: boost orders aligned with trend
        trend = self.trend_tracker.get_trend(ticker)
        if abs(trend) > TREND_MIN_SIGNAL:
            if self.trend_tracker.get_trend_direction_aligned(ticker, side):
                # Trend-aligned: mild boost (let skew do the positioning)
                asym_mult *= (1.0 + abs(trend) * 0.3)
            else:
                # Counter-trend: reduce size
                asym_mult *= max(0.4, 1.0 - abs(trend) * 0.4)

        # Momentum boost in first MOMENTUM_DURATION_SEC seconds
        momentum = abs(self.trend_tracker.get_momentum(ticker))
        if momentum > 0.1 and second_in_day < MOMENTUM_DURATION_SEC:
            mom_signal = self.trend_tracker.get_momentum(ticker)
            if (mom_signal > 0 and side == "BUY") or (mom_signal < 0 and side == "SELL"):
                asym_mult *= MOMENTUM_SIZE_BOOST

        # Volatility regime
        regime = self.vol_tracker.get_regime(ticker)
        vol_mult = VOL_SIZE_MULT.get(regime, 1.0)

        # Utilization reduction
        if utilization > UTIL_PANIC:
            util_mult = 0.0
        elif utilization > UTIL_EMERGENCY:
            util_mult = 0.15
        elif utilization > UTIL_REDUCE:
            util_mult = 0.4
        elif utilization > UTIL_SKEW:
            util_mult = 0.7
        else:
            util_mult = 1.0

        # Time-based
        time_mult = 1.0
        if second_in_day < POST_CLOSE_RECOVERY_SEC:
            time_mult = POST_CLOSE_SIZE_MULT
        elif second_in_day >= PRE_CLOSE_WIDEN_SEC:
            time_mult = PRE_CLOSE_SIZE_MULT

        cb_mult = 0.3 if self.circuit_breaker_active else 1.0

        size = int(base_size * layer_mult * asym_mult *
                   vol_mult * util_mult * time_mult * cb_mult)

        # ============================================================
        # HARD CAPS: enforce per-stock limit strictly (including pending orders)
        # ============================================================
        pending = self.order_tracker.pending_exposure(ticker, side)
        if (pos > 0 and side == "BUY") or (pos < 0 and side == "SELL"):
            room = max(0, per_stock_limit - abs(pos) - pending)
            size = min(size, room)
        elif pos == 0:
            room = max(0, per_stock_limit - pending)
            size = min(size, room)

        # Gross limit room
        gross = self.compute_aggregate()
        gross_room = max(0, int(self.gross_limit * GROSS_LIMIT_BUFFER) - gross)
        if (pos > 0 and side == "BUY") or (pos < 0 and side == "SELL") or pos == 0:
            size = min(size, gross_room // max(1, NUM_LAYERS))

        return max(0, min(size, MAX_ORDER_SIZE))

    # ============================================================
    # INVENTORY SKEW - V13: THREE COMPONENTS
    # ============================================================

    def compute_skew(self, ticker: str, utilization: float,
                     second_in_day: int) -> float:
        """Compute total quote skew from 3 independent signals.

        Component 1: INVENTORY SKEW - push quotes away from position to flatten
        Component 2: TREND SKEW - push quotes toward detected trend
        Component 3: MOMENTUM SKEW - push toward day-open price jump direction

        The skew shifts the effective mid price, causing one side's orders
        to be more aggressive (closer to true mid) and the other less aggressive.
        """
        pos = self.positions.get(ticker, 0)
        per_stock_limit = self.per_stock_limit.get(ticker, MIN_PER_STOCK_LIMIT)

        # ---- Component 1: Inventory Skew (always active) ----
        # Negative position → negative skew → lower effective mid → sell less aggressive
        if per_stock_limit > 0:
            normalized_pos = pos / per_stock_limit  # -1 to +1
        else:
            normalized_pos = 0.0
        inv_skew = -normalized_pos * SKEW_FACTOR

        # Scale up at high utilization
        if utilization > UTIL_REDUCE:
            inv_skew *= 2.5
        elif utilization > UTIL_SKEW:
            inv_skew *= 1.5

        # ---- Component 2: Trend Skew ----
        trend = self.trend_tracker.get_trend(ticker)
        trend_skew = 0.0
        if abs(trend) > TREND_MIN_SIGNAL:
            # Positive trend → positive skew → raise effective mid →
            # bid is higher (more aggressive buy), ask is higher (less aggressive sell)
            # → accumulate longs in uptrend
            trend_skew = trend * TREND_SKEW_FACTOR

            # Reduce trend skew if already loaded in trend direction
            if (trend > 0 and pos > per_stock_limit * 0.5) or \
               (trend < 0 and pos < -per_stock_limit * 0.5):
                trend_skew *= 0.3  # Don't pile on if already heavily positioned

        # ---- Component 3: Momentum Skew (day-open only) ----
        mom_skew = 0.0
        if second_in_day < MOMENTUM_DURATION_SEC:
            momentum = self.trend_tracker.get_momentum(ticker)
            if abs(momentum) > 0.05:
                mom_skew = momentum * MOMENTUM_SKEW_FACTOR

                # Reduce if already positioned in momentum direction
                if (momentum > 0 and pos > per_stock_limit * 0.4) or \
                   (momentum < 0 and pos < -per_stock_limit * 0.4):
                    mom_skew *= 0.2

        # ---- Component 4: Book Imbalance ----
        imbalance = self.book_imbalance.get(ticker, 0.0)
        imb_skew = 0.0
        if abs(imbalance) > IMBALANCE_THRESHOLD:
            imb_skew = imbalance * IMBALANCE_SKEW_FACTOR

        # ---- Combine ----
        total_skew = inv_skew + trend_skew + mom_skew + imb_skew

        return round(total_skew, 4)

    # ============================================================
    # QUOTING DECISIONS
    # ============================================================

    def should_quote_side(self, ticker: str, side: str, utilization: float) -> bool:
        pos = self.positions.get(ticker, 0)
        per_stock_limit = self.per_stock_limit.get(ticker, MIN_PER_STOCK_LIMIT)

        if self.circuit_breaker_halt or self.in_lockdown:
            return False

        # V13: STRICT per-stock limit enforcement
        if side == "BUY" and pos >= per_stock_limit:
            return False
        if side == "SELL" and pos <= -per_stock_limit:
            return False

        # At high utilization, only reducing side
        if utilization > UTIL_EMERGENCY:
            if (pos > 0 and side == "BUY") or (pos < 0 and side == "SELL"):
                return False
            if pos == 0:
                return False

        # Gross limit check
        gross = self.compute_aggregate()
        if gross >= int(self.gross_limit * GROSS_LIMIT_BUFFER):
            if (pos > 0 and side == "BUY") or (pos < 0 and side == "SELL") or pos == 0:
                return False

        # Net limit check
        net = self.compute_net()
        if net >= int(self.net_limit * NET_LIMIT_BUFFER):
            net_dir = sum(self.positions.values())
            if (net_dir > 0 and side == "BUY") or (net_dir < 0 and side == "SELL"):
                return False

        return True

    # ============================================================
    # MAIN QUOTING ENGINE - V13 WITH 3 LAYERS + TREND AWARENESS
    # ============================================================

    def quote_ticker(self, ticker: str, tick: int, second_in_day: int,
                     utilization: float):
        mid = self.mid_prices.get(ticker, 25.0)
        if mid < MIN_PRICE or mid > MAX_PRICE:
            return

        skew = self.compute_skew(ticker, utilization, second_in_day)
        quote_buy = self.should_quote_side(ticker, "BUY", utilization)
        quote_sell = self.should_quote_side(ticker, "SELL", utilization)

        if not quote_buy and not quote_sell:
            if self.order_tracker.has_any_order(ticker):
                self.api.cancel_ticker_orders(ticker)
                self.order_tracker.clear_ticker(ticker)
            return

        # Get fresh book data
        book = self.api.get_book(ticker, limit=5)
        best_bid = self.api.get_best_bid(book)
        best_ask = self.api.get_best_ask(book)
        if best_bid > 0 and best_ask > 0:
            self.book_imbalance[ticker] = self.compute_book_imbalance(book)
            self.market_spread[ticker] = best_ask - best_bid
            mid = (best_bid + best_ask) / 2.0

        # Compute all layers
        layers = []
        for layer_num in range(1, NUM_LAYERS + 1):
            if layer_num == 3 and (second_in_day < POST_CLOSE_RECOVERY_SEC or
                                    second_in_day >= PRE_CLOSE_WIDEN_SEC):
                continue
            if layer_num == 2 and second_in_day >= PRE_CLOSE_REDUCE_SEC:
                continue

            spread = self.compute_spread(ticker, second_in_day, layer=layer_num)
            half = spread / 2.0
            buy_size = self.compute_order_size(
                ticker, "BUY", utilization, second_in_day, layer=layer_num) if quote_buy else 0
            sell_size = self.compute_order_size(
                ticker, "SELL", utilization, second_in_day, layer=layer_num) if quote_sell else 0

            # Apply skew to effective mid
            effective_mid = mid + skew
            bid = round(effective_mid - half, 2)
            ask = round(effective_mid + half, 2)

            # Safety: bid must be below ask
            if bid >= ask:
                bid = round(mid - 0.02, 2)
                ask = round(mid + 0.02, 2)
            bid = max(MIN_PRICE, min(bid, MAX_PRICE - 0.02))
            ask = max(bid + 0.01, min(ask, MAX_PRICE))

            # Ensure outer layers are outside inner layers
            if layers:
                inner_bid = layers[-1]['bid']
                inner_ask = layers[-1]['ask']
                if bid >= inner_bid:
                    bid = round(inner_bid - 0.02, 2)
                if ask <= inner_ask:
                    ask = round(inner_ask + 0.02, 2)
                bid = max(MIN_PRICE, bid)
                ask = min(MAX_PRICE, ask)

            layers.append({
                'layer': layer_num, 'bid': bid, 'ask': ask,
                'buy_size': buy_size, 'sell_size': sell_size, 'half': half
            })

        # Check if requoting needed
        need_cancel = False
        if layers:
            tolerance = layers[0]['half'] * REQUOTE_THRESHOLD
            for ldata in layers:
                ln = ldata['layer']
                tol = tolerance * ln
                for side, target, size in [("BUY", ldata['bid'], ldata['buy_size']),
                                            ("SELL", ldata['ask'], ldata['sell_size'])]:
                    if size >= 100:
                        if not self.order_tracker.order_is_close_enough(
                                ticker, side, target, tol, layer=ln):
                            need_cancel = True
                            break
                if need_cancel:
                    break

        if need_cancel or not self.order_tracker.has_any_order(ticker):
            self.api.cancel_ticker_orders(ticker)
            self.order_tracker.clear_ticker(ticker)
            self.orders_cancelled += 1

            for ldata in layers:
                ln = ldata['layer']
                if quote_buy and ldata['buy_size'] >= 100:
                    result = self.api.submit_limit_order(
                        ticker, ldata['buy_size'], "BUY", ldata['bid'])
                    if result and isinstance(result, dict):
                        self.order_tracker.record_order(
                            ticker, "BUY", result.get("order_id", 0),
                            ldata['bid'], ldata['buy_size'], layer=ln)
                        self.orders_placed += 1

                if quote_sell and ldata['sell_size'] >= 100:
                    result = self.api.submit_limit_order(
                        ticker, ldata['sell_size'], "SELL", ldata['ask'])
                    if result and isinstance(result, dict):
                        self.order_tracker.record_order(
                            ticker, "SELL", result.get("order_id", 0),
                            ldata['ask'], ldata['sell_size'], layer=ln)
                        self.orders_placed += 1

    # ============================================================
    # PROACTIVE POSITION REDUCTION - V13: MORE AGGRESSIVE
    # ============================================================

    def reduce_large_positions(self, tick: int):
        for ticker in TICKERS:
            pos = self.positions.get(ticker, 0)
            per_stock_limit = self.per_stock_limit.get(ticker, MIN_PER_STOCK_LIMIT)
            threshold = int(per_stock_limit * 0.60)  # V13: start earlier (was 0.70)
            if abs(pos) < threshold:
                continue

            mid = self.mid_prices.get(ticker, 25.0)
            excess = abs(pos) - threshold

            if abs(pos) > per_stock_limit * 0.85:
                # Very close to limit: use market orders
                mkt_size = min(excess, int(per_stock_limit * 0.25))
                mkt_size = max(100, min(mkt_size, MAX_ORDER_SIZE))
                side = "SELL" if pos > 0 else "BUY"
                self.api.submit_market_order(ticker, mkt_size, side)
                self.market_orders_sent += 1
            else:
                # Aggressive limit order near mid
                reduce_size = min(excess, int(per_stock_limit * 0.25))
                reduce_size = max(100, min(reduce_size, MAX_ORDER_SIZE))
                if pos > 0:
                    price = round(mid - 0.01, 2)  # Sell aggressively
                    self.api.submit_limit_order(ticker, reduce_size, "SELL", price)
                else:
                    price = round(mid + 0.01, 2)
                    self.api.submit_limit_order(ticker, reduce_size, "BUY", price)

    # ============================================================
    # MARKET CLOSE PROTOCOL (kept from V12.1)
    # ============================================================

    def pre_close_handler(self, tick: int, second_in_day: int):
        aggregate = self.compute_aggregate()
        close_target = int(self.close_limit * CLOSE_TARGET_UTILIZATION)

        if second_in_day >= PRE_CLOSE_FLATTEN_SEC:
            if aggregate > close_target:
                sorted_positions = sorted(
                    [(t, self.positions[t]) for t in TICKERS if abs(self.positions[t]) > 0],
                    key=lambda x: abs(x[1]), reverse=True)
                remaining = aggregate - close_target
                for ticker, pos in sorted_positions:
                    if remaining <= 0:
                        break
                    to_reduce = min(abs(pos), remaining, MAX_ORDER_SIZE)
                    to_reduce = max(100, to_reduce)
                    side = "SELL" if pos > 0 else "BUY"
                    self.api.submit_market_order(ticker, to_reduce, side)
                    self.market_orders_sent += 1
                    remaining -= to_reduce
                    print(f"    [FLATTEN] {ticker}: MKT {side} {to_reduce} "
                          f"(pos={pos}, agg={aggregate}, target={close_target})")

        elif second_in_day >= PRE_CLOSE_CANCEL_SEC:
            if not self.in_lockdown:
                self.api.cancel_all_orders()
                self.order_tracker.clear_all()
                self.in_lockdown = True
                print(f"    [LOCKDOWN] T{tick} sec={second_in_day} "
                      f"agg={aggregate} close_lim={self.close_limit}")

        elif second_in_day >= PRE_CLOSE_REDUCE_SEC:
            for ticker in TICKERS:
                pos = self.positions.get(ticker, 0)
                if abs(pos) < 200:
                    continue
                mid = self.mid_prices.get(ticker, 25.0)
                reduce_size = min(abs(pos), 5000)
                reduce_size = max(100, min(reduce_size, MAX_ORDER_SIZE))
                if pos > 0:
                    price = round(mid - 0.01, 2)
                    self.api.submit_limit_order(ticker, reduce_size, "SELL", price)
                else:
                    price = round(mid + 0.01, 2)
                    self.api.submit_limit_order(ticker, reduce_size, "BUY", price)

    # ============================================================
    # EMERGENCY HANDLERS
    # ============================================================

    def emergency_flatten(self, target_util: float = 0.70):
        current = self.compute_aggregate()
        target = int(self.gross_limit * target_util)
        if current <= target:
            return
        self.api.cancel_all_orders()
        self.order_tracker.clear_all()
        excess = current - target
        sorted_positions = sorted(
            [(t, self.positions[t]) for t in TICKERS if abs(self.positions[t]) > 0],
            key=lambda x: abs(x[1]), reverse=True)
        remaining = excess
        for ticker, pos in sorted_positions:
            if remaining <= 0:
                break
            to_reduce = min(abs(pos), remaining, MAX_ORDER_SIZE)
            to_reduce = max(100, to_reduce)
            side = "SELL" if pos > 0 else "BUY"
            self.api.submit_market_order(ticker, to_reduce, side)
            self.market_orders_sent += 1
            remaining -= to_reduce

    def panic_flatten(self):
        self.api.cancel_all_orders()
        self.order_tracker.clear_all()
        for ticker in TICKERS:
            pos = self.positions.get(ticker, 0)
            if abs(pos) >= 100:
                side = "SELL" if pos > 0 else "BUY"
                qty = min(abs(pos), MAX_ORDER_SIZE)
                self.api.submit_market_order(ticker, qty, side)
                self.market_orders_sent += 1

    # ============================================================
    # MAIN LOOP - V13: WITH DAY BOUNDARY HANDLING
    # ============================================================

    def run(self):
        print(BANNER.format(
            psl=MAX_PER_STOCK_LIMIT, gl=self.gross_limit, cl=self.close_limit,
            cycle=int(CYCLE_SLEEP * 1000), layers=NUM_LAYERS,
            skew=SKEW_FACTOR, url=API_BASE_URL, key=API_KEY))

        self.update_limits()
        print(f"  Intraday gross limit: {self.gross_limit:,}")
        print(f"  Close-time limit: {self.close_limit:,}")
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
                        agg = self.compute_aggregate()
                        print(f"  Final P&L: ${self.current_pnl:,.2f}")
                        print(f"  Orders: +{self.orders_placed}/-{self.orders_cancelled}")
                        print(f"  Market orders: {self.market_orders_sent}")
                        print(f"  Volume: {self.total_volume_traded:,}")
                        print(f"  Final aggregate: {agg:,}")
                        for t in TICKERS:
                            print(f"    {t}: {self.positions.get(t, 0):+,}")
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

                # ============================================================
                # DAY BOUNDARY HANDLING - V13 KEY FEATURE
                # ============================================================
                if current_day != last_day:
                    if last_day >= 0:
                        # Store closes from previous day
                        self.trend_tracker.on_day_end(self.mid_prices.copy())
                        print(f"\n  === DAY {current_day + 1} (tick {tick}) ===")

                    # Get fresh prices for new day
                    self.update_state(tick)

                    # Initialize trend tracker for new day with momentum detection
                    self.trend_tracker.on_new_day(current_day, self.mid_prices.copy())

                    # Log momentum signals
                    for t in TICKERS:
                        mom = self.trend_tracker.get_momentum(t)
                        if abs(mom) > 0.05:
                            direction = "UP" if mom > 0 else "DOWN"
                            print(f"    [MOMENTUM] {t}: {direction} "
                                  f"(signal={mom:+.3f})")

                    self.in_lockdown = False
                    last_day = current_day

                # Circuit breaker halt: re-check P&L periodically
                if self.circuit_breaker_halt:
                    self.update_pnl()
                    time.sleep(0.5)
                    continue

                # ============================================================
                # STATE UPDATES (every tick)
                # ============================================================
                self.update_state(tick)
                if tick % 5 == 0:
                    self.update_limits()
                if tick % 3 == 0:
                    self.update_pnl()
                self.update_adaptive_spreads(tick)

                # Decay momentum signal
                self.trend_tracker.decay_momentum()

                utilization = self.compute_utilization()
                aggregate = self.compute_aggregate()
                net = self.compute_net()

                # ============================================================
                # PRE-CLOSE PHASE
                # ============================================================
                if second_in_day >= PRE_CLOSE_WIDEN_SEC:
                    self.pre_close_handler(tick, second_in_day)
                    time.sleep(CYCLE_SLEEP)
                    continue

                # ============================================================
                # POST-CLOSE RECOVERY (just 1 second)
                # ============================================================
                if second_in_day < POST_CLOSE_RECOVERY_SEC:
                    if self.order_tracker.has_any_order(TICKERS[0]):
                        self.api.cancel_all_orders()
                        self.order_tracker.clear_all()
                    time.sleep(CYCLE_SLEEP)
                    continue

                # ============================================================
                # ACTIVE TRADING
                # ============================================================
                self.in_lockdown = False

                # Emergency position management
                if utilization > UTIL_PANIC:
                    self.panic_flatten()
                    time.sleep(CYCLE_SLEEP)
                    continue
                elif utilization > UTIL_EMERGENCY:
                    self.emergency_flatten(target_util=UTIL_REDUCE)

                # Proactive position reduction (every other tick for speed)
                if tick % 2 == 0:
                    self.reduce_large_positions(tick)

                # Quote all tickers
                for ticker in TICKERS:
                    self.quote_ticker(ticker, tick, second_in_day, utilization)

                # ============================================================
                # LOGGING
                # ============================================================
                if tick - last_log_tick >= LOG_INTERVAL_TICKS:
                    pos_str = " | ".join(
                        f"{t}:{int(self.positions.get(t, 0)):+d}" for t in TICKERS)
                    pnl_str = f"${self.current_pnl:+,.0f}" if self.start_nlv > 0 else "N/A"

                    # V13: show trend signals
                    trend_str = " ".join(
                        f"{t}:{self.trend_tracker.get_trend(t):+.2f}"
                        for t in TICKERS)

                    print(f"  T{tick:03d} d{second_in_day:02d} | "
                          f"pos=[{pos_str}] | "
                          f"agg={aggregate:,}/{self.gross_limit:,}({utilization:.0%}) | "
                          f"pnl={pnl_str} | "
                          f"trend=[{trend_str}] | "
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
    fh = logging.FileHandler(os.path.join(log_dir, f"mm_v13_{ts}.log"))
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