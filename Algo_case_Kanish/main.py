# -*- coding: utf-8 -*-
"""
ALGO MARKET MAKING BOT - V12.1 AGGRESSIVE CAPACITY UTILIZATION
===============================================================
V12 result: +$20,073 (sim max: $63,786)
V12.1 target: $40,000-60,000

WHAT CHANGED FROM V12 → V12.1:
1. ORDER SIZES 3x BIGGER: WNTR 5000, SMMR 4500, ATMN 3000, SPNG 2000
2. 3 LAYERS of quotes (was 2): capture 3x more passive fills
3. EARLIER FLATTENING: start at second 36 (was 45), cancel at 42 (was 52)
4. SEPARATE CLOSE-TIME LIMIT: 9,000 (logged from API) vs 50k intraday
5. HIGHER PER-STOCK LIMITS: 12,500 per stock (was ~3,500)
6. FASTER CYCLE: 40ms (was 50ms)

KEY DATA INSIGHT:
- Intraday gross limit = 50,000 → V12 only used 12% average
- Close-time limit = 9,000 → need to flatten from ~30k to <7k
- Flattening cost: ~25k × $0.02/share = $500/day
- Extra volume revenue: potentially $20k-40k more profit
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
  ALGO MARKET MAKING BOT V12.1 - AGGRESSIVE CAPACITY UTILIZATION
  Target: 50-70% intraday utilization | 3-layer quotes | Flatten before close
  Close-time limit: {close_lim:,} | Intraday limit: {intra_lim:,}
  {url} | Key: {key}
================================================================================
"""


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


