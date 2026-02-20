# -*- coding: utf-8 -*-
"""
ALGO MARKET MAKING BOT - V15 AVELLANEDA-STOIKOV MARKET MAKER
==============================================================

CORE IDEA: Use the Avellaneda-Stoikov (2008) optimal market making framework
to set mathematically optimal quotes instead of ad-hoc parameters.

THE AS MODEL IN BRIEF:
  - Reservation price: r = s - q * gamma * sigma^2 * (T - t)
    → Shifts your "fair value" based on inventory (q), risk aversion (gamma),
      volatility (sigma), and time remaining (T-t)
    → If you're long, your reservation price is BELOW mid → you sell more aggressively

  - Optimal spread: delta* = gamma * sigma^2 * (T-t) + (2/gamma) * ln(1 + gamma/k)
    → First term: risk premium (increases with vol and time remaining)
    → Second term: market power (depends on order arrival intensity k)

  - Fill intensity: Lambda(delta) = A * exp(-k * delta)
    → Probability of fill decreases exponentially with distance from mid

KEY ADAPTATIONS FOR THIS COMPETITION:
  1. gamma is time-varying: low during active trading (more volume), high near close
  2. sigma is estimated in real-time from recent price moves
  3. T-t resets each "day" (60 seconds) since close penalty is per-day
  4. Jump detection: widen spreads on news-driven price jumps
  5. Aggressive volume: top teams profit from spread × volume + rebates × volume

WHERE DOES $240k COME FROM?
  - Assume avg spread capture of ~2c/share on both sides = 1c/share net
  - Need ~24M shares over 300 seconds = 80k shares/second across 4 stocks
  - That's 20k shares/sec per stock, or filling a 5000-share order every 0.25 sec
  - Plus rebates: 24M × $0.018 avg = $432k from rebates alone
  - Minus adverse selection and penalties
  - Net: $200-300k is achievable with high-volume MM + low adverse selection
"""

import sys
import os
import time
import math
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
    SPREAD_MIN_PER_TICKER, SPREAD_MIN_ABSOLUTE, SPREAD_MAX_ABSOLUTE,
    SPREAD_INSIDE_FACTOR,
    BASE_ORDER_SIZE, MAX_ORDER_SIZE,
    VOL_SIZE_MULT,
    AS_GAMMA_NORMAL, AS_GAMMA_HIGH_INV, AS_GAMMA_PRE_CLOSE,
    AS_K_PARAMETER, AS_VOL_WINDOW, AS_VOL_DEFAULT,
    JUMP_THRESHOLD, JUMP_WIDEN_MULT, JUMP_DECAY_RATE, JUMP_MIN_SIGNAL,
    UTIL_NORMAL, UTIL_SKEW, UTIL_REDUCE, UTIL_EMERGENCY, UTIL_PANIC,
    DEFAULT_GROSS_LIMIT, DEFAULT_NET_LIMIT, CLOSE_TIME_LIMIT,
    GROSS_LIMIT_BUFFER, NET_LIMIT_BUFFER,
    PER_STOCK_TRADING_LIMIT, PER_STOCK_CLOSE_LIMIT,
    CYCLE_SLEEP, HEAT_DURATION, DAY_LENGTH,
    PRE_CLOSE_WIDEN_SEC, PRE_CLOSE_CANCEL_SEC, PRE_CLOSE_FLATTEN_SEC,
    CLOSE_TARGET_UTILIZATION,
    POST_CLOSE_RECOVERY_SEC, POST_CLOSE_SPREAD_MULT, POST_CLOSE_SIZE_MULT,
    VOL_LOOKBACK, VOL_LOW_THRESHOLD, VOL_HIGH_THRESHOLD, VOL_SPREAD_MULT,
    MIN_PRICE, MAX_PRICE,
    LOG_INTERVAL_TICKS, REQUOTE_THRESHOLD,
    IMBALANCE_THRESHOLD, IMBALANCE_SKEW_FACTOR,
    CIRCUIT_BREAKER_CAUTION, CIRCUIT_BREAKER_HALT,
    ADAPTIVE_INTERVAL, ADAPTIVE_MIN_MULT, ADAPTIVE_MAX_MULT,
    ADAPTIVE_TARGET_FILLS,
    ENABLE_LAYERED_QUOTES, NUM_LAYERS, LAYER_CONFIG,
    ASYM_REDUCE_BOOST, ASYM_INCREASE_FLOOR, ASYM_KICK_IN_FRAC,
    API_BASE_URL, API_KEY,
)
from api import RITApi

