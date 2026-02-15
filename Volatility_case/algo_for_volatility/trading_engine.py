"""
V10 Trading Engine - Liam's Optimal Straddle Method.

Weekly straddle positioning with mathematically optimal sizing.
Each week:
  1. Close old position
  2. Run optimizer to find n* straddles at ATM strike
  3. Execute position
  4. Delta hedge ONLY when approaching delta limit
  5. Repeat at next week boundary

Delta hedging: hedge to zero delta when |delta| hits ~88% of limit.
"""

import logging
import math
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from config import (
    UNDERLYING_TICKER, STRIKE_PRICES, OPTIONS_MULTIPLIER,
    RISK_FREE_RATE, TICKS_PER_SUBHEAT, TICKS_PER_WEEK,
    TRADING_DAYS_PER_MONTH, TRADING_DAYS_PER_YEAR,
    MAX_ORDERS_PER_CYCLE, OPTIONS_MAX_TRADE_SIZE,
    RTM_MAX_TRADE_SIZE, RTM_GROSS_LIMIT, RTM_NET_LIMIT,
    OPTIONS_GROSS_LIMIT, OPTIONS_NET_LIMIT,
    MIN_OPTION_PRICE, HEDGE_TRIGGER_PCT, HEDGE_TARGET_DELTA,
    MIN_HEDGE_SIZE, HEDGE_COOLDOWN_TICKS, POSITION_TICKS,
    UNWIND_START_TICK, MAX_STRADDLES,
)
from black_scholes import bs_gamma, bs_delta, bs_vega, bs_price, implied_volatility
from news_parser import VolatilityState
from rit_api import RITApi
from optimizer import find_optimal_n, compute_straddle_gamma, MULTIPLIER

logger = logging.getLogger(__name__)

# Holding period: 1 week in years
T_HOLD = 1.0 / 52.0


# ============================================================================
# Data classes
# ============================================================================

@dataclass
class OptionInfo:
    ticker: str
    option_type: str  # "CALL" or "PUT"
    strike: float
    bid: float
    ask: float
    mid: float
    position: int
    iv: float = 0.0


@dataclass
class MarketState:
    """Snapshot of current market and portfolio state."""
    spot: float = 0.0
    spot_bid: float = 0.0
    spot_ask: float = 0.0
    stock_position: int = 0
    options: List[OptionInfo] = field(default_factory=list)
    total_delta: float = 0.0
    total_gamma: float = 0.0
    options_gross: int = 0
    options_net: int = 0


# ============================================================================
# Engine states
# ============================================================================

class Phase:
    IDLE = "IDLE"              # No position, waiting for news/positioning tick
    CLOSING = "CLOSING"        # Flattening old position
    POSITIONING = "POSITIONING"  # Building new straddle position
    HOLDING = "HOLDING"        # Position built, only delta hedging
    UNWINDING = "UNWINDING"    # End of sub-heat, closing everything


