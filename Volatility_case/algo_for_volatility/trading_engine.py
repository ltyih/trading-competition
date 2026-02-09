"""
V8.1 Trading Engine - Adaptive Aggression.

Key changes from V8:
- ADAPTIVE position sizing: delta_limit/10 for high limits, /13 for low
- ADAPTIVE hedge bands: trigger/target scale with gamma-to-limit ratio
- 5 strikes for max capacity
- Build position FAST, hold, hedge only
"""

import logging
import math
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from config import (
    UNDERLYING_TICKER, STRIKE_PRICES, OPTIONS_MULTIPLIER,
    RISK_FREE_RATE, TICKS_PER_SUBHEAT, TRADING_DAYS_PER_MONTH,
    TRADING_DAYS_PER_YEAR, VOL_EDGE_THRESHOLD,
    MAX_OPTION_ORDERS_PER_CYCLE, TARGET_NET_POSITION,
    FULL_EDGE_THRESHOLD, RTM_MAX_TRADE_SIZE,
    OPTIONS_MAX_TRADE_SIZE, UNWIND_START_TICK,
    OPTIONS_GROSS_LIMIT, OPTIONS_NET_LIMIT, MIN_OPTION_PRICE,
    HEDGE_TRIGGER_PCT, HEDGE_TARGET_PCT, MIN_HEDGE_SIZE,
    HEDGE_COOLDOWN_TICKS, NUM_STRIKES,
    NO_REVERSAL_AFTER_TICK, MIN_REVERSAL_EDGE, REVERSAL_COOLDOWN_TICKS,
)
from black_scholes import (
    bs_price, bs_delta, bs_gamma, bs_vega, implied_volatility,
)
from news_parser import VolatilityState
from rit_api import RITApi

logger = logging.getLogger(__name__)


@dataclass
class OptionData:
    ticker: str
    option_type: str
    strike: float
    bid: float
    ask: float
    mid: float
    position: int
    bs_fair_price: float = 0.0
    market_iv: float = 0.0
    bs_delta: float = 0.0
    bs_gamma: float = 0.0
    bs_vega: float = 0.0
    mispricing: float = 0.0
    mispricing_pct: float = 0.0


@dataclass
class PortfolioState:
    underlying_position: int = 0
    underlying_price: float = 0.0
    underlying_bid: float = 0.0
    underlying_ask: float = 0.0
    options: List[OptionData] = field(default_factory=list)
    total_delta: float = 0.0
    total_gamma: float = 0.0
    total_vega: float = 0.0
    options_gross: int = 0
    options_net: int = 0
    avg_market_iv: float = 0.0