class MarketMaker:
    def __init__(self, api: RITApi):
        self.api = api
        self.vol_tracker = VolatilityTracker()
        self.order_tracker = OrderTracker()

        self.positions: Dict[str, int] = {t: 0 for t in TICKERS}
        self.mid_prices: Dict[str, float] = {}
        self.book_imbalance: Dict[str, float] = {t: 0.0 for t in TICKERS}
        self.market_spread: Dict[str, float] = {t: 0.10 for t in TICKERS}

        # V12.1: Two separate limits
        self.gross_limit = DEFAULT_GROSS_LIMIT      # 50,000 intraday
        self.close_limit = CLOSE_TIME_LIMIT          # 9,000 at close
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
        """Compute per-stock position limits weighted by rebate."""
        total_rebate = sum(REBATES[t] for t in TICKERS)
        for t in TICKERS:
            weight = REBATES[t] / total_rebate
            # Weight by rebate but cap
            allocated = int(self.gross_limit * PER_STOCK_LIMIT_FRACTION * weight / 0.25)
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
                # Detect if we're at close-time (limit drops to ~9k)
                if current_gl < 20000:
                    self.close_limit = current_gl  # Learn the actual close limit
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
        else:
            if self.circuit_breaker_active:
                self.circuit_breaker_active = False

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
        base = BASE_SPREAD.get(ticker, 0.04)

        # Layer multipliers
        if layer == 2:
            base *= LAYER2_SPREAD_MULT
        elif layer == 3:
            base *= LAYER3_SPREAD_MULT

        regime = self.vol_tracker.get_regime(ticker)
        vol_mult = VOL_SPREAD_MULT.get(regime, 1.0)
        adaptive = self.adaptive_mult.get(ticker, 1.0)

        time_mult = 1.0
        if second_in_day < POST_CLOSE_RECOVERY_SEC:
            time_mult = POST_CLOSE_SPREAD_MULT
        elif second_in_day >= PRE_CLOSE_WIDEN_SEC:
            time_mult = PRE_CLOSE_SPREAD_MULT

        cb_mult = 1.5 if self.circuit_breaker_active else 1.0

        spread = base * vol_mult * adaptive * time_mult * cb_mult
        min_spread = max(0.02, REBATES.get(ticker, 0.02) * 0.4)
        return max(min_spread, round(spread, 4))

    # ============================================================
    # ORDER SIZING - V12.1: AGGRESSIVE
    # ============================================================

    def compute_order_size(self, ticker: str, side: str, utilization: float,
                           second_in_day: int, layer: int = 1) -> int:
        base_size = ORDER_SIZE.get(ticker, 2000)
        pos = self.positions.get(ticker, 0)
        per_stock_limit = self.per_stock_limit.get(ticker, MIN_PER_STOCK_LIMIT)

        # Layer sizing
        if layer == 2:
            layer_mult = LAYER2_SIZE_MULT
        elif layer == 3:
            layer_mult = LAYER3_SIZE_MULT
        else:
            layer_mult = 1.0

        # Asymmetric sizing
        asym_mult = 1.0
        asym_threshold = per_stock_limit * ASYM_KICK_IN
        if abs(pos) > asym_threshold:
            pos_frac = min(abs(pos) / per_stock_limit, 1.0)
            if (pos > 0 and side == "SELL") or (pos < 0 and side == "BUY"):
                asym_mult = 1.0 + pos_frac * (ASYM_REDUCE_MAX - 1.0)
            elif (pos > 0 and side == "BUY") or (pos < 0 and side == "SELL"):
                asym_mult = max(ASYM_INCREASE_MIN, 1.0 - pos_frac * (1.0 - ASYM_INCREASE_MIN))

        regime = self.vol_tracker.get_regime(ticker)
        vol_mult = VOL_SIZE_MULT.get(regime, 1.0)

        # Utilization reduction (based on intraday 50k limit)
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

        time_mult = 1.0
        if second_in_day < POST_CLOSE_RECOVERY_SEC:
            time_mult = POST_CLOSE_SIZE_MULT
        elif second_in_day >= PRE_CLOSE_WIDEN_SEC:
            time_mult = PRE_CLOSE_SIZE_MULT

        cb_mult = 0.3 if self.circuit_breaker_active else 1.0

        size = int(base_size * layer_mult * asym_mult *
                   vol_mult * util_mult * time_mult * cb_mult)

        # Cap: don't exceed per-stock limit on increasing side
        if (pos > 0 and side == "BUY") or (pos < 0 and side == "SELL"):
            room = max(0, per_stock_limit - abs(pos))
            size = min(size, room)

        # Gross limit room
        gross = self.compute_aggregate()
        gross_room = max(0, int(self.gross_limit * GROSS_LIMIT_BUFFER) - gross)
        if (pos > 0 and side == "BUY") or (pos < 0 and side == "SELL") or pos == 0:
            size = min(size, gross_room // max(1, NUM_LAYERS))  # Share room across layers

        return max(0, min(size, MAX_ORDER_SIZE))

    # ============================================================
    # INVENTORY SKEW
    # ============================================================

    def compute_skew(self, ticker: str, utilization: float) -> float:
        pos = self.positions.get(ticker, 0)
        per_stock_limit = self.per_stock_limit.get(ticker, MIN_PER_STOCK_LIMIT)
        if per_stock_limit <= 0:
            return 0.0

        normalized = pos / per_stock_limit
        skew = -normalized * SKEW_FACTOR

        if utilization > UTIL_REDUCE:
            skew *= 2.0
        elif utilization > UTIL_SKEW:
            skew *= 1.5

        imbalance = self.book_imbalance.get(ticker, 0.0)
        if abs(imbalance) > IMBALANCE_THRESHOLD:
            skew += imbalance * IMBALANCE_SKEW_FACTOR

        return round(skew, 4)

    # ============================================================
    # QUOTING DECISIONS
    # ============================================================

    def should_quote_side(self, ticker: str, side: str, utilization: float) -> bool:
        pos = self.positions.get(ticker, 0)
        per_stock_limit = self.per_stock_limit.get(ticker, MIN_PER_STOCK_LIMIT)

        if self.circuit_breaker_halt or self.in_lockdown:
            return False

        # Block at 90% of per-stock limit for increasing side
        if side == "BUY" and pos >= int(per_stock_limit * 0.90):
            return False
        if side == "SELL" and pos <= -int(per_stock_limit * 0.90):
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
    # MAIN QUOTING ENGINE - V12.1 WITH 3 LAYERS
    # ============================================================

    def quote_ticker(self, ticker: str, tick: int, second_in_day: int,
                     utilization: float):
        mid = self.mid_prices.get(ticker, 25.0)
        if mid < MIN_PRICE or mid > MAX_PRICE:
            return

        skew = self.compute_skew(ticker, utilization)
        quote_buy = self.should_quote_side(ticker, "BUY", utilization)
        quote_sell = self.should_quote_side(ticker, "SELL", utilization)

        if not quote_buy and not quote_sell:
            if self.order_tracker.has_any_order(ticker):
                self.api.cancel_ticker_orders(ticker)
                self.order_tracker.clear_ticker(ticker)
            return

        book = self.api.get_book(ticker, limit=5)
        best_bid = self.api.get_best_bid(book)
        best_ask = self.api.get_best_ask(book)
        if best_bid > 0 and best_ask > 0:
            self.book_imbalance[ticker] = self.compute_book_imbalance(book)
            self.market_spread[ticker] = best_ask - best_bid
            mid = (best_bid + best_ask) / 2.0

        # Compute all 3 layers
        layers = []
        for layer_num in range(1, NUM_LAYERS + 1):
            # Skip layer 3 during pre-close or post-close
            if layer_num == 3 and (second_in_day < POST_CLOSE_RECOVERY_SEC or
                                    second_in_day >= PRE_CLOSE_WIDEN_SEC):
                continue
            # Skip layer 2 during pre-close reduce
            if layer_num == 2 and second_in_day >= PRE_CLOSE_REDUCE_SEC:
                continue

            spread = self.compute_spread(ticker, second_in_day, layer=layer_num)
            half = spread / 2.0
            buy_size = self.compute_order_size(ticker, "BUY", utilization, second_in_day, layer=layer_num) if quote_buy else 0
            sell_size = self.compute_order_size(ticker, "SELL", utilization, second_in_day, layer=layer_num) if quote_sell else 0

            bid = round(mid - half + skew, 2)
            ask = round(mid + half + skew, 2)

            # Safety
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
                tol = tolerance * ln  # Wider tolerance for outer layers
                for side, target, size in [("BUY", ldata['bid'], ldata['buy_size']),
                                            ("SELL", ldata['ask'], ldata['sell_size'])]:
                    if size >= 100:
                        if not self.order_tracker.order_is_close_enough(ticker, side, target, tol, layer=ln):
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
                    result = self.api.submit_limit_order(ticker, ldata['buy_size'], "BUY", ldata['bid'])
                    if result and isinstance(result, dict):
                        self.order_tracker.record_order(
                            ticker, "BUY", result.get("order_id", 0),
                            ldata['bid'], ldata['buy_size'], layer=ln)
                        self.orders_placed += 1

                if quote_sell and ldata['sell_size'] >= 100:
                    result = self.api.submit_limit_order(ticker, ldata['sell_size'], "SELL", ldata['ask'])
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
            per_stock_limit = self.per_stock_limit.get(ticker, MIN_PER_STOCK_LIMIT)
            threshold = int(per_stock_limit * 0.70)
            if abs(pos) < threshold:
                continue

            mid = self.mid_prices.get(ticker, 25.0)
            excess = abs(pos) - threshold

            if abs(pos) > per_stock_limit * 0.90:
                mkt_size = min(excess, int(per_stock_limit * 0.20))
                mkt_size = max(100, min(mkt_size, MAX_ORDER_SIZE))
                side = "SELL" if pos > 0 else "BUY"
                self.api.submit_market_order(ticker, mkt_size, side)
                self.market_orders_sent += 1
            else:
                reduce_size = min(excess, int(per_stock_limit * 0.20))
                reduce_size = max(100, min(reduce_size, MAX_ORDER_SIZE))
                if pos > 0:
                    price = round(mid + 0.01, 2)
                    self.api.submit_limit_order(ticker, reduce_size, "SELL", price)
                else:
                    price = round(mid - 0.01, 2)
                    self.api.submit_limit_order(ticker, reduce_size, "BUY", price)

    # ============================================================
    # MARKET CLOSE PROTOCOL - V12.1 (AGGRESSIVE)
    # ============================================================

    def pre_close_handler(self, tick: int, second_in_day: int):
        """V12.1: Earlier and more aggressive flattening.

        Timeline:
        - Second 36-37: Widen spreads, reduce sizes (via spread/size functions)
        - Second 38-41: Passive flattening (limit orders near mid)
        - Second 42: CANCEL ALL ORDERS
        - Second 43-59: Market-order flatten until agg < close_target
        """
        aggregate = self.compute_aggregate()
        close_target = int(self.close_limit * CLOSE_TARGET_UTILIZATION)

        if second_in_day >= PRE_CLOSE_FLATTEN_SEC:
            # Phase 3: AGGRESSIVE MARKET-ORDER FLATTEN
            if aggregate > close_target:
                sorted_positions = sorted(
                    [(t, self.positions[t]) for t in TICKERS if abs(self.positions[t]) > 0],
                    key=lambda x: abs(x[1]),
                    reverse=True
                )
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
            # Phase 2: CANCEL ALL
            if not self.in_lockdown:
                self.api.cancel_all_orders()
                self.order_tracker.clear_all()
                self.in_lockdown = True
                print(f"    [LOCKDOWN] T{tick} sec={second_in_day} "
                      f"agg={aggregate} close_lim={self.close_limit}")

        elif second_in_day >= PRE_CLOSE_REDUCE_SEC:
            # Phase 1: PASSIVE FLATTEN with aggressive limit orders
            for ticker in TICKERS:
                pos = self.positions.get(ticker, 0)
                if abs(pos) < 200:
                    continue
                mid = self.mid_prices.get(ticker, 25.0)
                reduce_size = min(abs(pos), 5000)
                reduce_size = max(100, min(reduce_size, MAX_ORDER_SIZE))
                if pos > 0:
                    price = round(mid - 0.01, 2)  # Aggressive: sell below mid
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
    # MAIN LOOP
    # ============================================================

    def run(self):
        print(BANNER.format(
            close_lim=self.close_limit, intra_lim=self.gross_limit,
            url=API_BASE_URL, key=API_KEY))

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
                        print(f"  Final P&L: ${self.current_pnl:,.2f}")
                        print(f"  Orders: +{self.orders_placed}/-{self.orders_cancelled}")
                        print(f"  Market orders: {self.market_orders_sent}")
                        print(f"  Volume: {self.total_volume_traded:,}")
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

                if current_day != last_day:
                    if last_day >= 0:
                        print(f"\n  === DAY {current_day + 1} (tick {tick}) ===")
                    self.in_lockdown = False
                    last_day = current_day

                if self.circuit_breaker_halt:
                    time.sleep(1.0)
                    continue

                # State updates
                self.update_state(tick)
                if tick % 5 == 0:
                    self.update_limits()
                if tick % 3 == 0:
                    self.update_pnl()
                self.update_adaptive_spreads(tick)

                utilization = self.compute_utilization()
                aggregate = self.compute_aggregate()
                net = self.compute_net()

                # PRE-CLOSE (second >= 36)
                if second_in_day >= PRE_CLOSE_WIDEN_SEC:
                    self.pre_close_handler(tick, second_in_day)
                    time.sleep(CYCLE_SLEEP)
                    continue

                # POST-CLOSE RECOVERY (second < 5)
                if second_in_day < POST_CLOSE_RECOVERY_SEC:
                    if self.order_tracker.has_any_order(TICKERS[0]):
                        self.api.cancel_all_orders()
                        self.order_tracker.clear_all()
                    time.sleep(CYCLE_SLEEP)
                    continue

                # ACTIVE TRADING (second 5-35)
                self.in_lockdown = False

                if utilization > UTIL_PANIC:
                    self.panic_flatten()
                    time.sleep(CYCLE_SLEEP)
                    continue
                elif utilization > UTIL_EMERGENCY:
                    self.emergency_flatten(target_util=UTIL_REDUCE)

                if tick % 3 == 0:
                    self.reduce_large_positions(tick)

                for ticker in TICKERS:
                    self.quote_ticker(ticker, tick, second_in_day, utilization)

                # Logging
                if tick - last_log_tick >= LOG_INTERVAL_TICKS:
                    pos_str = " | ".join(
                        f"{t}:{int(self.positions.get(t,0)):+d}" for t in TICKERS)
                    pnl_str = f"${self.current_pnl:+,.0f}" if self.start_nlv > 0 else "N/A"
                    print(f"  T{tick:03d} d{second_in_day:02d} | "
                          f"pos=[{pos_str}] | "
                          f"agg={aggregate:,}/{self.gross_limit:,}({utilization:.0%}) | "
                          f"net={net:,} | pnl={pnl_str} | "
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
    fh = logging.FileHandler(os.path.join(log_dir, f"mm_v12_1_{ts}.log"))
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