class StraddleEngine:
    """Week-based straddle trading engine with optimal sizing."""

    def __init__(self, api: RITApi):
        self.api = api
        self.vol_state = VolatilityState()
        self.phase = Phase.IDLE

        # Current position info
        self.current_n = 0          # Straddles held (magnitude)
        self.current_direction = 0  # +1 long, -1 short
        self.current_strike = None
        self.expected_profit = 0.0

        # Week tracking
        self.weeks_positioned = set()

        # Saved optimizer result from before close (use this for positioning)
        self.pending_n = 0
        self.pending_direction = 0
        self.pending_strike = None
        self.pending_profit = 0.0

        # Delta hedging
        self.last_hedge_tick = -100
        self.rtm_volume = 0

        # Cycle tracking for closing/positioning
        self.close_ticks = 0
        self.position_ticks = 0
        self.close_cancel_phase = True  # True = cancel phase, False = close phase

    def reset(self):
        """Reset for new sub-heat."""
        self.vol_state = VolatilityState()
        self.phase = Phase.IDLE
        self.current_n = 0
        self.current_direction = 0
        self.current_strike = None
        self.expected_profit = 0.0
        self.weeks_positioned = set()
        self.pending_n = 0
        self.pending_direction = 0
        self.pending_strike = None
        self.pending_profit = 0.0
        self.last_hedge_tick = -100
        self.rtm_volume = 0
        self.close_ticks = 0
        self.position_ticks = 0
        self.close_cancel_phase = True

    # ========================================================================
    # Time helpers
    # ========================================================================

    def get_time_to_expiry(self, tick: int) -> float:
        """Time to option expiry in years."""
        ticks_remaining = max(TICKS_PER_SUBHEAT - tick, 1)
        days_remaining = (ticks_remaining / TICKS_PER_SUBHEAT) * TRADING_DAYS_PER_MONTH
        return days_remaining / TRADING_DAYS_PER_YEAR

    def get_current_week(self, tick: int) -> int:
        """Which week (1-4) we're in."""
        for i, pt in enumerate(POSITION_TICKS):
            next_pt = POSITION_TICKS[i + 1] if i + 1 < len(POSITION_TICKS) else TICKS_PER_SUBHEAT + 1
            if pt <= tick < next_pt:
                return i + 1
        return 4

    def is_position_tick(self, tick: int) -> bool:
        """Should we take a new position at this tick?
        Week 1: allow anytime from tick 1 to 70 (vol news can arrive late).
        Other weeks: 5-tick window after each position tick."""
        week = self.get_current_week(tick)
        if week in self.weeks_positioned:
            return False
        # Week 1: wide window so we don't miss it
        if week == 1 and 1 <= tick <= 70:
            return True
        for pt in POSITION_TICKS:
            if pt <= tick <= pt + 5:
                return True
        return False

    # ========================================================================
    # Market data
    # ========================================================================

    def update_news(self) -> bool:
        news = self.api.get_news(since=self.vol_state.last_news_id)
        if news:
            return self.vol_state.process_news(news)
        return False

    def get_market_state(self, tick: int) -> Optional[MarketState]:
        """Get current market snapshot with positions and Greeks."""
        securities = self.api.get_securities()
        if not securities:
            return None

        state = MarketState()
        vol = self.vol_state.best_vol_estimate
        T_exp = self.get_time_to_expiry(tick)

        for sec in securities:
            ticker = sec.get("ticker", "")
            position = int(sec.get("position", 0) or 0)
            bid = sec.get("bid", 0) or 0
            ask = sec.get("ask", 0) or 0
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0

            if ticker == UNDERLYING_TICKER:
                state.spot = mid if mid > 0 else (sec.get("last", 0) or 0)
                state.spot_bid = bid
                state.spot_ask = ask
                state.stock_position = position
                continue

            # Parse option ticker
            if ticker.startswith("RTM") and ("C" in ticker or "P" in ticker):
                option_type = "CALL" if "C" in ticker else "PUT"
                try:
                    strike = float(ticker[-2:])
                except ValueError:
                    continue
                if strike not in STRIKE_PRICES:
                    continue

                opt = OptionInfo(
                    ticker=ticker, option_type=option_type, strike=strike,
                    bid=bid, ask=ask, mid=mid, position=position,
                )

                # Compute IV from market mid price
                if mid > 0 and state.spot > 0 and T_exp > 0:
                    opt.iv = implied_volatility(mid, state.spot, strike,
                                                T_exp, RISK_FREE_RATE, option_type)

                # Accumulate Greeks from positions
                if position != 0 and vol and vol > 0 and state.spot > 0 and T_exp > 0:
                    d = bs_delta(state.spot, strike, T_exp, RISK_FREE_RATE, vol, option_type)
                    g = bs_gamma(state.spot, strike, T_exp, RISK_FREE_RATE, vol)
                    state.total_delta += position * d * OPTIONS_MULTIPLIER
                    state.total_gamma += position * g * OPTIONS_MULTIPLIER

                state.options_gross += abs(position)
                state.options_net += position
                state.options.append(opt)

        # Add stock delta
        state.total_delta += state.stock_position
        return state

    def get_best_strike(self, spot: float) -> float:
        """Get the strike closest to current spot (ATM)."""
        return min(STRIKE_PRICES, key=lambda k: abs(k - spot))

    def get_option_at_strike(self, state: MarketState, strike: float,
                             opt_type: str) -> Optional[OptionInfo]:
        """Find option info for a given strike and type."""
        for opt in state.options:
            if opt.strike == strike and opt.option_type == opt_type:
                return opt
        return None

    def get_straddle_spread(self, state: MarketState, strike: float) -> float:
        """Bo = call_spread + put_spread at this strike."""
        call = self.get_option_at_strike(state, strike, "CALL")
        put = self.get_option_at_strike(state, strike, "PUT")

        Bo = 0.0
        if call and call.bid > 0 and call.ask > 0:
            Bo += call.ask - call.bid
        if put and put.bid > 0 and put.ask > 0:
            Bo += put.ask - put.bid
        return Bo

    def get_implied_vol_at_strike(self, state: MarketState, strike: float) -> float:
        """Average implied vol of call and put at this strike."""
        call = self.get_option_at_strike(state, strike, "CALL")
        put = self.get_option_at_strike(state, strike, "PUT")

        ivs = []
        if call and call.iv > 0:
            ivs.append(call.iv)
        if put and put.iv > 0:
            ivs.append(put.iv)

        if ivs:
            return sum(ivs) / len(ivs)
        return 0.0

    # ========================================================================
    # Run optimizer
    # ========================================================================

    def run_optimizer(self, state: MarketState, tick: int) -> Tuple[int, int, float, float]:
        """
        Run Liam's optimizer to find optimal straddle position.

        Returns: (n_star, direction, expected_pnl, strike)
        """
        sigma = self.vol_state.best_vol_estimate
        L = self.vol_state.delta_limit

        if not sigma or sigma <= 0:
            logger.info("No vol estimate yet, cannot optimize")
            return 0, 0, 0.0, 0.0

        if not L or L <= 0:
            L = 10000.0  # Default delta limit
            logger.info("No delta limit from news, using default %.0f", L)

        S0 = state.spot
        if S0 <= 0:
            return 0, 0, 0.0, 0.0

        # Choose ATM strike
        X = self.get_best_strike(S0)

        # Time to option expiry (for gamma calculation)
        T_exp = self.get_time_to_expiry(tick)

        # Call gamma per share at ATM
        gamma_c = bs_gamma(S0, X, T_exp, RISK_FREE_RATE, sigma)
        if gamma_c <= 0:
            return 0, 0, 0.0, X

        # Implied vol from market
        sigma_hat = self.get_implied_vol_at_strike(state, X)
        if sigma_hat <= 0:
            # Fallback: average across all options
            all_ivs = [o.iv for o in state.options if o.iv > 0]
            sigma_hat = sum(all_ivs) / len(all_ivs) if all_ivs else 0.0
        if sigma_hat <= 0:
            logger.info("No implied vol data, cannot optimize")
            return 0, 0, 0.0, X

        # Market spreads
        Bo = self.get_straddle_spread(state, X)
        Bs = (state.spot_ask - state.spot_bid) if state.spot_ask > state.spot_bid else 0.02

        # Run the optimizer (equation 5)
        n_star, direction, f_star = find_optimal_n(
            S0=S0, X=X,
            gamma_call_per_share=gamma_c,
            sigma=sigma,
            sigma_hat=sigma_hat,
            L=L, Bo=Bo, Bs=Bs,
            T_hold=T_HOLD,
            max_n=MAX_STRADDLES,
        )

        return n_star, direction, f_star, X

    # ========================================================================
    # Position execution
    # ========================================================================

    def close_all_positions(self, state: MarketState) -> int:
        """Flatten all options and stock with market orders.
        Uses cancel-then-close phasing to prevent oscillation.
        Ignores tiny residual positions (<=2 options, <=200 stock)."""
        orders = 0

        # Cancel all open orders first
        self.api.cancel_all_orders()

        # If in cancel phase, just cancel and wait one cycle for cancels to settle
        if self.close_cancel_phase:
            self.close_cancel_phase = False
            logger.info("CLOSE: cancel phase - waiting for cancels to settle")
            return 0

        # Close options ONLY first (ignore tiny residuals <= 2)
        # Stock is closed separately after options are flat (avoids delta fight)
        has_options = False
        for opt in state.options:
            if abs(opt.position) <= 2:
                continue
            has_options = True
            action = "SELL" if opt.position > 0 else "BUY"
            remaining = abs(opt.position)
            while remaining > 0 and orders < MAX_ORDERS_PER_CYCLE:
                qty = min(remaining, OPTIONS_MAX_TRADE_SIZE)
                result = self.api.submit_market_order(opt.ticker, qty, action)
                if result:
                    orders += 1
                    remaining -= qty
                else:
                    break

        # Only close stock AFTER all options are flat (no gamma = no delta fight)
        if not has_options:
            stock_remaining = abs(state.stock_position)
            if stock_remaining > 200:
                action = "SELL" if state.stock_position > 0 else "BUY"
                while stock_remaining > 200 and orders < MAX_ORDERS_PER_CYCLE:
                    qty = min(stock_remaining, RTM_MAX_TRADE_SIZE)
                    result = self.api.submit_market_order(UNDERLYING_TICKER, qty, action)
                    if result:
                        self.rtm_volume += qty
                        orders += 1
                        stock_remaining -= qty
                    else:
                        break

        return orders

    def _submit_leg_orders(self, ticker: str, qty_total: int, action: str,
                           running_gross: int, running_net: int) -> tuple:
        """Submit orders for one leg (call or put). Returns (orders_sent, qty_filled, gross, net)."""
        orders = 0
        filled = 0
        g = running_gross
        n = running_net
        remaining = qty_total

        while remaining > 0 and orders < MAX_ORDERS_PER_CYCLE:
            qty = min(remaining, OPTIONS_MAX_TRADE_SIZE)

            # Pre-flight limit check
            new_gross = g + qty
            new_net = n + qty if action == "BUY" else n - qty
            if new_gross > OPTIONS_GROSS_LIMIT - 20:
                logger.warning("Gross limit approaching (%d), stopping", new_gross)
                break
            if abs(new_net) > OPTIONS_NET_LIMIT - 10:
                logger.warning("Net limit approaching (%d), stopping", new_net)
                break

            result = self.api.submit_market_order(ticker, qty, action)
            if result:
                orders += 1
                filled += qty
                remaining -= qty
                g = new_gross
                n = new_net
            else:
                break

        return orders, filled, g, n

    def take_straddle_position(self, n: int, direction: int,
                               strike: float, state: MarketState) -> int:
        """
        Execute n straddles at strike with interleaved call/put batches.
        direction: +1 = buy (long), -1 = sell (short)
        Submits in small interleaved batches (100C, 100P, 100C, 100P...)
        to ensure balanced legs. If one leg fails, closes the unmatched leg.
        """
        if n <= 0 or direction == 0:
            return 0

        # Cap at MAX_STRADDLES
        n = min(n, MAX_STRADDLES)

        action = "BUY" if direction > 0 else "SELL"
        call_ticker = f"RTM1C{int(strike)}"
        put_ticker = f"RTM1P{int(strike)}"

        # Track running limits
        g = state.options_gross
        net = state.options_net
        total_orders = 0
        total_calls = 0
        total_puts = 0
        remaining = n
        batch_size = OPTIONS_MAX_TRADE_SIZE  # 100

        while remaining > 0:
            qty = min(remaining, batch_size)

            # Submit call batch
            c_ord, c_fill, g, net = self._submit_leg_orders(
                call_ticker, qty, action, g, net)
            total_orders += c_ord
            total_calls += c_fill

            if c_fill == 0:
                logger.warning("Call leg failed, stopping straddle build")
                break

            # Submit matching put batch
            p_ord, p_fill, g, net = self._submit_leg_orders(
                put_ticker, c_fill, action, g, net)
            total_orders += p_ord
            total_puts += p_fill

            if p_fill < c_fill:
                # Put leg failed to match calls - close the unmatched calls
                unmatched = c_fill - p_fill
                reverse = "SELL" if action == "BUY" else "BUY"
                logger.warning("Put leg short by %d, closing unmatched calls", unmatched)
                self._submit_leg_orders(call_ticker, unmatched, reverse, g, net)
                total_calls -= unmatched
                break

            remaining -= c_fill

        straddles_filled = min(total_calls, total_puts)
        logger.info("STRADDLE %s %d/%d @ K=%.0f: %d orders (%dC+%dP filled)",
                    action, straddles_filled, n, strike,
                    total_orders, total_calls, total_puts)
        return total_orders

    # ========================================================================
    # Delta hedging
    # ========================================================================

    def delta_hedge(self, state: MarketState, tick: int) -> int:
        """
        Hedge when |delta| approaches delta limit.
        Hedge to zero delta (paper's recommendation).
        """
        L = self.vol_state.delta_limit
        if not L or L <= 0:
            L = 10000.0

        current_delta = state.total_delta
        abs_delta = abs(current_delta)

        # Only hedge when approaching the limit
        trigger = L * HEDGE_TRIGGER_PCT
        if abs_delta < trigger:
            return 0

        # Cooldown check - ABSOLUTE, no bypass (prevents oscillation death spiral)
        ticks_since = tick - self.last_hedge_tick
        if ticks_since < HEDGE_COOLDOWN_TICKS:
            return 0

        # Cancel pending stock orders to prevent accumulation
        self.api.cancel_orders_for_ticker(UNDERLYING_TICKER)

        # Hedge to zero: trade -current_delta shares
        hedge_needed = -current_delta
        hedge_total = abs(int(round(hedge_needed)))

        if hedge_total < MIN_HEDGE_SIZE:
            return 0

        # Check stock position limits before hedging
        action = "BUY" if hedge_needed > 0 else "SELL"
        current_stock = abs(state.stock_position)
        if action == "BUY":
            max_allowed = RTM_NET_LIMIT - state.stock_position
        else:
            max_allowed = RTM_NET_LIMIT + state.stock_position
        max_allowed = max(max_allowed, 0)
        hedge_total = min(hedge_total, max_allowed)

        if hedge_total < MIN_HEDGE_SIZE:
            logger.warning("Hedge capped by ETF limit: need %d, allowed %d",
                          abs(int(round(hedge_needed))), max_allowed)
            return 0

        # Submit ONE hedge order per cycle (prevents overshoot from stale position)
        hedge_qty = min(hedge_total, RTM_MAX_TRADE_SIZE)
        result = self.api.submit_market_order(UNDERLYING_TICKER, hedge_qty, action)
        orders_sent = 0
        if result:
            orders_sent = 1
            self.rtm_volume += hedge_qty

        if orders_sent > 0:
            self.last_hedge_tick = tick
            logger.info("HEDGE %s %d RTM | delta %.0f -> ~%.0f | limit %.0f (%.0f%%)",
                        action, hedge_qty, current_delta,
                        current_delta + (hedge_qty if action == "BUY" else -hedge_qty),
                        L, abs_delta / L * 100)
        return orders_sent

    # ========================================================================
    # Unwind
    # ========================================================================

    def unwind_positions(self, state: MarketState, tick: int) -> int:
        """Close all positions near end of sub-heat.
        ALL market orders (no limit orders - prevents stale fill oscillation).
        Cancel all open orders every cycle to prevent accumulation.
        Ignore tiny residuals to prevent flip-flopping."""
        orders = 0

        # Cancel ALL open orders every cycle during unwind
        self.api.cancel_all_orders()

        # Options: close everything with market orders (ignore residuals <= 5)
        for opt in state.options:
            if abs(opt.position) <= 5:
                continue
            action = "SELL" if opt.position > 0 else "BUY"
            remaining = abs(opt.position)
            while remaining > 0 and orders < MAX_ORDERS_PER_CYCLE:
                qty = min(remaining, OPTIONS_MAX_TRADE_SIZE)
                self.api.submit_market_order(opt.ticker, qty, action)
                orders += 1
                remaining -= qty

        # Stock: flatten with market orders, loop for large positions
        stock_remaining = abs(state.stock_position)
        if stock_remaining > 200:
            action = "SELL" if state.stock_position > 0 else "BUY"
            while stock_remaining > 200 and orders < MAX_ORDERS_PER_CYCLE:
                qty = min(stock_remaining, RTM_MAX_TRADE_SIZE)
                self.api.submit_market_order(UNDERLYING_TICKER, qty, action)
                self.rtm_volume += qty
                orders += 1
                stock_remaining -= qty

        return orders

    # ========================================================================
    # Check if position is flat
    # ========================================================================

    def is_flat(self, state: MarketState) -> bool:
        """Are all positions closed?"""
        if abs(state.stock_position) > 100:
            return False
        for opt in state.options:
            if abs(opt.position) > 2:
                return False
        return True

    # ========================================================================
    # Main cycle
    # ========================================================================

    def execute_cycle(self, tick: int) -> Dict:
        """Execute one trading cycle."""
        result = {
            "tick": tick,
            "phase": self.phase,
            "vol": None,
            "market_iv": None,
            "direction": self.current_direction,
            "n_straddles": self.current_n,
            "strike": self.current_strike,
            "edge": 0.0,
            "delta": 0.0,
            "delta_limit": None,
            "spot": 0.0,
            "gross": 0,
            "net": 0,
            "hedge_trades": 0,
            "option_trades": 0,
            "expected_pnl": self.expected_profit,
            "rtm_vol": self.rtm_volume,
        }

        # 1. Update news
        self.update_news()
        vol = self.vol_state.best_vol_estimate
        result["vol"] = (vol * 100) if vol else None
        result["delta_limit"] = self.vol_state.delta_limit

        # 2. Get market state
        state = self.get_market_state(tick)
        if state is None:
            return result

        result["spot"] = state.spot
        result["delta"] = state.total_delta
        result["gross"] = state.options_gross
        result["net"] = state.options_net

        # Compute market IV for display
        if self.current_strike:
            iv = self.get_implied_vol_at_strike(state, self.current_strike)
            result["market_iv"] = iv * 100 if iv > 0 else None

        # Compute edge for display
        if vol and result.get("market_iv"):
            result["edge"] = (vol * 100) - result["market_iv"]

        # ===== Phase: UNWINDING =====
        if tick >= UNWIND_START_TICK:
            if self.phase != Phase.UNWINDING:
                self.phase = Phase.UNWINDING
                self.current_n = 0
                self.current_direction = 0
                self.api.cancel_all_orders()
                logger.info("ENTERING UNWIND at tick %d", tick)

            trades = self.unwind_positions(state, tick)
            result["option_trades"] = trades
            result["phase"] = self.phase
            return result

        # ===== Phase: CLOSING (flattening old position) =====
        if self.phase == Phase.CLOSING:
            self.close_ticks += 1
            if self.is_flat(state):
                logger.info("Position flat. Ready to reposition.")
                self.phase = Phase.POSITIONING
                self.position_ticks = 0
                self.current_n = 0
                self.current_direction = 0
                self.current_strike = None
            elif self.close_ticks > 5:
                # Force: try closing again
                trades = self.close_all_positions(state)
                result["option_trades"] = trades
                if self.close_ticks > 10:
                    # Give up - go IDLE, do NOT build new position on top of old one
                    logger.warning("Could not fully flatten after 10 ticks, going IDLE (not repositioning)")
                    self.phase = Phase.IDLE
                    week = self.get_current_week(tick)
                    self.weeks_positioned.add(week)
                    self.current_n = 0
                    self.current_direction = 0
                    self.current_strike = None
            result["phase"] = self.phase
            return result

        # ===== Phase: POSITIONING (building new straddle) =====
        if self.phase == Phase.POSITIONING:
            self.position_ticks += 1
            if self.current_n == 0:
                # Use saved optimizer result if available (from before close phase)
                if self.pending_n > 0 and self.pending_direction != 0:
                    n_star = self.pending_n
                    direction = self.pending_direction
                    f_star = self.pending_profit
                    strike = self.pending_strike
                    logger.info("Using saved optimizer result: n=%d %s @ K=%.0f",
                                n_star, "LONG" if direction > 0 else "SHORT", strike)
                    # Clear pending
                    self.pending_n = 0
                    self.pending_direction = 0
                    self.pending_strike = None
                    self.pending_profit = 0.0
                else:
                    # Fresh optimizer run (first position of sub-heat, or from IDLE)
                    n_star, direction, f_star, strike = self.run_optimizer(state, tick)

                if n_star > 0 and direction != 0:
                    self.current_n = n_star
                    self.current_direction = direction
                    self.current_strike = strike
                    self.expected_profit = f_star
                    result["n_straddles"] = n_star
                    result["direction"] = direction
                    result["strike"] = strike
                    result["expected_pnl"] = f_star

                    # Execute the straddle position
                    trades = self.take_straddle_position(n_star, direction, strike, state)
                    result["option_trades"] = trades
                    self.phase = Phase.HOLDING
                    week = self.get_current_week(tick)
                    self.weeks_positioned.add(week)
                    logger.info("POSITIONED: %d %s straddles @ %.0f (week %d, expected $%.0f)",
                                n_star, "LONG" if direction > 0 else "SHORT",
                                strike, week, f_star)
                else:
                    # No profitable trade found
                    logger.info("No profitable trade for this week (n*=0)")
                    self.phase = Phase.IDLE
                    week = self.get_current_week(tick)
                    self.weeks_positioned.add(week)
            result["phase"] = self.phase
            return result

        # ===== Check if it's a positioning tick =====
        if self.is_position_tick(tick) and vol:
            week = self.get_current_week(tick)
            logger.info("WEEK %d positioning tick %d", week, tick)

            if self.phase == Phase.HOLDING and self.current_n > 0:
                # SMART REPOSITIONING: check if we need to change
                n_new, dir_new, f_new, strike_new = self.run_optimizer(state, tick)

                # Skip repositioning if direction unchanged and strike within $3
                same_direction = (dir_new == self.current_direction)
                same_strike = (self.current_strike is not None and
                               abs(strike_new - self.current_strike) <= 3.0)

                if same_direction and same_strike and n_new > 0:
                    logger.info("HOLD THROUGH week %d: same direction=%s strike=%.0f "
                                "(saved $%.0f in repositioning costs)",
                                week,
                                "LONG" if dir_new > 0 else "SHORT",
                                self.current_strike,
                                self.current_n * 8.0)
                    self.weeks_positioned.add(week)
                    self.expected_profit += f_new
                    result["expected_pnl"] = self.expected_profit
                    result["phase"] = self.phase
                    hedges = self.delta_hedge(state, tick)
                    result["hedge_trades"] = hedges
                    return result

                # Reposition cost threshold: only reposition if new expected profit
                # exceeds the cost of closing + reopening (~$8/contract * n)
                reposition_cost = self.current_n * 8.0 + (n_new * 4.0 if n_new > 0 else 0)
                if f_new <= reposition_cost and n_new > 0:
                    logger.info("HOLD (reposition not worth it): f_new=$%.0f <= cost=$%.0f",
                                f_new, reposition_cost)
                    self.weeks_positioned.add(week)
                    result["phase"] = self.phase
                    hedges = self.delta_hedge(state, tick)
                    result["hedge_trades"] = hedges
                    return result

                if n_new <= 0 or dir_new == 0:
                    # New optimizer says unprofitable - keep holding old position
                    logger.info("HOLD (new optimizer unprofitable, keeping current position)")
                    self.weeks_positioned.add(week)
                    result["phase"] = self.phase
                    hedges = self.delta_hedge(state, tick)
                    result["hedge_trades"] = hedges
                    return result

                # Direction changed significantly - must reposition
                # Save optimizer result to use AFTER close (market moves during close)
                logger.info("REPOSITIONING: dir %d->%d, strike %s->%.0f (f=$%.0f)",
                            self.current_direction, dir_new,
                            self.current_strike, strike_new, f_new)
                self.pending_n = n_new
                self.pending_direction = dir_new
                self.pending_strike = strike_new
                self.pending_profit = f_new
                self.phase = Phase.CLOSING
                self.close_ticks = 0
                self.close_cancel_phase = True
                trades = self.close_all_positions(state)
                result["option_trades"] = trades
                result["phase"] = self.phase
                return result
            else:
                # No old position, go straight to positioning
                self.phase = Phase.POSITIONING
                self.position_ticks = 0
                self.current_n = 0
                self.current_direction = 0
                self.current_strike = None
                # Will run optimizer next cycle

        # ===== Phase: HOLDING (just delta hedge) =====
        if self.phase == Phase.HOLDING:
            hedges = self.delta_hedge(state, tick)
            result["hedge_trades"] = hedges

        # ===== Phase: IDLE (waiting) =====
        # Nothing to do, wait for positioning tick or news

        result["phase"] = self.phase
        return result

    # ========================================================================
    # Display helpers
    # ========================================================================

    def print_position_summary(self, state: MarketState):
        """Print current position details."""
        if not state.options:
            return

        print(f"\n{'Ticker':<10} {'Type':<5} {'K':>3} {'Bid':>7} {'Ask':>7} "
              f"{'IV':>6} {'Pos':>5}")
        print("-" * 50)

        for opt in sorted(state.options, key=lambda o: (o.strike, o.option_type)):
            if opt.position == 0:
                continue
            iv_str = f"{opt.iv*100:.1f}%" if opt.iv > 0 else "  -  "
            print(f"{opt.ticker:<10} {opt.option_type:<5} {opt.strike:>3.0f} "
                  f"{opt.bid:>7.3f} {opt.ask:>7.3f} {iv_str:>6} "
                  f"{int(opt.position):>5}")

        print(f"\n  RTM position: {state.stock_position:>+6d}")
        print(f"  Total delta:  {state.total_delta:>+8.0f}")
        print(f"  Total gamma:  {state.total_gamma:>+8.1f}")
        print(f"  Options gross/net: {state.options_gross}/{state.options_net}")