logger = logging.getLogger(__name__)

BANNER = """
================================================================================
  ALGO MARKET MAKING BOT V15 - AVELLANEDA-STOIKOV ENGINE
  Optimal quotes | High volume | AS skew | Jump detection
  Gross: {gl:,} | Close: {cl:,} | Per-stock: {psl:,}
  Cycle: {cycle}ms | Layers: {layers} | gamma={gamma}
  {url} | Key: {key}
================================================================================
"""


# ============================================================
# VOLATILITY ESTIMATOR - Real-time sigma^2 for AS formula
# ============================================================

class VolatilityEstimator:
    """Estimates per-tick volatility (sigma) for each ticker.

    Uses rolling window of mid-price returns to compute sigma.
    This feeds directly into the AS optimal spread and skew formulas.
    """

    def __init__(self, window: int = AS_VOL_WINDOW):
        self.window = window
        self._prices: Dict[str, deque] = {}
        self._vol_cache: Dict[str, float] = {}
        self._regime_cache: Dict[str, str] = {}

    def update(self, ticker: str, mid: float):
        if mid <= 0:
            return
        if ticker not in self._prices:
            self._prices[ticker] = deque(maxlen=200)
        self._prices[ticker].append(mid)
        self._recompute(ticker)

    def _recompute(self, ticker: str):
        prices = self._prices.get(ticker, deque())
        if len(prices) < 3:
            self._vol_cache[ticker] = AS_VOL_DEFAULT
            self._regime_cache[ticker] = "MEDIUM"
            return

        recent = list(prices)[-self.window:]
        if len(recent) < 2:
            self._vol_cache[ticker] = AS_VOL_DEFAULT
            return

        # Compute absolute returns
        returns = [abs(recent[i] - recent[i-1]) for i in range(1, len(recent))]
        avg_return = sum(returns) / len(returns) if returns else AS_VOL_DEFAULT

        # sigma per tick (standard deviation of price changes)
        if len(returns) >= 2:
            mean_r = sum(returns) / len(returns)
            variance = sum((r - mean_r)**2 for r in returns) / len(returns)
            sigma = math.sqrt(variance) if variance > 0 else avg_return
        else:
            sigma = avg_return

        # Floor and cap
        sigma = max(0.005, min(0.20, sigma))
        self._vol_cache[ticker] = sigma

        # Regime classification
        if avg_return < VOL_LOW_THRESHOLD:
            self._regime_cache[ticker] = "LOW"
        elif avg_return > VOL_HIGH_THRESHOLD:
            self._regime_cache[ticker] = "HIGH"
        else:
            self._regime_cache[ticker] = "MEDIUM"

    def get_sigma(self, ticker: str) -> float:
        """Get sigma (per-tick volatility) for the AS formula."""
        return self._vol_cache.get(ticker, AS_VOL_DEFAULT)

    def get_sigma_squared(self, ticker: str) -> float:
        """Get sigma^2 for direct use in AS formulas."""
        s = self.get_sigma(ticker)
        return s * s

    def get_regime(self, ticker: str) -> str:
        return self._regime_cache.get(ticker, "MEDIUM")


# ============================================================
# JUMP DETECTOR - Detect news-driven price jumps
# ============================================================