class TradingEngine:
    """V8 Trading Engine - Maximum aggression, controlled risk."""

    def __init__(self, api: RITApi):
        self.api = api
        self.vol_state = VolatilityState()
        self.last_tick = 0
        self.last_direction = 0
        self.direction_changes = 0
        self.last_reversal_tick = -100
        self.position_built = False
        self.rtm_volume = 0
        # Hedge cooldown tracking
        self.last_hedge_tick = -100
        # Track if we're in unwind phase
        self.unwinding = False

    def get_time_to_expiry(self, tick: int) -> float:
        ticks_remaining = max(TICKS_PER_SUBHEAT - tick, 1)
        days_remaining = (ticks_remaining / TICKS_PER_SUBHEAT) * TRADING_DAYS_PER_MONTH
        return days_remaining / TRADING_DAYS_PER_YEAR

    def update_news(self) -> bool:
        news = self.api.get_news(since=self.vol_state.last_news_id)
        if news:
            return self.vol_state.process_news(news)
        return False

    def get_portfolio_state(self, tick: int) -> Optional[PortfolioState]:
        securities = self.api.get_securities()
        if not securities:
            return None

        state = PortfolioState()
        analyst_vol = self.vol_state.best_vol_estimate
        T = self.get_time_to_expiry(tick)

        for sec in securities:
            ticker = sec.get("ticker", "")
            position = int(sec.get("position", 0) or 0)
            bid = sec.get("bid", 0) or 0
            ask = sec.get("ask", 0) or 0
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0

            if ticker == UNDERLYING_TICKER:
                state.underlying_position = position
                state.underlying_price = mid if mid > 0 else (sec.get("last", 0) or 0)
                state.underlying_bid = bid
                state.underlying_ask = ask
                continue

            if "C" in ticker and ticker.startswith("RTM"):
                option_type = "CALL"
            elif "P" in ticker and ticker.startswith("RTM"):
                option_type = "PUT"
            else:
                continue

            try:
                strike = float(ticker[-2:])
            except ValueError:
                continue
            if strike not in STRIKE_PRICES:
                continue

            opt = OptionData(
                ticker=ticker, option_type=option_type, strike=strike,
                bid=bid, ask=ask, mid=mid, position=position,
            )

            S = state.underlying_price
            if analyst_vol and analyst_vol > 0 and S > 0 and T > 0:
                opt.bs_fair_price = bs_price(S, strike, T, RISK_FREE_RATE, analyst_vol, option_type)
                opt.bs_delta = bs_delta(S, strike, T, RISK_FREE_RATE, analyst_vol, option_type)
                opt.bs_gamma = bs_gamma(S, strike, T, RISK_FREE_RATE, analyst_vol)
                opt.bs_vega = bs_vega(S, strike, T, RISK_FREE_RATE, analyst_vol)

                if mid > 0:
                    opt.market_iv = implied_volatility(mid, S, strike, T, RISK_FREE_RATE, option_type)
                    opt.mispricing = opt.bs_fair_price - mid
                    opt.mispricing_pct = opt.mispricing / mid if mid > 0 else 0

            if position != 0 and S > 0 and T > 0 and analyst_vol and analyst_vol > 0:
                d = bs_delta(S, strike, T, RISK_FREE_RATE, analyst_vol, option_type)
                g = bs_gamma(S, strike, T, RISK_FREE_RATE, analyst_vol)
                v = bs_vega(S, strike, T, RISK_FREE_RATE, analyst_vol)
                state.total_delta += position * d * OPTIONS_MULTIPLIER
                state.total_gamma += position * g * OPTIONS_MULTIPLIER
                state.total_vega += position * v * OPTIONS_MULTIPLIER

            state.options_gross += abs(position)
            state.options_net += position
            state.options.append(opt)

        state.total_delta += state.underlying_position

        # Vega-weighted avg market IV
        total_vega_weight = 0.0
        weighted_iv_sum = 0.0
        for opt in state.options:
            if opt.market_iv > 0 and opt.bs_vega > 0:
                w = abs(opt.bs_vega)
                weighted_iv_sum += opt.market_iv * w
                total_vega_weight += w
        if total_vega_weight > 0:
            state.avg_market_iv = weighted_iv_sum / total_vega_weight

        return state

    def get_vol_direction(self, state: PortfolioState, tick: int) -> Tuple[int, float]:
        """Determine long/short vol direction with strict reversal protection."""
        analyst_vol = self.vol_state.best_vol_estimate
        if not analyst_vol or analyst_vol <= 0:
            return 0, 0.0

        market_iv = state.avg_market_iv
        if market_iv <= 0:
            return 0, 0.0

        edge = analyst_vol - market_iv
        abs_edge = abs(edge)
        new_direction = 1 if edge > 0 else -1

        # No direction yet
        if self.last_direction == 0:
            if abs_edge < VOL_EDGE_THRESHOLD:
                return 0, abs_edge
            return new_direction, abs_edge

        # Same direction - keep it
        if new_direction == self.last_direction:
            return new_direction, abs_edge

        # DIFFERENT direction - strict reversal protection
        if tick > NO_REVERSAL_AFTER_TICK:
            logger.info("BLOCKED reversal at tick %d (after %d)", tick, NO_REVERSAL_AFTER_TICK)
            return self.last_direction, abs_edge

        ticks_since_reversal = tick - self.last_reversal_tick
        if ticks_since_reversal < REVERSAL_COOLDOWN_TICKS:
            return self.last_direction, abs_edge

        if abs_edge < MIN_REVERSAL_EDGE:
            return self.last_direction, abs_edge

        # Genuine reversal approved
        logger.info("REVERSAL APPROVED at tick %d: %d -> %d (edge=%.1f%%)",
                    tick, self.last_direction, new_direction, abs_edge * 100)
        return new_direction, abs_edge

    def get_best_strikes(self, spot: float) -> List[float]:
        """Get the N strikes closest to ATM, sorted by distance."""
        sorted_strikes = sorted(STRIKE_PRICES, key=lambda k: abs(k - spot))
        return sorted_strikes[:NUM_STRIKES]

    def calculate_targets(self, state: PortfolioState, direction: int,
                          edge: float) -> Dict[str, int]:
        """
        Calculate target positions. Key change from V6:
        - Focus on NUM_STRIKES closest to ATM
        - Weight by vega
        - direction=1 (long vol) -> BUY options, direction=-1 (short vol) -> SELL options
        """
        if direction == 0:
            return {}

        S = state.underlying_price
        if S <= 0:
            return {}

        # Scale position by edge strength
        target_scale = min(edge / FULL_EDGE_THRESHOLD, 1.0)
        target_scale = max(target_scale, 0.40)

        # V8.1: ADAPTIVE position sizing based on delta_limit.
        # Higher delta limits allow more aggressive sizing.
        # gamma_per_contract * 100 ≈ 9 delta/$1 at ATM.
        # Target: gamma/delta_limit ratio ≈ 75% per $1 move.
        # This means: max_contracts = delta_limit * 0.75 / 9 ≈ delta_limit / 12.
        # But for high limits (10k+), we can be more aggressive: delta_limit / 10.
        delta_limit = self.vol_state.delta_limit or 10000
        if delta_limit >= 10000:
            divisor = 10  # Aggressive for high limits
        elif delta_limit >= 7000:
            divisor = 11
        else:
            divisor = 13  # Conservative for low limits
        max_position_for_limit = int(delta_limit / divisor)
        max_position_for_limit = max(max_position_for_limit, 300)  # Floor at 300

        base_target = min(TARGET_NET_POSITION, max_position_for_limit)
        total_target = int(base_target * target_scale)
        logger.info("Position sizing: delta_limit=%.0f, max_for_limit=%d, base=%d, scaled=%d",
                    delta_limit, max_position_for_limit, base_target, total_target)

        # Get best strikes
        best_strikes = self.get_best_strikes(S)

        # Calculate vega weights for best strikes
        vega_list = []
        for opt in state.options:
            if opt.strike in best_strikes and opt.option_type == "CALL" and opt.bs_vega > 0:
                vega_list.append((opt.strike, opt.bs_vega))

        if not vega_list:
            return {}

        total_vega = sum(v for _, v in vega_list)
        if total_vega <= 0:
            return {}

        vega_weights = {s: v / total_vega for s, v in vega_list}

        targets = {}
        for opt in state.options:
            if opt.strike not in vega_weights:
                continue
            w = vega_weights[opt.strike]
            # Allocate proportionally by vega weight
            # NOTE: strike_target is the TARGET POSITION, not order size.
            # Order size is capped at OPTIONS_MAX_TRADE_SIZE (100) in build_position()
            strike_target = int(total_target * w / 2)  # /2 because each strike has call+put pair
            strike_target = max(strike_target, 0)
            targets[opt.ticker] = direction * strike_target

        return targets

    def build_position(self, state: PortfolioState, targets: Dict[str, int],
                       tick: int) -> int:
        """
        Build toward target positions using MARKET orders for speed.
        Track limits carefully to avoid rejections.
        """
        orders_sent = 0
        positions = {opt.ticker: opt.position for opt in state.options}
        running_gross = state.options_gross
        running_net = state.options_net

        # Build trade list
        trade_list = []
        for ticker, target in targets.items():
            current = positions.get(ticker, 0)
            gap = target - current
            if abs(gap) < 5:  # Close enough
                continue
            trade_list.append((ticker, current, target, gap))

        if not trade_list:
            # All positions within tolerance -> mark as built
            if state.options_gross > 20:
                self.position_built = True
            return 0

        # Sort by gap size (build biggest positions first)
        trade_list.sort(key=lambda x: abs(x[3]), reverse=True)

        for ticker, current, target, gap in trade_list:
            if orders_sent >= MAX_OPTION_ORDERS_PER_CYCLE:
                break

            action = "BUY" if gap > 0 else "SELL"
            remaining_gap = abs(gap)

            # Check price viability once
            opt = next((o for o in state.options if o.ticker == ticker), None)
            if opt:
                price_check = opt.ask if action == "BUY" else opt.bid
                if price_check < MIN_OPTION_PRICE:
                    continue

            # Submit multiple orders of up to 100 contracts each to fill the gap
            while remaining_gap >= 5 and orders_sent < MAX_OPTION_ORDERS_PER_CYCLE:
                qty = min(remaining_gap, OPTIONS_MAX_TRADE_SIZE)

                # Check limits BEFORE submitting
                if action == "BUY":
                    if running_gross + qty > OPTIONS_GROSS_LIMIT - 30:
                        qty = max(0, OPTIONS_GROSS_LIMIT - 30 - running_gross)
                    if running_net + qty > OPTIONS_NET_LIMIT - 20:
                        qty = min(qty, max(0, OPTIONS_NET_LIMIT - 20 - running_net))
                else:
                    if running_gross + qty > OPTIONS_GROSS_LIMIT - 30:
                        qty = max(0, OPTIONS_GROSS_LIMIT - 30 - running_gross)
                    if running_net - qty < -(OPTIONS_NET_LIMIT - 20):
                        qty = min(qty, max(0, running_net + OPTIONS_NET_LIMIT - 20))

                if qty < 5:
                    break

                result = self.api.submit_market_order(ticker, qty, action)
                if result:
                    orders_sent += 1
                    remaining_gap -= qty
                    if action == "BUY":
                        running_gross += qty
                        running_net += qty
                    else:
                        running_gross += qty
                        running_net -= qty
                else:
                    break  # API error, don't retry same ticker

        return orders_sent

    def delta_hedge(self, state: PortfolioState, tick: int) -> int:
        """
        V8.1 DELTA HEDGING - ADAPTIVE thresholds.

        Key insight: trigger/target should adapt to gamma-to-limit ratio.
        High gamma relative to limit → tighter trigger (hedge earlier).
        Low gamma → wider trigger (hedge less, save on commissions).
        """
        delta_limit = self.vol_state.delta_limit
        if delta_limit is None:
            delta_limit = 10000

        current_delta = state.total_delta
        abs_delta = abs(current_delta)

        # ADAPTIVE trigger based on gamma/limit ratio
        abs_gamma = abs(state.total_gamma)
        gamma_ratio = abs_gamma / delta_limit if delta_limit > 0 else 1.0

        if gamma_ratio > 1.0:
            # Very high gamma - tight trigger, moderate target
            trigger_pct = 0.65
            target_pct = 0.20
        elif gamma_ratio > 0.7:
            # Medium-high gamma
            trigger_pct = 0.75
            target_pct = 0.25
        else:
            # Low gamma - wide trigger, save commissions
            trigger_pct = 0.85
            target_pct = 0.35

        trigger_threshold = delta_limit * trigger_pct
        target_magnitude = delta_limit * target_pct

        # Check if delta is within acceptable band
        if abs_delta < trigger_threshold:
            return 0

        # Cooldown check - prevent oscillation
        ticks_since_hedge = tick - self.last_hedge_tick
        if ticks_since_hedge < HEDGE_COOLDOWN_TICKS:
            # Only bypass cooldown for truly extreme delta (>90% of limit)
            if abs_delta < delta_limit * 0.95:
                return 0
            logger.warning("BYPASSING cooldown: delta=%.0f exceeds 90%% of limit %.0f",
                          current_delta, delta_limit)

        # CRITICAL: Cancel ALL pending RTM orders first to prevent accumulation
        self.api.cancel_orders_for_ticker(UNDERLYING_TICKER)

        # Calculate hedge amount: bring delta to target_magnitude (same sign)
        if current_delta > 0:
            target_delta = target_magnitude
            hedge_needed = -(current_delta - target_delta)  # Negative = sell RTM
        else:
            target_delta = -target_magnitude
            hedge_needed = -(current_delta - target_delta)  # Positive = buy RTM

        hedge_qty = abs(int(round(hedge_needed)))
        hedge_qty = min(hedge_qty, RTM_MAX_TRADE_SIZE)

        if hedge_qty < MIN_HEDGE_SIZE:
            return 0

        action = "BUY" if hedge_needed > 0 else "SELL"

        # MARKET order only - no limit orders for hedging
        result = self.api.submit_market_order(UNDERLYING_TICKER, hedge_qty, action)
        if result:
            self.last_hedge_tick = tick
            self.rtm_volume += hedge_qty
            logger.info("HEDGE %s %d RTM (delta=%.0f -> target=%.0f, limit=%.0f)",
                        action, hedge_qty, current_delta, target_delta, delta_limit)
            return 1

        return 0

    def flatten_all_options(self, state: PortfolioState) -> int:
        """Flatten all option positions with market orders."""
        orders_sent = 0
        for opt in state.options:
            if opt.position == 0:
                continue
            action = "SELL" if opt.position > 0 else "BUY"
            qty = min(abs(opt.position), OPTIONS_MAX_TRADE_SIZE)
            if qty > 0:
                self.api.submit_market_order(opt.ticker, qty, action)
                orders_sent += 1
        return orders_sent

    def unwind_positions(self, state: PortfolioState, tick: int) -> int:
        """Gradually unwind all positions near end of period."""
        orders_sent = 0
        ticks_left = TICKS_PER_SUBHEAT - tick

        # Options: flatten gradually
        for opt in state.options:
            if opt.position == 0:
                continue
            action = "SELL" if opt.position > 0 else "BUY"
            abs_pos = abs(opt.position)

            if ticks_left <= 5:
                # Final ticks - dump everything with market orders
                qty = min(abs_pos, OPTIONS_MAX_TRADE_SIZE)
                self.api.submit_market_order(opt.ticker, qty, action)
                orders_sent += 1
            elif ticks_left <= 15:
                # Aggressive unwind
                qty = min(abs_pos, OPTIONS_MAX_TRADE_SIZE)
                if opt.bid > MIN_OPTION_PRICE or action == "BUY":
                    self.api.submit_market_order(opt.ticker, qty, action)
                    orders_sent += 1
            else:
                # Gradual unwind - use limit orders for better price
                qty = min(abs_pos, OPTIONS_MAX_TRADE_SIZE)
                if opt.mid > MIN_OPTION_PRICE and opt.bid > 0 and opt.ask > 0:
                    if action == "SELL":
                        price = round(max(opt.mid - 0.01, opt.bid), 2)
                        self.api.submit_limit_order(opt.ticker, qty, action, price)
                    else:
                        price = round(min(opt.mid + 0.01, opt.ask), 2)
                        self.api.submit_limit_order(opt.ticker, qty, action, price)
                    orders_sent += 1

        # RTM: flatten
        if abs(state.underlying_position) > 100:
            action = "SELL" if state.underlying_position > 0 else "BUY"
            qty = min(abs(state.underlying_position), RTM_MAX_TRADE_SIZE)
            if ticks_left <= 8:
                self.api.submit_market_order(UNDERLYING_TICKER, qty, action)
            else:
                mid = state.underlying_price
                if mid > 0:
                    offset = 0.03
                    price = round(mid - offset if action == "SELL" else mid + offset, 2)
                    self.api.submit_limit_order(UNDERLYING_TICKER, qty, action, price)
            self.rtm_volume += qty
            orders_sent += 1

        return orders_sent

    def execute_cycle(self, tick: int) -> Dict:
        """Execute one trading cycle - clean, single-path logic."""
        self.last_tick = tick

        result = {
            "tick": tick,
            "vol": None,
            "market_iv": None,
            "direction": 0,
            "edge": 0.0,
            "delta": 0.0,
            "delta_limit": None,
            "option_trades": 0,
            "hedge_trades": 0,
            "unwind_trades": 0,
            "spot": 0.0,
            "gross": 0,
            "net": 0,
            "reversal": False,
            "built": self.position_built,
            "rtm_vol": self.rtm_volume,
        }

        # 1. Update news
        self.update_news()
        vol_est = self.vol_state.best_vol_estimate
        result["vol"] = (vol_est * 100) if vol_est else None
        result["delta_limit"] = self.vol_state.delta_limit

        # 2. Get portfolio state
        state = self.get_portfolio_state(tick)
        if state is None:
            return result

        result["spot"] = state.underlying_price
        result["delta"] = state.total_delta
        result["gross"] = state.options_gross
        result["net"] = state.options_net
        result["market_iv"] = state.avg_market_iv * 100 if state.avg_market_iv > 0 else None

        # 3. UNWIND PHASE
        if tick >= UNWIND_START_TICK:
            if not self.unwinding:
                self.unwinding = True
                # Cancel all open orders when entering unwind
                self.api.cancel_all_orders()
                logger.info("ENTERING UNWIND PHASE at tick %d", tick)

            unwind_count = self.unwind_positions(state, tick)
            result["unwind_trades"] = unwind_count
            return result

        # 4. Determine direction
        direction, edge = self.get_vol_direction(state, tick)
        result["direction"] = direction
        result["edge"] = edge * 100

        # 5. Handle reversal (flatten and rebuild)
        if (self.last_direction != 0 and direction != 0 and
                direction != self.last_direction):
            logger.info("VOL REVERSAL at tick %d: %d -> %d (edge=%.1f%%)",
                        tick, self.last_direction, direction, edge * 100)
            # Cancel everything
            self.api.cancel_all_orders()
            # Flatten options
            self.flatten_all_options(state)
            result["reversal"] = True
            self.direction_changes += 1
            self.last_reversal_tick = tick
            self.position_built = False
            self.last_direction = direction
            # Hedge the remaining delta after flattening
            state = self.get_portfolio_state(tick)
            if state:
                result["delta"] = state.total_delta
                result["gross"] = state.options_gross
                result["net"] = state.options_net
                hedges = self.delta_hedge(state, tick)
                result["hedge_trades"] = hedges
            return result

        if direction != 0:
            self.last_direction = direction

        # 6. If position built - ONLY delta hedge, never touch options
        if self.position_built:
            hedges = self.delta_hedge(state, tick)
            result["hedge_trades"] = hedges
            result["built"] = True
            return result

        # 7. Build position toward targets
        if direction != 0:
            targets = self.calculate_targets(state, direction, edge)
            trades = self.build_position(state, targets, tick)
            result["option_trades"] = trades

            if trades == 0 and state.options_gross > 20:
                self.position_built = True
                result["built"] = True
                logger.info("POSITION BUILT at tick %d: gross=%d net=%d",
                           tick, state.options_gross, state.options_net)

        # 8. Delta hedge AFTER option trades (single path only)
        # Re-fetch state to get accurate delta after option fills
        state = self.get_portfolio_state(tick)
        if state:
            result["delta"] = state.total_delta
            result["gross"] = state.options_gross
            result["net"] = state.options_net
            result["built"] = self.position_built
            hedges = self.delta_hedge(state, tick)
            result["hedge_trades"] = hedges

        return result

    def print_options_table(self, state: PortfolioState):
        if not state.options:
            return

        print(f"\n{'Ticker':<10} {'Type':<5} {'K':>3} {'Bid':>7} {'Ask':>7} "
              f"{'BS Fair':>7} {'MktIV':>6} {'Misp%':>7} {'Pos':>4}")
        print("-" * 70)

        for opt in sorted(state.options, key=lambda o: (o.option_type, o.strike)):
            iv_str = f"{opt.market_iv*100:.1f}%" if opt.market_iv > 0 else "  -  "
            print(f"{opt.ticker:<10} {opt.option_type:<5} {opt.strike:>3.0f} "
                  f"{opt.bid:>7.3f} {opt.ask:>7.3f} {opt.bs_fair_price:>7.3f} "
                  f"{iv_str:>6} {opt.mispricing_pct*100:>6.2f}% "
                  f"{int(opt.position):>4}")
