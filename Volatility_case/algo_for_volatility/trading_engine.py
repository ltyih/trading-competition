"""
V5 Maximum-Profit Trading Engine for Volatility Case.

Key fixes from V4:
- NO CHURNING: only trade when gap > MIN_TRADE_GAP per option
- NO FALSE REVERSALS: require large edge + cooldown before reversing
- BUILD AND HOLD: reach target fast, then stop trading and just delta hedge
- Focus on ATM straddles for max vega per contract
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
)
from black_scholes import (
    bs_price, bs_delta, bs_gamma, bs_vega, implied_volatility,
)
from news_parser import VolatilityState
from rit_api import RITApi

logger = logging.getLogger(__name__)

# Anti-churn: minimum gap per option before we trade
MIN_TRADE_GAP = 5

# Reversal protection: minimum edge to reverse direction
MIN_REVERSAL_EDGE = 0.03  # 3% vol edge required to reverse
REVERSAL_COOLDOWN_TICKS = 15  # Wait 15 ticks after a reversal before allowing another

# Position building: once within this % of target, stop rebalancing
POSITION_TOLERANCE = 0.15  # Within 15% of target = close enough


@dataclass
class OptionData:
    """Parsed option data for a single contract."""
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
    """Current portfolio state."""
    underlying_position: int = 0
    underlying_price: float = 0.0
    options: List[OptionData] = field(default_factory=list)
    total_delta: float = 0.0
    total_gamma: float = 0.0
    total_vega: float = 0.0
    options_gross: int = 0
    options_net: int = 0
    avg_market_iv: float = 0.0


class TradingEngine:
    """V5 Maximum-Profit Trading Engine - Build and Hold."""

    def __init__(self, api: RITApi):
        self.api = api
        self.vol_state = VolatilityState()
        self.last_tick = 0
        self.last_direction = 0
        self.direction_changes = 0
        self.last_reversal_tick = -100
        self.position_built = False  # True once we've reached target

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
        """
        Determine direction with reversal protection.
        Won't reverse direction unless edge is large AND cooldown has passed.
        """
        analyst_vol = self.vol_state.best_vol_estimate
        if not analyst_vol or analyst_vol <= 0:
            return 0, 0.0

        market_iv = state.avg_market_iv
        if market_iv <= 0:
            return 0, 0.0

        edge = analyst_vol - market_iv
        abs_edge = abs(edge)
        new_direction = 1 if edge > 0 else -1

        # If no direction yet, use normal threshold
        if self.last_direction == 0:
            if abs_edge < VOL_EDGE_THRESHOLD:
                return 0, abs_edge
            return new_direction, abs_edge

        # Same direction as before - keep it
        if new_direction == self.last_direction:
            if abs_edge < VOL_EDGE_THRESHOLD:
                # Edge too small, go flat but DON'T reverse
                return 0, abs_edge
            return new_direction, abs_edge

        # DIFFERENT direction - apply strict reversal protection
        ticks_since_reversal = tick - self.last_reversal_tick
        if ticks_since_reversal < REVERSAL_COOLDOWN_TICKS:
            # Still in cooldown - keep current direction if edge exists, else flat
            return self.last_direction if abs_edge > VOL_EDGE_THRESHOLD else 0, abs_edge

        if abs_edge < MIN_REVERSAL_EDGE:
            # Edge not big enough to justify reversal - keep current direction
            return self.last_direction if abs_edge > VOL_EDGE_THRESHOLD else 0, abs_edge

        # Genuine reversal - large edge, cooldown passed
        return new_direction, abs_edge

    def calculate_targets(self, state: PortfolioState, direction: int,
                          edge: float) -> Dict[str, int]:
        """Calculate target positions. Only ATM ± 2 strikes for focus."""
        if direction == 0:
            return {}

        S = state.underlying_price
        if S <= 0:
            return {}

        target_scale = min(edge / FULL_EDGE_THRESHOLD, 1.0)
        target_scale = max(target_scale, 0.30)
        total_target = int(TARGET_NET_POSITION * target_scale)

        # Weight strikes by vega but concentrate on top 5 strikes
        vega_list = []
        for opt in state.options:
            if opt.bs_vega > 0 and opt.option_type == "CALL":
                vega_list.append((opt.strike, opt.bs_vega))

        vega_list.sort(key=lambda x: x[1], reverse=True)
        # Take top 5 strikes (highest vega = most ATM)
        top_strikes = [s for s, v in vega_list[:5]]

        vega_weights = {}
        total_vega = 0.0
        for s, v in vega_list[:5]:
            vega_weights[s] = v
            total_vega += v

        if total_vega <= 0:
            return {}

        for k in vega_weights:
            vega_weights[k] /= total_vega

        targets = {}
        for opt in state.options:
            if opt.strike not in vega_weights:
                # Non-top strikes: target = 0 (don't actively close though)
                continue
            w = vega_weights[opt.strike]
            strike_target = int(total_target * w / 2)
            strike_target = max(strike_target, 0)
            targets[opt.ticker] = direction * strike_target

        return targets

    def execute_towards_targets(self, state: PortfolioState,
                                targets: Dict[str, int],
                                tick: int) -> int:
        """Submit market orders but ONLY for significant gaps (anti-churn)."""
        orders_sent = 0
        positions = {opt.ticker: opt.position for opt in state.options}
        running_gross = state.options_gross
        running_net = state.options_net

        # Build trade list, filtering out small gaps
        trade_list = []
        for ticker, target in targets.items():
            current = positions.get(ticker, 0)
            gap = target - current
            # ANTI-CHURN: only trade if gap is significant
            if abs(gap) < MIN_TRADE_GAP:
                continue
            trade_list.append((ticker, current, target, gap))

        # Check if we're close enough to target overall
        total_target_pos = sum(abs(t) for t in targets.values())
        total_current_pos = sum(abs(positions.get(t, 0)) for t in targets)
        if total_target_pos > 0:
            achieved_ratio = total_current_pos / total_target_pos
            if achieved_ratio > (1 - POSITION_TOLERANCE) and not trade_list:
                self.position_built = True
                return 0

        trade_list.sort(key=lambda x: abs(x[3]), reverse=True)

        for ticker, current, target, gap in trade_list:
            if orders_sent >= MAX_OPTION_ORDERS_PER_CYCLE:
                break

            action = "BUY" if gap > 0 else "SELL"
            qty = min(abs(gap), OPTIONS_MAX_TRADE_SIZE)

            if qty <= 0:
                continue

            # Pre-check limits
            if action == "BUY":
                if running_gross + qty > OPTIONS_GROSS_LIMIT - 20:
                    qty = max(0, OPTIONS_GROSS_LIMIT - 20 - running_gross)
                if running_net + qty > OPTIONS_NET_LIMIT - 10:
                    qty = min(qty, max(0, OPTIONS_NET_LIMIT - 10 - running_net))
            else:
                if running_gross + qty > OPTIONS_GROSS_LIMIT - 20:
                    qty = max(0, OPTIONS_GROSS_LIMIT - 20 - running_gross)
                if running_net - qty < -(OPTIONS_NET_LIMIT - 10):
                    qty = min(qty, max(0, running_net + OPTIONS_NET_LIMIT - 10))

            if qty < MIN_TRADE_GAP:
                continue

            # Check price
            opt = next((o for o in state.options if o.ticker == ticker), None)
            if opt:
                price_check = opt.ask if action == "BUY" else opt.bid
                if price_check < MIN_OPTION_PRICE:
                    continue

            result = self.api.submit_market_order(ticker, qty, action)
            if result:
                orders_sent += 1
                if action == "BUY":
                    running_gross += qty
                    running_net += qty
                else:
                    running_gross += qty
                    running_net -= qty

        return orders_sent

    def flatten_all_options(self, state: PortfolioState) -> int:
        """Flatten all option positions."""
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

    def delta_hedge(self, state: PortfolioState) -> int:
        """Gamma-aware delta hedging."""
        delta_limit = self.vol_state.delta_limit
        if delta_limit is None:
            delta_limit = 10000

        current_delta = state.total_delta
        abs_delta = abs(current_delta)

        if state.total_gamma > 0:
            hedge_threshold = delta_limit * 0.20
        else:
            hedge_threshold = delta_limit * 0.10

        if abs_delta < hedge_threshold:
            return 0

        hedge_needed = -current_delta
        hedge_qty = min(abs(int(round(hedge_needed))), RTM_MAX_TRADE_SIZE)

        if hedge_qty < 50:
            return 0

        action = "BUY" if hedge_needed > 0 else "SELL"
        self.api.submit_market_order(UNDERLYING_TICKER, hedge_qty, action)
        return 1

    def execute_cycle(self, tick: int) -> Dict:
        """Execute one trading cycle with anti-churn logic."""
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
        }

        # 1. Update news
        self.update_news()
        vol_est = self.vol_state.best_vol_estimate
        result["vol"] = (vol_est * 100) if vol_est else None
        result["delta_limit"] = self.vol_state.delta_limit

        # 2. Cancel stale orders every 10 ticks (not every cycle!)
        if tick % 10 == 0:
            self.api.cancel_all_orders()

        # 3. Get portfolio state
        state = self.get_portfolio_state(tick)
        if state is None:
            return result

        result["spot"] = state.underlying_price
        result["delta"] = state.total_delta
        result["gross"] = state.options_gross
        result["net"] = state.options_net
        result["market_iv"] = state.avg_market_iv * 100 if state.avg_market_iv > 0 else None

        # 4. Near end -> unwind
        if tick >= UNWIND_START_TICK:
            unwind_count = self.flatten_all_options(state)
            result["unwind_trades"] = unwind_count
            if abs(state.underlying_position) > 100:
                action = "SELL" if state.underlying_position > 0 else "BUY"
                qty = min(abs(state.underlying_position), RTM_MAX_TRADE_SIZE)
                self.api.submit_market_order(UNDERLYING_TICKER, qty, action)
                result["hedge_trades"] = 1
            return result

        # 5. Determine direction with reversal protection
        direction, edge = self.get_vol_direction(state, tick)
        result["direction"] = direction
        result["edge"] = edge * 100

        # 6. Handle reversal
        if (self.last_direction != 0 and direction != 0 and
                direction != self.last_direction):
            logger.info("VOL REVERSAL at tick %d: %d -> %d (edge=%.1f%%)",
                        tick, self.last_direction, direction, edge * 100)
            self.api.cancel_all_orders()
            self.flatten_all_options(state)
            result["reversal"] = True
            self.direction_changes += 1
            self.last_reversal_tick = tick
            self.position_built = False
            self.last_direction = direction
            # Don't trade this cycle - just flatten and wait
            state = self.get_portfolio_state(tick)
            if state:
                result["delta"] = state.total_delta
                result["gross"] = state.options_gross
                result["net"] = state.options_net
                hedges = self.delta_hedge(state)
                result["hedge_trades"] = hedges
            return result

        if direction != 0:
            self.last_direction = direction

        # 7. If position already built and direction unchanged, just delta hedge
        if self.position_built and direction == self.last_direction:
            hedges = self.delta_hedge(state)
            result["hedge_trades"] = hedges
            result["built"] = True
            return result

        # 8. Build towards target
        if direction != 0:
            targets = self.calculate_targets(state, direction, edge)
            trades = self.execute_towards_targets(state, targets, tick)
            result["option_trades"] = trades

        # 9. Delta hedge
        state = self.get_portfolio_state(tick)
        if state:
            result["delta"] = state.total_delta
            result["gross"] = state.options_gross
            result["net"] = state.options_net
            result["built"] = self.position_built
            hedges = self.delta_hedge(state)
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