class JumpDetector:
    """Detects sudden price jumps (likely from news between days).

    When a jump is detected, we temporarily widen spreads to avoid
    adverse selection, then gradually decay back to normal.
    """

    def __init__(self):
        self._last_price: Dict[str, float] = {}
        self._jump_signal: Dict[str, float] = {}  # Current widening multiplier

    def update(self, ticker: str, mid: float):
        if mid <= 0:
            return

        last = self._last_price.get(ticker, 0)
        if last > 0:
            jump = abs(mid - last)
            if jump > JUMP_THRESHOLD:
                # Big jump detected! Set widening signal
                signal = min(JUMP_WIDEN_MULT, 1.0 + (jump / JUMP_THRESHOLD))
                self._jump_signal[ticker] = max(
                    self._jump_signal.get(ticker, 1.0), signal)

        self._last_price[ticker] = mid

    def decay(self):
        """Decay jump signals each tick."""
        for t in list(self._jump_signal.keys()):
            self._jump_signal[t] *= JUMP_DECAY_RATE
            if self._jump_signal[t] < 1.0 + JUMP_MIN_SIGNAL:
                self._jump_signal[t] = 1.0

    def get_widen_mult(self, ticker: str) -> float:
        """Get spread widening multiplier (1.0 = no widening)."""
        return self._jump_signal.get(ticker, 1.0)

    def is_jumping(self, ticker: str) -> bool:
        return self._jump_signal.get(ticker, 1.0) > 1.1

    def reset_day(self):
        """Reset at day boundary - expect jumps on new day."""
        # Don't reset: keep decay natural


# ============================================================
# ORDER TRACKER
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
# AVELLANEDA-STOIKOV MARKET MAKER
# ============================================================

