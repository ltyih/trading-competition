"""
V10 Trading Engine - Liam's Optimal Straddle Method.

Weekly straddle positioning with mathematically optimal sizing.
Each week:
  1. Close old position
  2. Run optimizer to find n* straddles at ATM strike
  3. Execute position
  4. Delta hedge continuously while holding
  5. Repeat at next week boundary

Delta hedging: high-frequency hedge-only cycles between simulator ticks.
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
    MIN_OPTION_PRICE, HEDGE_TARGET_DELTA,
    HEDGE_MAX_SHARES_PER_HEDGE, MIN_HEDGE_SIZE, POSITION_TICKS,
    WEEKLY_CLOSE_START_TICKS, WEEKLY_CLOSE_DEADLINE_TICKS,
    MAX_STRADDLES, FORCED_N_VOL_GAP_THRESHOLD,
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
        if tick < POSITION_TICKS[0]:
            return 1
        for i, pt in enumerate(POSITION_TICKS):
            next_pt = POSITION_TICKS[i + 1] if i + 1 < len(POSITION_TICKS) else TICKS_PER_SUBHEAT + 1
            if pt <= tick < next_pt:
                return i + 1
        return 4

    def is_position_tick(self, tick: int) -> bool:
        """Should we take a new position at this tick?
        Strict schedule on POSITION_TICKS, with week-1 catch-up if tick 1 was missed."""
        week = self.get_current_week(tick)
        if week in self.weeks_positioned:
            return False
        if tick in POSITION_TICKS:
            return True

        # Catch-up: if algo starts after tick 1, allow week-1 positioning on
        # subsequent ticks until week 2 boundary.
        first_tick = POSITION_TICKS[0]
        next_week_tick = POSITION_TICKS[1] if len(POSITION_TICKS) > 1 else (TICKS_PER_SUBHEAT + 1)
        if week == 1 and first_tick < tick < next_week_tick:
            return True

        return False

    def get_weekly_close_window(self, tick: int) -> Optional[Tuple[int, int, int]]:
        """Return (week, start_tick, deadline_tick) for scheduled close windows."""
        for i, (start_tick, deadline_tick) in enumerate(
            zip(WEEKLY_CLOSE_START_TICKS, WEEKLY_CLOSE_DEADLINE_TICKS), start=1
        ):
            if start_tick <= tick <= deadline_tick:
                return i, start_tick, deadline_tick
        return None

    # ========================================================================
    # Market data
    # ========================================================================

    def update_news(self) -> bool:
        news = self.api.get_news(since=self.vol_state.last_news_id)
        if news:
            return self.vol_state.process_news(news)
        return False

    def get_market_state(self, tick: int, compute_iv: bool = True) -> Optional[MarketState]:
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

                # Compute IV from best available market price when needed.
                if compute_iv:
                    iv_price = mid
                    if iv_price <= 0:
                        last = sec.get("last", 0) or 0
                        if last > 0:
                            iv_price = last
                        elif ask > 0:
                            iv_price = ask
                        elif bid > 0:
                            iv_price = bid
                    if iv_price > 0 and state.spot > 0 and T_exp > 0:
                        opt.iv = implied_volatility(iv_price, state.spot, strike,
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

    def has_any_implied_vol(self, state: MarketState) -> bool:
        """Whether current snapshot has at least one usable implied volatility value."""
        for opt in state.options:
            if opt.iv > 0:
                return True
        return False

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

        # Conservative-override guard: force max sizing for large IV-RV dislocations.
        vol_gap = abs(sigma_hat - sigma)
        if vol_gap > FORCED_N_VOL_GAP_THRESHOLD:
            forced_n = MAX_STRADDLES
            forced_direction = 1 if sigma > sigma_hat else -1
            logger.info(
                "FORCED SIZE: |IV-RV|=%.2f%% > %.2f%% => n=%d %s @ K=%.0f",
                vol_gap * 100.0,
                FORCED_N_VOL_GAP_THRESHOLD * 100.0,
                forced_n,
                "LONG" if forced_direction > 0 else "SHORT",
                X,
            )
            return forced_n, forced_direction, 0.0, X

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
        Uses cancel-then-close phasing to prevent oscillation."""
        orders = 0
        option_qty_closed = 0
        stock_qty_closed = 0

        # Cancel all open orders first
        self.api.cancel_all_orders()

        # If in cancel phase, just cancel and wait one cycle for cancels to settle
        if self.close_cancel_phase:
            self.close_cancel_phase = False
            logger.info("CLOSE: cancel phase - waiting for cancels to settle")
            return 0

        stock_remaining = abs(state.stock_position)
        reserve_stock_slot = stock_remaining > 0
        option_order_cap = MAX_ORDERS_PER_CYCLE - (1 if reserve_stock_slot else 0)

        # Close options first, while reserving one stock order slot when stock exists.
        for opt in state.options:
            if abs(opt.position) == 0:
                continue
            action = "SELL" if opt.position > 0 else "BUY"
            remaining = abs(opt.position)
            while remaining > 0 and orders < option_order_cap:
                qty = min(remaining, OPTIONS_MAX_TRADE_SIZE)
                result = self.api.submit_market_order(opt.ticker, qty, action)
                if result:
                    orders += 1
                    remaining -= qty
                    option_qty_closed += qty
                else:
                    break

        # Close stock in the same close cycle.
        if stock_remaining > 0:
            action = "SELL" if state.stock_position > 0 else "BUY"
            while stock_remaining > 0 and orders < MAX_ORDERS_PER_CYCLE:
                qty = min(stock_remaining, RTM_MAX_TRADE_SIZE)
                result = self.api.submit_market_order(UNDERLYING_TICKER, qty, action)
                if result:
                    self.rtm_volume += qty
                    orders += 1
                    stock_remaining -= qty
                    stock_qty_closed += qty
                else:
                    break

        if reserve_stock_slot and stock_qty_closed == 0 and abs(state.stock_position) > 0:
            logger.info("CLOSE: stock unwind deferred this cycle due order cap")
        if option_qty_closed > 0 or stock_qty_closed > 0:
            logger.info("CLOSE: options_closed=%d stock_closed=%d orders=%d",
                        option_qty_closed, stock_qty_closed, orders)

        return orders

    def _run_scheduled_close_step(self, state: MarketState, tick: int,
                                  deadline_tick: int) -> int:
        """Gradually close positions to be flat by deadline tick."""
        orders = 0
        option_qty_closed = 0
        stock_qty_closed = 0
        ticks_left = max(deadline_tick - tick + 1, 1)
        force_full_close = (tick >= deadline_tick)
        stock_position = int(state.stock_position)
        stock_remaining = abs(stock_position)
        reserve_stock_slot = stock_remaining > 0
        option_order_cap = MAX_ORDERS_PER_CYCLE - (1 if reserve_stock_slot else 0)

        # Close options first.
        for opt in state.options:
            pos = int(opt.position)
            if abs(pos) == 0:
                continue
            if force_full_close:
                qty_target = abs(pos)
            else:
                qty_target = int(math.ceil(abs(pos) / ticks_left))
            qty_target = min(qty_target, abs(pos))
            if qty_target <= 0:
                continue

            action = "SELL" if pos > 0 else "BUY"
            remaining = qty_target
            while remaining > 0 and orders < option_order_cap:
                qty = min(remaining, OPTIONS_MAX_TRADE_SIZE)
                result = self.api.submit_market_order(opt.ticker, qty, action)
                if not result:
                    break
                orders += 1
                remaining -= qty
                option_qty_closed += qty

        # Close stock every close tick (reserve one order slot when stock exists).
        if stock_remaining > 0:
            if force_full_close:
                stock_target = stock_remaining
            else:
                stock_target = int(math.ceil(stock_remaining / ticks_left))
                stock_target = min(stock_target, stock_remaining)
            action = "SELL" if stock_position > 0 else "BUY"
            qty = min(stock_target, RTM_MAX_TRADE_SIZE)
            if qty > 0 and orders < MAX_ORDERS_PER_CYCLE:
                result = self.api.submit_market_order(UNDERLYING_TICKER, qty, action)
                if result:
                    self.rtm_volume += qty
                    orders += 1
                    stock_qty_closed += qty

        if reserve_stock_slot and stock_qty_closed == 0 and stock_remaining > 0:
            logger.info("SCHEDULED CLOSE: stock unwind deferred due order cap at tick %d", tick)
        if option_qty_closed > 0 or stock_qty_closed > 0:
            logger.info("SCHEDULED CLOSE tick %d: options_closed=%d stock_closed=%d orders=%d",
                        tick, option_qty_closed, stock_qty_closed, orders)

        return orders

    def _submit_leg_orders(self, ticker: str, qty_total: int, action: str,
                           running_gross: int, running_net: int,
                           running_positions: Dict[str, int]) -> tuple:
        """Submit orders for one leg (call or put). Returns (orders_sent, qty_filled, gross, net)."""
        orders = 0
        filled = 0
        g = running_gross
        n = running_net
        remaining = qty_total

        while remaining > 0 and orders < MAX_ORDERS_PER_CYCLE:
            qty = min(remaining, OPTIONS_MAX_TRADE_SIZE)

            # Pre-flight limit check using per-ticker position impact.
            current_pos = int(running_positions.get(ticker, 0))
            new_pos = current_pos + qty if action == "BUY" else current_pos - qty
            new_gross = g + (abs(new_pos) - abs(current_pos))
            new_net = n + qty if action == "BUY" else n - qty
            if new_gross > OPTIONS_GROSS_LIMIT:
                logger.warning("Gross limit approaching (%d), stopping", new_gross)
                break
            if abs(new_net) > OPTIONS_NET_LIMIT:
                logger.warning("Net limit approaching (%d), stopping", new_net)
                break

            result = self.api.submit_market_order(ticker, qty, action)
            if result:
                orders += 1
                filled += qty
                remaining -= qty
                g = new_gross
                n = new_net
                running_positions[ticker] = new_pos
            else:
                break

        return orders, filled, g, n

    def _submit_paired_orders(self, call_ticker: str, put_ticker: str,
                              qty_total: int, action: str,
                              running_gross: int, running_net: int,
                              running_positions: Dict[str, int],
                              label: str) -> tuple:
        """Submit matched call/put legs. Returns (orders, paired, calls, puts, gross, net)."""
        orders = 0
        calls = 0
        puts = 0
        g = running_gross
        n = running_net
        remaining = qty_total
        batch_size = OPTIONS_MAX_TRADE_SIZE

        while remaining > 0 and orders < MAX_ORDERS_PER_CYCLE:
            qty = min(remaining, batch_size)

            c_ord, c_fill, g, n = self._submit_leg_orders(
                call_ticker, qty, action, g, n, running_positions)
            orders += c_ord
            calls += c_fill

            if c_fill == 0:
                break

            p_ord, p_fill, g, n = self._submit_leg_orders(
                put_ticker, c_fill, action, g, n, running_positions)
            orders += p_ord
            puts += p_fill

            if p_fill < c_fill:
                unmatched = c_fill - p_fill
                reverse = "SELL" if action == "BUY" else "BUY"
                logger.warning("%s put leg short by %d, closing unmatched calls",
                               label, unmatched)
                r_ord, r_fill, g, n = self._submit_leg_orders(
                    call_ticker, unmatched, reverse, g, n, running_positions)
                orders += r_ord
                calls -= r_fill
                if r_fill < unmatched:
                    logger.warning("%s unmatched-call close incomplete: %d/%d",
                                   label, r_fill, unmatched)
                break

            remaining -= c_fill

        paired = min(calls, puts)
        return orders, paired, calls, puts, g, n

    def take_straddle_position(self, n: int, direction: int,
                               strike: float, state: MarketState) -> int:
        """Build ATM straddles, then hedge extra ATM with opposite extreme OTM legs."""
        if n <= 0 or direction == 0:
            return 0

        n = min(n, MAX_STRADDLES)
        action = "BUY" if direction > 0 else "SELL"
        opposite_action = "SELL" if action == "BUY" else "BUY"

        call_ticker = f"RTM1C{int(strike)}"
        put_ticker = f"RTM1P{int(strike)}"

        g = state.options_gross
        net = state.options_net
        total_orders = 0
        total_calls = 0
        total_puts = 0
        total_extreme_calls = 0
        total_extreme_puts = 0
        option_positions = {opt.ticker: int(opt.position) for opt in state.options}

        # Base capacity under net limit: each straddle contributes 2 option net.
        base_capacity = OPTIONS_NET_LIMIT // 2
        base_target = min(n, base_capacity)
        extra_target = max(0, n - base_target)

        ords, base_paired, c_fill, p_fill, g, net = self._submit_paired_orders(
            call_ticker, put_ticker, base_target, action, g, net, option_positions, "ATM")
        total_orders += ords
        total_calls += c_fill
        total_puts += p_fill

        if extra_target > 0:
            extreme_put_strike = 45
            extreme_call_strike = 54

            if extreme_call_strike not in STRIKE_PRICES or extreme_put_strike not in STRIKE_PRICES:
                logger.warning(
                    "Cannot add extreme overlay (P%d/C%d out of strike range)",
                    extreme_put_strike, extreme_call_strike
                )
            else:
                extreme_call_ticker = f"RTM1C{int(extreme_call_strike)}"
                extreme_put_ticker = f"RTM1P{int(extreme_put_strike)}"

                remaining_extra = extra_target
                while remaining_extra > 0:
                    qty = min(remaining_extra, OPTIONS_MAX_TRADE_SIZE)

                    # 1) Build opposite extreme OTM pair first to free net capacity.
                    w_ords, w_paired, w_c, w_p, g, net = self._submit_paired_orders(
                        extreme_call_ticker, extreme_put_ticker, qty,
                        opposite_action, g, net, option_positions, "EXTREME")
                    total_orders += w_ords
                    total_extreme_calls += w_c
                    total_extreme_puts += w_p

                    if w_paired == 0:
                        break

                    # 2) Add the matching ATM straddle quantity.
                    a_ords, a_paired, a_c, a_p, g, net = self._submit_paired_orders(
                        call_ticker, put_ticker, w_paired,
                        action, g, net, option_positions, "ATM-EXTRA")
                    total_orders += a_ords
                    total_calls += a_c
                    total_puts += a_p

                    if a_paired < w_paired:
                        unmatched = w_paired - a_paired
                        logger.warning("ATM extra short by %d, unwinding unmatched extreme legs", unmatched)
                        u_ords, _, u_c, u_p, g, net = self._submit_paired_orders(
                            extreme_call_ticker, extreme_put_ticker, unmatched,
                            action, g, net, option_positions, "EXTREME-UNWIND")
                        total_orders += u_ords
                        total_extreme_calls -= u_c
                        total_extreme_puts -= u_p
                        break

                    remaining_extra -= a_paired

        straddles_filled = min(total_calls, total_puts)
        logger.info(
            "STRADDLE %s %d/%d @ K=%.0f: %d orders | ATM %dC+%dP | EXTREME C54 %d + P45 %d",
            action, straddles_filled, n, strike,
            total_orders, total_calls, total_puts, total_extreme_calls, total_extreme_puts)
        return total_orders

    def _run_positioning_step(self, state: MarketState, tick: int, result: Dict) -> Dict:
        """Run one positioning step (optimizer + optional entry)."""
        self.position_ticks += 1
        if self.current_n == 0:
            # Always refresh news immediately before optimizing/entry so
            # positioning uses the latest realized-vol / delta-limit inputs.
            self.update_news()
            vol_now = self.vol_state.best_vol_estimate
            result["vol"] = (vol_now * 100) if vol_now else None
            result["delta_limit"] = self.vol_state.delta_limit
            if not vol_now or vol_now <= 0:
                logger.info("No realized vol available at positioning tick %d; skipping entry this tick", tick)
                self.phase = Phase.IDLE
                result["phase"] = self.phase
                return result

            # Refresh market snapshot right before entry and require IV data.
            pre_trade_state = self.get_market_state(tick)
            if pre_trade_state is None:
                result["phase"] = self.phase
                return result
            if not self.has_any_implied_vol(pre_trade_state):
                logger.info("No implied vol data at positioning tick %d; skipping entry this tick", tick)
                self.phase = Phase.IDLE
                result["phase"] = self.phase
                return result

            # Fresh optimizer run at entry time (do not rely on cached sizing).
            n_star, direction, f_star, strike = self.run_optimizer(pre_trade_state, tick)

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
                trades = self.take_straddle_position(n_star, direction, strike, pre_trade_state)
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

    # ========================================================================
    # Delta hedging
    # ========================================================================

    def execute_hedge_cycle(self, tick: int) -> Dict:
        """Run hedge-only cycle between simulator ticks."""
        result = {
            "tick": tick,
            "phase": self.phase,
            "delta": 0.0,
            "spot": 0.0,
            "hedge_trades": 0,
        }

        if self.phase != Phase.HOLDING:
            return result

        state = self.get_market_state(tick, compute_iv=False)
        if state is None:
            return result

        result["delta"] = state.total_delta
        result["spot"] = state.spot
        result["hedge_trades"] = self.delta_hedge(state, tick)
        return result

    def delta_hedge(self, state: MarketState, tick: int) -> int:
        """
        Delta hedging policy:
        - Long position: continuous re-hedging when outside target band.
        - Short position: minimal hedging, only when delta limit is breached.
        """
        if self.phase == Phase.CLOSING:
            return 0

        L = self.vol_state.delta_limit
        if not L or L <= 0:
            L = 10000.0

        current_delta = state.total_delta
        abs_delta = abs(current_delta)

        short_mode = (self.current_direction < 0 and self.current_n > 0)
        if short_mode:
            # Minimize hedging for short option books: only act on limit breach.
            if abs_delta < L:
                return 0
            hedge_mode = "limit"
        else:
            hedge_mode = "continuous"

        # Hedge toward target absolute delta: sign(delta) * (target_pct * limit)
        target_pct = float(HEDGE_TARGET_DELTA)
        target_pct = max(0.0, min(1.0, target_pct))
        target_abs = L * target_pct
        target_delta = math.copysign(target_abs, current_delta)

        # If already at/below target, no hedge needed.
        if abs_delta <= target_abs:
            return 0

        hedge_needed = target_delta - current_delta
        hedge_total = abs(int(round(hedge_needed)))

        if hedge_total < MIN_HEDGE_SIZE:
            return 0

        # Check stock position limits before hedging
        action = "BUY" if hedge_needed > 0 else "SELL"
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
        hedge_cap = max(int(HEDGE_MAX_SHARES_PER_HEDGE), 0)
        hedge_qty = min(hedge_total, RTM_MAX_TRADE_SIZE, hedge_cap)
        if hedge_qty < MIN_HEDGE_SIZE:
            return 0
        if hedge_qty < hedge_total:
            logger.info("Hedge qty capped: requested %d, capped %d", hedge_total, hedge_qty)

        # Cancel pending stock orders to prevent accumulation, only when submitting.
        self.api.cancel_orders_for_ticker(UNDERLYING_TICKER)
        result = self.api.submit_market_order(UNDERLYING_TICKER, hedge_qty, action)
        orders_sent = 0
        if result:
            orders_sent = 1
            self.rtm_volume += hedge_qty

        if orders_sent > 0:
            self.last_hedge_tick = tick
            logger.info(
                        "HEDGE %s %d RTM | delta %.0f -> ~%.0f | target %.0f (%.0f%% of limit %.0f) "
                        "| mode %s",
                        action, hedge_qty, current_delta,
                        current_delta + (hedge_qty if action == "BUY" else -hedge_qty),
                        target_delta, target_pct * 100, L,
                        hedge_mode)
        return orders_sent

    # ========================================================================
    # Check if position is flat
    # ========================================================================

    def is_flat(self, state: MarketState) -> bool:
        """Are all positions closed?"""
        if int(state.stock_position) != 0:
            return False
        for opt in state.options:
            if int(opt.position) != 0:
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

        # ===== Scheduled weekly closing (weeks 1-4) =====
        close_window = self.get_weekly_close_window(tick)
        if close_window:
            close_week, start_tick, deadline_tick = close_window
            self.phase = Phase.CLOSING
            trades = self._run_scheduled_close_step(state, tick, deadline_tick)
            result["option_trades"] = trades
            if self.is_flat(state):
                logger.info("Scheduled close complete week %d by tick %d",
                            close_week, tick)
                self.phase = Phase.IDLE
                self.current_n = 0
                self.current_direction = 0
                self.current_strike = None
                self.expected_profit = 0.0
            else:
                logger.info("Scheduled close week %d tick %d (window %d-%d), trades=%d",
                            close_week, tick, start_tick, deadline_tick, trades)
            result["phase"] = self.phase
            return result

        # ===== Phase: CLOSING (flattening old position) =====
        if self.phase == Phase.CLOSING:
            self.close_ticks += 1
            if self.is_flat(state):
                logger.info("Position flat. Waiting for next scheduled positioning tick.")
                self.phase = Phase.IDLE
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
            if not self.is_position_tick(tick):
                logger.info("Deferring positioning at tick %d (not in POSITION_TICKS)", tick)
                self.phase = Phase.IDLE
                result["phase"] = self.phase
                return result
            return self._run_positioning_step(state, tick, result)

        # ===== Check if it's a positioning tick =====
        if self.is_position_tick(tick):
            week = self.get_current_week(tick)
            logger.info("WEEK %d positioning tick %d", week, tick)

            # Safety: do not open a new week on top of residual old positions.
            if tick in POSITION_TICKS[1:] and not self.is_flat(state):
                logger.warning("Boundary tick %d but portfolio not flat; continuing close-first",
                               tick)
                self.phase = Phase.CLOSING
                self.close_ticks = 0
                self.close_cancel_phase = True
                trades = self.close_all_positions(state)
                result["option_trades"] = trades
                result["phase"] = self.phase
                return result

            if self.phase == Phase.HOLDING and self.current_n > 0:
                # Weekly rollover policy: only roll if ATM strike has changed.
                atm_now = self.get_best_strike(state.spot)
                strike_changed = (self.current_strike is None or atm_now != self.current_strike)

                if not strike_changed:
                    logger.info("HOLD week %d: ATM unchanged at K=%.0f, keeping current straddle",
                                week, atm_now)
                    self.weeks_positioned.add(week)
                    result["phase"] = self.phase
                    hedges = self.delta_hedge(state, tick)
                    result["hedge_trades"] = hedges
                    return result

                logger.info("FORCED WEEKLY ROLL week %d tick %d: strike %.0f -> ATM %.0f",
                            week, tick,
                            self.current_strike if self.current_strike is not None else -1,
                            atm_now)
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
                logger.info("Immediate positioning at week %d tick %d", week, tick)
                return self._run_positioning_step(state, tick, result)

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