class ASMarketMaker:
    """Market maker using the Avellaneda-Stoikov optimal quoting framework.

    The AS model provides:
    1. Reservation price: r = s - q * gamma * sigma^2 * tau
       (where tau = time remaining in current day)
    2. Optimal spread: delta* = gamma * sigma^2 * tau + (2/gamma) * ln(1 + gamma/k)

    We adapt this by:
    - Varying gamma based on inventory level and time-of-day
    - Using real-time sigma estimates
    - Adding jump detection for news events
    - Enforcing competition-specific position limits
    """

    def __init__(self, api: RITApi):
        self.api = api
        self.vol_estimator = VolatilityEstimator()
        self.jump_detector = JumpDetector()
        self.order_tracker = OrderTracker()

        self.positions: Dict[str, int] = {t: 0 for t in TICKERS}
        self.mid_prices: Dict[str, float] = {}
        self.book_imbalance: Dict[str, float] = {t: 0.0 for t in TICKERS}
        self.market_spread: Dict[str, float] = {t: 0.10 for t in TICKERS}

        # Limits
        self.gross_limit = DEFAULT_GROSS_LIMIT
        self.close_limit = CLOSE_TIME_LIMIT
        self.net_limit = DEFAULT_NET_LIMIT
        self._close_limit_confirmed = False   # True once read from news

        # P&L
        self.start_nlv = 0.0
        self.current_pnl = 0.0
        self.circuit_breaker_active = False
        self.circuit_breaker_halt = False

        # Adaptive spread multiplier (learned from fill rates)
        self.adaptive_mult: Dict[str, float] = {t: 1.0 for t in TICKERS}
        self.fill_count: Dict[str, int] = {t: 0 for t in TICKERS}
        self.last_adaptive_tick = 0

        # Stats
        self.orders_placed = 0
        self.orders_cancelled = 0
        self.total_volume_traded = 0
        self.market_orders_sent = 0
        self.rebate_earned_est = 0.0

        self.in_lockdown = False
        self._widen_entered = False

    # ============================================================
    # AVELLANEDA-STOIKOV CORE FORMULAS
    # ============================================================

    def as_gamma(self, ticker: str, second_in_day: int) -> float:
        """Compute time-varying risk aversion parameter gamma.

        - Low gamma during active trading = tighter spreads, more volume
        - High gamma when inventory is large = wider spreads, stronger skew
        - Very high gamma near close = forces inventory reduction
        """
        pos = self.positions.get(ticker, 0)
        per_stock_limit = PER_STOCK_TRADING_LIMIT

        # Base gamma
        gamma = AS_GAMMA_NORMAL

        # Increase gamma with inventory (quadratic scaling)
        if per_stock_limit > 0:
            inv_frac = abs(pos) / per_stock_limit
            if inv_frac > 0.5:
                # Smoothly ramp up gamma as inventory grows
                gamma += (AS_GAMMA_HIGH_INV - AS_GAMMA_NORMAL) * (inv_frac - 0.5) * 2
            if inv_frac > 0.8:
                gamma *= 2.0  # Double risk aversion when very loaded

        # Time-based: increase gamma as close approaches
        time_remaining = max(1, DAY_LENGTH - second_in_day)
        if time_remaining < 15:
            # Last 15 seconds: ramp up gamma significantly
            close_urgency = (15 - time_remaining) / 15.0
            gamma += (AS_GAMMA_PRE_CLOSE - gamma) * close_urgency

        return max(0.01, gamma)

    def as_reservation_price(self, ticker: str, second_in_day: int) -> float:
        """Compute the AS reservation price (risk-adjusted fair value).

        r = s - q * gamma * sigma^2 * tau

        This shifts "fair value" based on inventory:
        - If long (q > 0): reservation price < mid → sell more aggressively
        - If short (q < 0): reservation price > mid → buy more aggressively
        """
        s = self.mid_prices.get(ticker, 25.0)
        pos = self.positions.get(ticker, 0)
        per_stock_limit = PER_STOCK_TRADING_LIMIT

        # Normalize position to [-1, +1] range
        if per_stock_limit > 0:
            q_normalized = pos / per_stock_limit
        else:
            q_normalized = 0.0

        gamma = self.as_gamma(ticker, second_in_day)
        sigma_sq = self.vol_estimator.get_sigma_squared(ticker)

        # tau = time remaining (in ticks/seconds)
        tau = max(1, DAY_LENGTH - second_in_day)

        # AS reservation price formula
        # Scale tau to make the skew reasonable (tau in seconds, sigma^2 in price units)
        skew = q_normalized * gamma * sigma_sq * tau

        # Additional skew from book imbalance
        imbalance = self.book_imbalance.get(ticker, 0.0)
        imb_skew = 0.0
        if abs(imbalance) > IMBALANCE_THRESHOLD:
            imb_skew = imbalance * IMBALANCE_SKEW_FACTOR

        reservation = s - skew + imb_skew

        return reservation

    def as_optimal_spread(self, ticker: str, second_in_day: int,
                          layer: int = 1) -> float:
        """Compute the AS optimal spread.

        delta* = gamma * sigma^2 * tau + (2/gamma) * ln(1 + gamma/k)

        First term: risk premium (wider when volatile, more time remaining)
        Second term: market power / arrival intensity term
        """
        gamma = self.as_gamma(ticker, second_in_day)
        sigma_sq = self.vol_estimator.get_sigma_squared(ticker)
        k = AS_K_PARAMETER
        tau = max(1, DAY_LENGTH - second_in_day)

        # AS formula
        risk_premium = gamma * sigma_sq * tau
        market_power = (2.0 / gamma) * math.log(1.0 + gamma / k)

        as_spread = risk_premium + market_power

        # Also consider market spread (don't quote wider than necessary)
        mkt_spread = self.market_spread.get(ticker, 0.05)
        inside_spread = mkt_spread * SPREAD_INSIDE_FACTOR

        # Use the better of AS and inside-market spread
        spread = max(as_spread, inside_spread)

        # Apply floor from config
        floor = SPREAD_MIN_PER_TICKER.get(ticker, SPREAD_MIN_ABSOLUTE)
        spread = max(spread, floor)

        # Layer multiplier
        layer_cfg = LAYER_CONFIG.get(layer, {"spread_mult": 1.0})
        spread *= layer_cfg["spread_mult"]

        # Jump detection: widen on news
        jump_mult = self.jump_detector.get_widen_mult(ticker)
        spread *= jump_mult

        # Volatility regime adjustment
        regime = self.vol_estimator.get_regime(ticker)
        vol_mult = VOL_SPREAD_MULT.get(regime, 1.0)
        spread *= vol_mult

        # Adaptive multiplier (learned from fill rates)
        spread *= self.adaptive_mult.get(ticker, 1.0)

        # Time-based overrides
        if second_in_day < POST_CLOSE_RECOVERY_SEC:
            spread *= POST_CLOSE_SPREAD_MULT
        elif second_in_day >= PRE_CLOSE_WIDEN_SEC:
            # Near close: AS formula already handles this via high gamma
            # Just add a small safety multiplier
            spread *= 1.3

        return max(SPREAD_MIN_ABSOLUTE, min(SPREAD_MAX_ABSOLUTE, round(spread, 4)))

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
                # Estimate rebate earned
                self.rebate_earned_est += pos_change * REBATES.get(t, 0.01)
            bid = sec.get("bid", 0) or 0
            ask = sec.get("ask", 0) or 0
            if bid > 0 and ask > 0:
                mid = (bid + ask) / 2.0
                self.mid_prices[t] = mid
                self.vol_estimator.update(t, mid)
                self.jump_detector.update(t, mid)
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

    def update_pnl(self):
        nlv = self.api.get_nlv()
        if nlv is not None and nlv > 0:
            # Latch start_nlv on first non-zero read (API returns 0 at startup)
            if self.start_nlv <= 0:
                self.start_nlv = nlv
                print(f"    [NLV] Start NLV latched: ${nlv:,.2f}")
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
                self.circuit_breaker_halt = False
        else:
            self.circuit_breaker_active = False
            self.circuit_breaker_halt = False

    def update_adaptive_spreads(self, tick: int):
        if tick - self.last_adaptive_tick < ADAPTIVE_INTERVAL:
            return
        for ticker in TICKERS:
            fills = self.fill_count.get(ticker, 0)
            if fills < 5:
                # Not enough fills → tighten spread to attract more
                self.adaptive_mult[ticker] *= 0.93
            elif fills > ADAPTIVE_TARGET_FILLS * 2:
                # Too many fills → can afford wider spread
                self.adaptive_mult[ticker] *= 1.06
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
    # ORDER SIZING - Volume-maximizing with AS risk control
    # ============================================================

    def compute_order_size(self, ticker: str, side: str, utilization: float,
                           second_in_day: int, layer: int = 1) -> int:
        base_size = BASE_ORDER_SIZE.get(ticker, 3000)
        pos = self.positions.get(ticker, 0)
        per_stock_limit = PER_STOCK_TRADING_LIMIT

        # Layer sizing
        layer_cfg = LAYER_CONFIG.get(layer, {"size_mult": 1.0})
        layer_mult = layer_cfg["size_mult"]

        # ============================================================
        # ASYMMETRIC SIZING (inventory-aware)
        # ============================================================
        asym_mult = 1.0
        asym_threshold = per_stock_limit * ASYM_KICK_IN_FRAC

        if abs(pos) > asym_threshold:
            pos_frac = min(abs(pos) / per_stock_limit, 1.0)
            if (pos > 0 and side == "SELL") or (pos < 0 and side == "BUY"):
                # Reducing side: boost size (want to flatten faster)
                asym_mult = 1.0 + pos_frac * (ASYM_REDUCE_BOOST - 1.0)
            elif (pos > 0 and side == "BUY") or (pos < 0 and side == "SELL"):
                # Increasing side: restrict
                asym_mult = max(ASYM_INCREASE_FLOOR, 1.0 - pos_frac * 0.8)

        # Volatility regime
        regime = self.vol_estimator.get_regime(ticker)
        vol_mult = VOL_SIZE_MULT.get(regime, 1.0)

        # Utilization reduction
        if utilization > UTIL_PANIC:
            util_mult = 0.0
        elif utilization > UTIL_EMERGENCY:
            util_mult = 0.15
        elif utilization > UTIL_REDUCE:
            util_mult = 0.4
        elif utilization > UTIL_SKEW:
            util_mult = 0.65
        else:
            util_mult = 1.0

        # Time-based
        time_mult = 1.0
        if second_in_day < POST_CLOSE_RECOVERY_SEC:
            time_mult = POST_CLOSE_SIZE_MULT
        elif second_in_day >= PRE_CLOSE_WIDEN_SEC:
            time_mult = 0.3

        # Jump detection: reduce size during jumps (avoid adverse selection)
        jump_mult = 1.0
        if self.jump_detector.is_jumping(ticker):
            jump_mult = 0.4

        cb_mult = 0.3 if self.circuit_breaker_active else 1.0

        size = int(base_size * layer_mult * asym_mult *
                   vol_mult * util_mult * time_mult * jump_mult * cb_mult)

        # ============================================================
        # HARD CAPS
        # ============================================================
        pending = self.order_tracker.pending_exposure(ticker, side)

        # Per-stock limit
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
    # QUOTING DECISIONS
    # ============================================================

    def should_quote_side(self, ticker: str, side: str, utilization: float) -> bool:
        pos = self.positions.get(ticker, 0)
        per_stock_limit = PER_STOCK_TRADING_LIMIT

        if self.circuit_breaker_halt or self.in_lockdown:
            return False

        if side == "BUY" and pos >= per_stock_limit:
            return False
        if side == "SELL" and pos <= -per_stock_limit:
            return False

        if utilization > UTIL_EMERGENCY:
            if (pos > 0 and side == "BUY") or (pos < 0 and side == "SELL"):
                return False
            if pos == 0:
                return False

        gross = self.compute_aggregate()
        if gross >= int(self.gross_limit * GROSS_LIMIT_BUFFER):
            if (pos > 0 and side == "BUY") or (pos < 0 and side == "SELL") or pos == 0:
                return False

        net = self.compute_net()
        if net >= int(self.net_limit * NET_LIMIT_BUFFER):
            net_dir = sum(self.positions.values())
            if (net_dir > 0 and side == "BUY") or (net_dir < 0 and side == "SELL"):
                return False

        return True

    # ============================================================
    # MAIN QUOTING ENGINE - AS-OPTIMAL
    # ============================================================

    def quote_ticker(self, ticker: str, tick: int, second_in_day: int,
                     utilization: float):
        mid = self.mid_prices.get(ticker, 25.0)
        if mid < MIN_PRICE or mid > MAX_PRICE:
            return

        # AS reservation price (risk-adjusted fair value)
        reservation = self.as_reservation_price(ticker, second_in_day)

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
            # Update reservation with fresh mid
            reservation = self.as_reservation_price(ticker, second_in_day)

        # Compute all layers
        layers = []
        for layer_num in range(1, NUM_LAYERS + 1):
            # Skip outer layers near close/open
            if layer_num == 3 and (second_in_day < POST_CLOSE_RECOVERY_SEC or
                                    second_in_day >= PRE_CLOSE_WIDEN_SEC):
                continue
            if layer_num == 2 and second_in_day >= PRE_CLOSE_CANCEL_SEC:
                continue

            # AS optimal spread for this layer
            spread = self.as_optimal_spread(ticker, second_in_day, layer=layer_num)
            half = spread / 2.0

            buy_size = self.compute_order_size(
                ticker, "BUY", utilization, second_in_day, layer=layer_num) if quote_buy else 0
            sell_size = self.compute_order_size(
                ticker, "SELL", utilization, second_in_day, layer=layer_num) if quote_sell else 0

            # Quote around RESERVATION PRICE (not mid!)
            # This is the key AS insight: shift quotes based on inventory
            bid = round(reservation - half, 2)
            ask = round(reservation + half, 2)

            # Safety checks
            if bid >= ask:
                bid = round(mid - 0.02, 2)
                ask = round(mid + 0.02, 2)
            bid = max(MIN_PRICE, min(bid, MAX_PRICE - 0.02))
            ask = max(bid + 0.01, min(ask, MAX_PRICE))

            # Outer layers must be outside inner layers
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
    # PROACTIVE POSITION REDUCTION
    # ============================================================

    def reduce_large_positions(self, tick: int):
        for ticker in TICKERS:
            pos = self.positions.get(ticker, 0)
            per_stock_limit = PER_STOCK_TRADING_LIMIT
            threshold = int(per_stock_limit * 0.70)
            if abs(pos) < threshold:
                continue

            mid = self.mid_prices.get(ticker, 25.0)
            excess = abs(pos) - threshold

            if abs(pos) > per_stock_limit * 0.90:
                # Near limit: use market orders
                mkt_size = min(excess, int(per_stock_limit * 0.20))
                mkt_size = max(100, min(mkt_size, MAX_ORDER_SIZE))
                side = "SELL" if pos > 0 else "BUY"
                self.api.submit_market_order(ticker, mkt_size, side)
                self.market_orders_sent += 1
            else:
                # Aggressive limit near mid
                reduce_size = min(excess, int(per_stock_limit * 0.25))
                reduce_size = max(100, min(reduce_size, MAX_ORDER_SIZE))
                if pos > 0:
                    price = round(mid - 0.01, 2)
                    self.api.submit_limit_order(ticker, reduce_size, "SELL", price)
                else:
                    price = round(mid + 0.01, 2)
                    self.api.submit_limit_order(ticker, reduce_size, "BUY", price)

    # ============================================================
    # MARKET CLOSE PROTOCOL - TIGHT AND FAST
    # ============================================================

    def pre_close_handler(self, tick: int, second_in_day: int):
        aggregate = self.compute_aggregate()
        close_target = int(self.close_limit * CLOSE_TARGET_UTILIZATION)

        if second_in_day >= PRE_CLOSE_FLATTEN_SEC:
            # Market-order flatten: cancel every tick so no stale limits accumulate
            self.api.cancel_all_orders()
            self.order_tracker.clear_all()
            if not self.in_lockdown:
                self.in_lockdown = True
                print(f"    [LOCKDOWN] T{tick} sec={second_in_day} "
                      f"agg={aggregate} close_lim={self.close_limit} "
                      f"target={close_target}")

            if aggregate > close_target:
                sorted_positions = sorted(
                    [(t, self.positions[t]) for t in TICKERS if abs(self.positions[t]) > 0],
                    key=lambda x: abs(x[1]), reverse=True)
                remaining = aggregate - close_target
                for t, pos in sorted_positions:
                    if remaining <= 0:
                        break
                    to_reduce = min(abs(pos), remaining, MAX_ORDER_SIZE)
                    to_reduce = max(100, to_reduce)
                    side = "SELL" if pos > 0 else "BUY"
                    self.api.submit_market_order(t, to_reduce, side)
                    self.market_orders_sent += 1
                    remaining -= to_reduce
                    print(f"    [FLATTEN] {t}: MKT {side} {to_reduce:,} "
                          f"(pos={pos:+,} agg={aggregate:,} target={close_target:,})")

        elif second_in_day >= PRE_CLOSE_WIDEN_SEC:
            # Widen phase: cancel MM quotes ONCE, then place limit reduces near mid.
            # Limit orders sit in book for ~7 seconds earning rebates.
            # At PRE_CLOSE_FLATTEN_SEC, they get cancelled and market orders finish the job.
            if not self._widen_entered:
                self._widen_entered = True
                self.api.cancel_all_orders()
                self.order_tracker.clear_all()
                print(f"    [WIDEN] T{tick} sec={second_in_day} "
                      f"agg={aggregate} -> placing limit reduces")

                for ticker in TICKERS:
                    pos = self.positions.get(ticker, 0)
                    if abs(pos) < 200:
                        continue
                    mid = self.mid_prices.get(ticker, 25.0)
                    # Offer to sell/buy at mid ± 1c — aggressive but earns rebate
                    reduce_size = min(abs(pos), MAX_ORDER_SIZE)
                    reduce_size = max(100, reduce_size)
                    if pos > 0:
                        price = round(mid - 0.01, 2)
                        self.api.submit_limit_order(ticker, reduce_size, "SELL", price)
                        print(f"      {ticker}: limit SELL {reduce_size:,} @ {price}")
                    else:
                        price = round(mid + 0.01, 2)
                        self.api.submit_limit_order(ticker, reduce_size, "BUY", price)
                        print(f"      {ticker}: limit BUY  {reduce_size:,} @ {price}")

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
    # NEWS/LIMIT DETECTION FROM API
    # ============================================================

    def check_news_for_limits(self):
        """Check news feed for the aggregate position limit announced at tick 1.
        Targets the specific headline to avoid false matches from later news.
        Stops scanning once confirmed.
        """
        if self._close_limit_confirmed:
            return
        import re
        news = self.api.get_news()
        if not news:
            return
        for item in news:
            headline = str(item.get("headline", "")).lower()
            body = str(item.get("body", ""))
            # Target the specific tick-1 announcement by headline
            if "aggregate position limit" in headline:
                m = re.search(r'limit\s+(?:to|of)\s+([\d,]+)', body, re.IGNORECASE)
                if m:
                    try:
                        num = int(m.group(1).replace(",", ""))
                        if num != self.close_limit:
                            print(f"    [NEWS] Aggregate close-time limit: {num:,}")
                            self.close_limit = num
                        self._close_limit_confirmed = True
                        return
                    except ValueError:
                        pass
                # Fallback: any number 1000-50000 in this specific headline's body
                for num_str in re.findall(r'[\d,]+', body):
                    try:
                        num = int(num_str.replace(",", ""))
                        if 1000 <= num <= 50000:
                            if num != self.close_limit:
                                print(f"    [NEWS] Aggregate close-time limit: {num:,}")
                                self.close_limit = num
                            self._close_limit_confirmed = True
                            return
                    except ValueError:
                        pass

    # ============================================================
    # MAIN LOOP
    # ============================================================

    def run(self):
        print(BANNER.format(
            psl=PER_STOCK_TRADING_LIMIT, gl=self.gross_limit, cl=self.close_limit,
            cycle=int(CYCLE_SLEEP * 1000), layers=NUM_LAYERS,
            gamma=AS_GAMMA_NORMAL, url=API_BASE_URL, key=API_KEY))

        self.update_limits()
        print(f"  Intraday gross limit: {self.gross_limit:,}")
        print(f"  Close-time limit: {self.close_limit:,}")
        print(f"  Net limit: {self.net_limit:,}")
        for t in TICKERS:
            print(f"  {t}: per_stock={PER_STOCK_TRADING_LIMIT:,}, "
                  f"base_size={BASE_ORDER_SIZE[t]}, rebate=${REBATES[t]}")

        # NLV is often 0 at startup; update_pnl() will latch it on first non-zero read
        print(f"  Starting NLV: will latch on first non-zero read")

        # Check news on first tick for aggregate limit
        self.check_news_for_limits()

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
                        print(f"  Est. rebates: ${self.rebate_earned_est:,.2f}")
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
                # DAY BOUNDARY HANDLING
                # ============================================================
                if current_day != last_day:
                    if last_day >= 0:
                        print(f"\n  === DAY {current_day + 1} (tick {tick}) ===")

                    self.update_state(tick)

                    # Check news for aggregate limit on day 1
                    if current_day == 0 or tick <= 5:
                        self.check_news_for_limits()
                        print(f"    Close limit: {self.close_limit:,}")

                    self.in_lockdown = False
                    self._widen_entered = False
                    last_day = current_day

                # Circuit breaker halt
                if self.circuit_breaker_halt:
                    self.update_pnl()
                    time.sleep(0.5)
                    continue

                # ============================================================
                # STATE UPDATES
                # ============================================================
                self.update_state(tick)
                if tick % 5 == 0:
                    self.update_limits()
                if tick % 3 == 0:
                    self.update_pnl()
                if tick % 2 == 0:
                    self.check_news_for_limits()
                self.update_adaptive_spreads(tick)
                self.jump_detector.decay()

                utilization = self.compute_utilization()
                aggregate = self.compute_aggregate()

                # ============================================================
                # PRE-CLOSE PHASE
                # ============================================================
                if second_in_day >= PRE_CLOSE_WIDEN_SEC:
                    self.pre_close_handler(tick, second_in_day)
                    time.sleep(CYCLE_SLEEP)
                    continue  # Never do normal quoting during close phase

                # ============================================================
                # POST-CLOSE RECOVERY
                # ============================================================
                if second_in_day < POST_CLOSE_RECOVERY_SEC:
                    # Always cancel on new day open — previous check only tested TICKERS[0]
                    self.api.cancel_all_orders()
                    self.order_tracker.clear_all()
                    time.sleep(CYCLE_SLEEP)
                    continue

                # ============================================================
                # ACTIVE TRADING
                # ============================================================
                self.in_lockdown = False

                if utilization > UTIL_PANIC:
                    self.panic_flatten()
                    time.sleep(CYCLE_SLEEP)
                    continue
                elif utilization > UTIL_EMERGENCY:
                    self.emergency_flatten(target_util=UTIL_REDUCE)

                # Proactive position reduction (every other tick)
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

                    # Show AS parameters
                    sigma_str = " ".join(
                        f"{t}:{self.vol_estimator.get_sigma(t):.3f}"
                        for t in TICKERS)

                    print(f"  T{tick:03d} d{second_in_day:02d} | "
                          f"pos=[{pos_str}] | "
                          f"agg={aggregate:,}/{self.gross_limit:,}({utilization:.0%}) | "
                          f"pnl={pnl_str} | "
                          f"sigma=[{sigma_str}] | "
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
    fh = logging.FileHandler(os.path.join(log_dir, f"mm_v15_AS_{ts}.log"))
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

        mm = ASMarketMaker(api)
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