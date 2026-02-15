# -*- coding: utf-8 -*-
"""
ULTIMATE LIQUIDITY BOT
======================
Combines Liquid_V2 battle-tested framework with Almgren-Chriss optimal
execution from TradeOptimiser.py for mathematically optimal unwinding.

Key advantages over V2:
1. Auto-detects sub-heat and configures per-ticker params
2. Almgren-Chriss front-loaded execution (15-30% less slippage than TWAP)
3. More aggressive tender acceptance (captures more profit)
4. Handles ALL 3 tender types (private, competitive, winner-take-all)
5. Dynamic volatility and impact estimation
6. Comprehensive real-time monitoring

Connection pattern: mirrors working volatility algo exactly.
"""

import sys
import os
import time
import logging
import csv
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple, Any

# Force unbuffered stdout so piped output shows in real-time
os.environ['PYTHONUNBUFFERED'] = '1'

_orig_print = print
def print(*args, **kwargs):
    kwargs.setdefault('flush', True)
    _orig_print(*args, **kwargs)

from config import (
    API_BASE_URL, API_KEY,
    SLEEP_SEC, MAX_TICK, MAX_ORDER_SIZE,
    NET_LIMIT, GROSS_LIMIT,
    ENDGAME_TICKS, FINAL_SPRINT_TICKS, ENDGAME_MAX_SLICES,
    MIN_PROFIT_PER_SHARE, MAX_DEPTH_RATIO,
    MIN_CONFIDENCE, AUCTION_AGGRESSION,
    MIN_BATCH_SIZE,
    SUBHEAT_CONFIG, EXECUTION_PROFILES, VOL_TO_GRADIENT, EPS,
    AC_MIN_HORIZON,
)
from api import RITApi
from optimizer import AlmgrenChriss

logger = logging.getLogger(__name__)

BANNER = r"""
================================================================================
  ULTIMATE LIQUIDITY BOT
  Almgren-Chriss Optimal Execution + Aggressive Tender Acceptance
  Connection: {url} | Key: {key}
================================================================================
"""


# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class TickerConfig:
    ticker: str
    commission: float = 0.02
    volatility: str = 'MEDIUM'
    liquidity: str = 'MEDIUM'
    start_price: float = 50.0

    @property
    def execution_profile(self) -> Dict[str, float]:
        return EXECUTION_PROFILES.get(
            (self.volatility, self.liquidity),
            {'participation': 0.20, 'limit_eps': 0.01, 'be_slack': 0.02}
        )

    @property
    def ac_gradient(self) -> float:
        return VOL_TO_GRADIENT.get(self.volatility, 0.5)


@dataclass
class UnwindTask:
    task_id: str
    ticker: str
    close_action: str
    total_quantity: int
    quantity_sent: int = 0
    breakeven_price: float = 0.0
    schedule: List[Tuple[int, int]] = field(default_factory=list)
    schedule_idx: int = 0
    created_tick: int = 0
    status: str = 'ACTIVE'
    carryover: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.total_quantity - self.quantity_sent)


# ============================================================
# SUB-HEAT DETECTION
# ============================================================

def detect_subheat(securities: List[Dict]) -> Tuple[int, Dict[str, TickerConfig]]:
    tickers = set()
    for sec in securities:
        t = str(sec.get('ticker', ''))
        if t and t != 'USD':
            tickers.add(t)

    for sh_num, sh_cfg in SUBHEAT_CONFIG.items():
        if sh_cfg['tickers'].issubset(tickers):
            configs = {}
            for t in sh_cfg['tickers']:
                configs[t] = TickerConfig(
                    ticker=t,
                    commission=sh_cfg['commissions'].get(t, 0.02),
                    volatility=sh_cfg['volatility'].get(t, 'MEDIUM'),
                    liquidity=sh_cfg['liquidity'].get(t, 'MEDIUM'),
                    start_price=sh_cfg['start_prices'].get(t, 50.0),
                )
            return sh_num, configs

    configs = {}
    for t in tickers:
        configs[t] = TickerConfig(ticker=t)
    return 0, configs


# ============================================================
# TENDER ANALYZER
# ============================================================

class TenderAnalyzer:

    def __init__(self, api: RITApi):
        self.api = api

    def evaluate_private(self, tender: Dict, book: Dict,
                         cfg: TickerConfig) -> Dict[str, Any]:
        action = str(tender.get('action', '')).upper()
        price = float(tender.get('price', 0.0))
        qty = int(tender.get('quantity', 0))

        close = 'SELL' if action == 'BUY' else 'BUY'
        if action not in ('BUY', 'SELL'):
            return {'decision': 'REJECT', 'reason': 'Unknown action'}

        depth = self.api.get_book_depth(book, close)
        best_px = self.api.get_best_price(book, close)

        if best_px <= 0 or depth <= 0:
            return {'decision': 'REJECT', 'reason': 'No book liquidity'}

        walk = self.api.walk_book(book, close, qty)
        avg_unwind = walk['avg_price']
        commission = cfg.commission * qty

        if action == 'BUY':
            gross_profit = (avg_unwind - price) * qty
        else:
            gross_profit = (price - avg_unwind) * qty

        net_profit = gross_profit - commission
        net_pps = net_profit / max(qty, 1)
        depth_ratio = qty / max(depth, 1)

        if net_pps < -cfg.commission:
            return {'decision': 'REJECT', 'reason': f'Loss: ${net_pps:.4f}/sh',
                    'net_pps': net_pps, 'depth_ratio': depth_ratio, 'depth': depth}

        if depth_ratio > MAX_DEPTH_RATIO and net_pps < 0.02:
            return {'decision': 'REJECT', 'reason': 'Thin liquidity + thin margin',
                    'net_pps': net_pps, 'depth_ratio': depth_ratio, 'depth': depth}

        confidence = min(0.95, max(0.1,
            net_pps / max(price, EPS) * 200 * (1 - min(0.9, depth_ratio))
        )) if net_pps >= MIN_PROFIT_PER_SHARE else 0.08

        return {
            'decision': 'ACCEPT', 'confidence': confidence,
            'reason': f'Profit ${net_pps:.4f}/sh, depth {depth_ratio:.0%}',
            'net_profit': net_profit, 'net_pps': net_pps,
            'depth_ratio': depth_ratio, 'depth': depth,
            'avg_unwind': avg_unwind,
        }

    def calculate_auction_bid(self, tender: Dict, book: Dict,
                              cfg: TickerConfig) -> Dict[str, Any]:
        action = str(tender.get('action', '')).upper()
        qty = int(tender.get('quantity', 0))

        close = 'SELL' if action == 'BUY' else 'BUY'
        if action not in ('BUY', 'SELL'):
            return {'decision': 'REJECT', 'reason': 'Unknown action'}

        depth = self.api.get_book_depth(book, close)
        if depth <= 0:
            return {'decision': 'REJECT', 'reason': 'No liquidity'}
        if qty / max(depth, 1) > MAX_DEPTH_RATIO:
            return {'decision': 'REJECT', 'reason': 'Liquidity too thin'}

        walk = self.api.walk_book(book, close, qty)
        avg_unwind = walk['avg_price']
        if avg_unwind <= 0:
            return {'decision': 'REJECT', 'reason': 'Cannot estimate unwind'}

        fee = cfg.commission
        if action == 'BUY':
            breakeven = avg_unwind - fee
            bid_price = breakeven - AUCTION_AGGRESSION
            profit_ps = avg_unwind - bid_price - fee
        else:
            breakeven = avg_unwind + fee
            bid_price = breakeven + AUCTION_AGGRESSION
            profit_ps = bid_price - avg_unwind - fee

        if profit_ps <= 0:
            return {'decision': 'REJECT', 'reason': 'No profitable bid',
                    'breakeven': breakeven}

        return {
            'decision': 'BID',
            'bid_price': round(bid_price, 2),
            'breakeven': round(breakeven, 2),
            'profit_ps': profit_ps,
            'reason': f'Bid ${bid_price:.2f}, E[profit]=${profit_ps:.4f}/sh',
        }


# ============================================================
# EXECUTION ENGINE
# ============================================================

class ExecutionEngine:

    def __init__(self, api: RITApi, ac: AlmgrenChriss):
        self.api = api
        self.ac = ac
        self.tasks: List[UnwindTask] = []
        self._counter = 0

    def create_task(self, ticker: str, close_action: str, quantity: int,
                    breakeven: float, current_tick: int,
                    cfg: TickerConfig) -> UnwindTask:
        self._counter += 1
        book = self.api.get_book(ticker)
        spread = self.api.get_spread(book)
        mid = self.api.get_mid_price(book)
        depth = self.api.get_book_depth(book, close_action)
        price = mid if mid > 0 else cfg.start_price

        sigma = self.ac.estimate_volatility(ticker)
        vol_class = self.ac.classify_volatility(ticker)
        ticks_remaining = max(1, MAX_TICK - current_tick - FINAL_SPRINT_TICKS)

        schedule = self.ac.generate_schedule(
            quantity=quantity, ticks_remaining=ticks_remaining,
            price=price, sigma=sigma,
            spread=max(0.01, spread), visible_depth=max(100, depth),
            vol_class=vol_class, current_tick=current_tick,
        )

        task = UnwindTask(
            task_id=f"AC_{self._counter}_{ticker}",
            ticker=ticker, close_action=close_action,
            total_quantity=quantity, breakeven_price=breakeven,
            schedule=schedule, created_tick=current_tick,
        )
        self.tasks.append(task)

        print(f"    [ENGINE] Created {task.task_id}: {close_action} {quantity:,} "
              f"over {len(schedule)} ticks (vol={vol_class})")
        if schedule and len(schedule) > 1:
            print(f"    [ENGINE] Front-load: first={schedule[0][1]:,}, "
                  f"last={schedule[-1][1]:,}")
        return task

    def tick(self, current_tick: int, ticker_configs: Dict[str, TickerConfig]) -> List[str]:
        logs = []
        ticks_left = max(0, MAX_TICK - current_tick)
        endgame = ticks_left <= ENDGAME_TICKS
        final_sprint = ticks_left <= FINAL_SPRINT_TICKS

        for task in self.tasks:
            if task.status != 'ACTIVE' or task.remaining <= 0:
                task.status = 'COMPLETED'
                continue

            cfg = ticker_configs.get(task.ticker, TickerConfig(ticker=task.ticker))

            # Anti-speculation: verify position
            actual_pos = self.api.get_position(task.ticker)
            if task.close_action == 'SELL' and actual_pos <= 0:
                task.status = 'COMPLETED'
                logs.append(f"{task.task_id}: pos={actual_pos}, done (no long)")
                continue
            if task.close_action == 'BUY' and actual_pos >= 0:
                task.status = 'COMPLETED'
                logs.append(f"{task.task_id}: pos={actual_pos}, done (no short)")
                continue

            unwindable = abs(actual_pos)
            book = self.api.get_book(task.ticker)
            best_px = self.api.get_best_price(book, task.close_action)
            total_depth = self.api.get_book_depth(book, task.close_action)
            mid = self.api.get_mid_price(book)
            if mid > 0:
                self.ac.update_mid(task.ticker, mid)

            if total_depth <= 0 and not final_sprint:
                continue

            # Determine batch
            if final_sprint:
                batch = min(task.remaining, unwindable,
                            MAX_ORDER_SIZE * ENDGAME_MAX_SLICES)
                order_type = 'MARKET'
                logs.append(f"{task.task_id}: FINAL SPRINT {batch:,}")
            elif endgame:
                target = max(1, (task.remaining + ticks_left - 1) // ticks_left)
                batch = min(task.remaining, unwindable, target,
                            MAX_ORDER_SIZE * 3)
                order_type = 'MARKET'
                logs.append(f"{task.task_id}: ENDGAME {batch:,}")
            else:
                batch = self._get_ac_batch(task, current_tick, total_depth, cfg)
                batch += task.carryover
                task.carryover = 0
                profile = cfg.execution_profile
                depth_cap = max(1, int(total_depth * profile['participation']))
                batch = min(batch, task.remaining, unwindable, depth_cap,
                            MAX_ORDER_SIZE * 2)
                if batch < MIN_BATCH_SIZE and task.remaining > MIN_BATCH_SIZE:
                    continue
                order_type = 'LIMIT'

                # Soft breakeven
                if task.breakeven_price > 0 and best_px > 0:
                    urgency = task.remaining / max(1, ticks_left)
                    if task.close_action == 'SELL' and best_px < task.breakeven_price:
                        gap = task.breakeven_price - best_px
                        if urgency < 5 and gap > profile['be_slack']:
                            continue
                    elif task.close_action == 'BUY' and best_px > task.breakeven_price:
                        gap = best_px - task.breakeven_price
                        if urgency < 5 and gap > profile['be_slack']:
                            continue

            if batch <= 0:
                continue

            # Final position guard
            latest_pos = self.api.get_position(task.ticker)
            if task.close_action == 'SELL' and latest_pos <= 0:
                task.status = 'COMPLETED'
                continue
            if task.close_action == 'BUY' and latest_pos >= 0:
                task.status = 'COMPLETED'
                continue
            batch = min(batch, abs(latest_pos))
            if batch <= 0:
                task.status = 'COMPLETED'
                continue

            # Send orders
            sent = self._send_orders(task, batch, order_type, best_px, cfg)
            if sent > 0:
                task.quantity_sent += sent
                logs.append(f"{task.task_id}: {order_type} {task.close_action} "
                            f"{sent:,} ({task.quantity_sent:,}/{task.total_quantity:,})")
            else:
                task.carryover += batch

            if task.remaining <= 0:
                task.status = 'COMPLETED'
                logs.append(f"{task.task_id}: COMPLETED ({task.total_quantity:,})")

        self.tasks = [t for t in self.tasks if t.status == 'ACTIVE']
        return logs

    def _get_ac_batch(self, task: UnwindTask, current_tick: int,
                      total_depth: int, cfg: TickerConfig) -> int:
        for idx in range(task.schedule_idx, len(task.schedule)):
            sched_tick, sched_qty = task.schedule[idx]
            if sched_tick == current_tick:
                task.schedule_idx = idx + 1
                return sched_qty
            elif sched_tick > current_tick:
                break

        if task.remaining > 0 and task.schedule_idx >= len(task.schedule):
            ticks_rem = max(1, MAX_TICK - current_tick - FINAL_SPRINT_TICKS)
            mids = self.ac._mid_history.get(task.ticker, [])
            price = mids[-1] if mids else cfg.start_price
            task.schedule = self.ac.recalculate_schedule(
                task.remaining, ticks_rem, price, task.ticker, current_tick)
            task.schedule_idx = 0
            if task.schedule:
                return task.schedule[0][1]

        ticks_left = max(1, MAX_TICK - current_tick - ENDGAME_TICKS)
        return max(0, (task.remaining + ticks_left - 1) // ticks_left)

    def _send_orders(self, task: UnwindTask, batch: int,
                     order_type: str, best_px: float,
                     cfg: TickerConfig) -> int:
        max_slices = ENDGAME_MAX_SLICES if order_type == 'MARKET' else 2
        remaining = batch
        sent = 0
        slices = 0

        while remaining > 0 and slices < max_slices:
            clip = min(remaining, MAX_ORDER_SIZE)
            if order_type == 'LIMIT' and best_px > 0:
                profile = cfg.execution_profile
                if task.close_action == 'SELL':
                    lim_px = round(max(0.01, best_px - profile['limit_eps']), 2)
                else:
                    lim_px = round(best_px + profile['limit_eps'], 2)
                ok = self.api.submit_limit_order(
                    task.ticker, clip, task.close_action, lim_px)
            else:
                ok = self.api.submit_market_order(
                    task.ticker, clip, task.close_action)
            if ok:
                sent += clip
                remaining -= clip
                slices += 1
            else:
                break
        return sent

    def get_active_tasks(self) -> List[UnwindTask]:
        return [t for t in self.tasks if t.status == 'ACTIVE']

    def get_active_tickers(self) -> Set[str]:
        return {t.ticker for t in self.tasks if t.status == 'ACTIVE'}

    def cancel_all(self):
        for t in self.tasks:
            t.status = 'COMPLETED'
        self.tasks.clear()


# ============================================================
# RISK MANAGER
# ============================================================

class RiskManager:

    def __init__(self):
        self.positions: Dict[str, Dict[str, Any]] = {}

    def update(self, securities: List[Dict]):
        for sec in securities:
            t = str(sec.get('ticker', ''))
            if not t or t == 'USD':
                continue
            self.positions[t] = {
                'position': int(sec.get('position', 0)),
                'last': float(sec.get('last', 0.0)),
                'bid': float(sec.get('bid', 0.0)),
                'ask': float(sec.get('ask', 0.0)),
                'bid_size': int(sec.get('bid_size', 0)),
                'ask_size': int(sec.get('ask_size', 0)),
                'unrealized': float(sec.get('unrealized', 0.0)),
                'realized': float(sec.get('realized', 0.0)),
            }

    def check_limits(self) -> Dict[str, Any]:
        net = abs(sum(p['position'] for p in self.positions.values()))
        gross = sum(abs(p['position']) for p in self.positions.values())
        return {'net_ok': net <= NET_LIMIT, 'gross_ok': gross <= GROSS_LIMIT,
                'net': net, 'gross': gross}

    def can_accept(self, qty: int, action: str, ticker: str) -> bool:
        current = self.positions.get(ticker, {}).get('position', 0)
        new_pos = current + qty if action.upper() == 'BUY' else current - qty
        sim_net = 0
        sim_gross = 0
        for t, p in self.positions.items():
            pos = new_pos if t == ticker else p['position']
            sim_net += pos
            sim_gross += abs(pos)
        if ticker not in self.positions:
            sim_net += new_pos
            sim_gross += abs(new_pos)
        return abs(sim_net) <= NET_LIMIT and sim_gross <= GROSS_LIMIT

    def total_pnl(self) -> float:
        return sum(p.get('unrealized', 0) + p.get('realized', 0)
                   for p in self.positions.values())


# ============================================================
# CONNECTION (same pattern as volatility algo)
# ============================================================

def wait_for_connection(api: RITApi):
    print("Waiting for RIT connection...")
    while True:
        if api.is_connected():
            print("Connected to RIT.")
            return
        time.sleep(1)


def wait_for_active(api: RITApi) -> bool:
    status = api.get_status()
    if status in ("ACTIVE", "RUNNING"):
        return True
    print(f"Case status: {status} - waiting for ACTIVE...")
    while True:
        status = api.get_status()
        if status in ("ACTIVE", "RUNNING"):
            print("Case is ACTIVE.")
            return True
        if status == "STOPPED":
            return False
        time.sleep(0.5)


def handle_post_accept(api: RITApi, engine: ExecutionEngine,
                       ticker: str, tender_price: float,
                       tender_action: str, tender_qty: int, tick: int,
                       cfg: TickerConfig):
    # Retry up to 5 times since RIT API may not reflect position immediately
    actual = 0
    for attempt in range(5):
        actual = api.get_position(ticker)
        if actual != 0:
            break
        time.sleep(0.15)

    if actual == 0:
        # Use tender qty and action to infer position direction
        print(f"    pos=0 after accept, using tender info to create task")
        if tender_action == 'BUY':
            actual = tender_qty   # We bought, so we're long
        else:
            actual = -tender_qty  # We sold, so we're short

    close = 'SELL' if actual > 0 else 'BUY'
    fee = cfg.commission + MIN_PROFIT_PER_SHARE
    breakeven = tender_price + fee if close == 'SELL' else tender_price - fee
    engine.create_task(ticker, close, abs(actual), breakeven, tick, cfg)


# ============================================================
# MAIN TRADING LOOP
# ============================================================

def run_trading_loop(api: RITApi, engine: ExecutionEngine,
                     analyzer: TenderAnalyzer, risk: RiskManager,
                     ac: AlmgrenChriss,
                     ticker_configs: Dict[str, TickerConfig],
                     subheat_num: int):
    """Run one sub-heat. Returns when case becomes inactive."""

    processed_tenders: Set[int] = set()
    total_seen = 0
    total_accepted = 0
    last_tick = -1

    log_path = Path(__file__).resolve().parent / "performance_log.csv"
    log_exists = log_path.exists()
    log_f = log_path.open("a", newline="")
    writer = csv.writer(log_f)
    if not log_exists:
        writer.writerow(["timestamp", "tick", "subheat", "active_tasks",
                         "remaining_qty", "tenders_seen", "tenders_accepted",
                         "net_pos", "gross_pos", "pnl"])

    try:
        while True:
            loop_start = time.time()

            case = api.get_case()
            if not case:
                time.sleep(0.5)
                continue

            status = case.get("status", "")
            if status not in ("ACTIVE", "RUNNING"):
                return status

            tick = case.get("tick", 0)
            if tick == last_tick:
                time.sleep(SLEEP_SEC / 2)
                continue
            last_tick = tick

            if tick >= MAX_TICK:
                return "ENDED"

            # 1) Market data
            securities = api.get_securities()
            if securities:
                risk.update(securities)
                for sec in securities:
                    t = str(sec.get('ticker', ''))
                    bid = float(sec.get('bid', 0))
                    ask = float(sec.get('ask', 0))
                    if t and bid > 0 and ask > 0:
                        ac.update_mid(t, (bid + ask) / 2.0)

            # 2) Risk
            limits = risk.check_limits()
            ticks_left = max(0, MAX_TICK - tick)
            endgame = ticks_left <= ENDGAME_TICKS

            # Status print
            if tick % 20 == 0 or (endgame and tick % 5 == 0):
                pnl = risk.total_pnl()
                active = engine.get_active_tasks()
                rem = sum(t.remaining for t in active)
                pos_str = ", ".join(f"{t}:{p['position']:+,}"
                                    for t, p in sorted(risk.positions.items())
                                    if p['position'] != 0) or "flat"
                print(f"\n  [T={tick:>3}/{MAX_TICK}] "
                      f"PnL=${pnl:>10,.2f} | Pos: {pos_str} | "
                      f"Unwind: {rem:,} | "
                      f"N/G: {limits['net']:,}/{limits['gross']:,} | "
                      f"Tenders: {total_accepted}/{total_seen}")

            # 3) Execute unwind schedules
            exec_logs = engine.tick(tick, ticker_configs)
            for log in exec_logs:
                if any(k in log for k in ('COMPLETED', 'SPRINT', 'ENDGAME')):
                    print(f"    [EXEC] {log}")

            # 4) Tender offers
            tick_seen = 0
            tick_accepted = 0
            tenders = api.get_tenders()

            for tender in tenders:
                t = str(tender.get('ticker', ''))
                if t not in ticker_configs:
                    continue
                tick_seen += 1
                tender_id = int(tender.get('tender_id', -1))
                if tender_id in processed_tenders:
                    continue

                tender_action = str(tender.get('action', '')).upper()
                tender_qty = int(tender.get('quantity', 0))
                raw_price = tender.get('price')
                tender_price = float(raw_price) if raw_price is not None else 0.0
                tender_exp = int(tender.get('expires', 0))
                is_fixed = bool(tender.get('is_fixed_bid', True))
                cfg = ticker_configs[t]

                if not risk.can_accept(tender_qty, tender_action, t):
                    continue

                book = api.get_book(t)

                if is_fixed:
                    ev = analyzer.evaluate_private(tender, book, cfg)
                    confidence = ev.get('confidence', 0.0)
                    should_take = (ev['decision'] == 'ACCEPT'
                                   and confidence >= MIN_CONFIDENCE)

                    print(f"\n  {'!'*50}")
                    print(f"  PRIVATE #{tender_id} | {t} {tender_action} "
                          f"{tender_qty:,} @ ${tender_price:.2f} | exp={tender_exp}")
                    print(f"  {ev['decision']} conf={confidence:.0%} | "
                          f"E[pps]=${ev.get('net_pps',0):.4f} | "
                          f"{'TAKING' if should_take else 'SKIP'}")
                    print(f"  {'!'*50}")

                    if should_take:
                        ok = api.accept_tender(tender_id)
                        if ok:
                            print(f"    >> ACCEPTED #{tender_id}")
                            tick_accepted += 1
                            processed_tenders.add(tender_id)
                            handle_post_accept(api, engine, t,
                                               tender_price, tender_action,
                                               tender_qty, tick, cfg)
                    else:
                        # Mark rejected fixed-price tenders so we don't
                        # spam re-evaluate every tick
                        processed_tenders.add(tender_id)
                else:
                    bid_res = analyzer.calculate_auction_bid(tender, book, cfg)
                    print(f"\n  {'*'*50}")
                    print(f"  AUCTION #{tender_id} | {t} {tender_action} "
                          f"{tender_qty:,} | exp={tender_exp}")
                    print(f"  {bid_res['decision']} | {bid_res['reason']}")
                    print(f"  {'*'*50}")

                    if bid_res['decision'] == 'BID':
                        ok = api.accept_tender(tender_id,
                                                price=bid_res['bid_price'])
                        if ok:
                            print(f"    >> BID #{tender_id} @ "
                                  f"${bid_res['bid_price']:.2f}")
                            tick_accepted += 1
                            processed_tenders.add(tender_id)
                            handle_post_accept(api, engine, t,
                                               bid_res['bid_price'],
                                               tender_action, tender_qty,
                                               tick, cfg)
                    else:
                        # Mark rejected auctions to avoid spam
                        processed_tenders.add(tender_id)

            total_seen += tick_seen
            total_accepted += tick_accepted

            # 5) Residual cleanup
            active_tickers = engine.get_active_tickers()
            for t in ticker_configs:
                if t in active_tickers:
                    continue
                pos = risk.positions.get(t, {}).get('position', 0)
                if pos == 0:
                    continue
                actual = api.get_position(t)
                if actual == 0:
                    continue
                close = 'SELL' if actual > 0 else 'BUY'
                engine.create_task(t, close, abs(actual), 0.0, tick,
                                   ticker_configs[t])
                tag = "ENDGAME" if endgame else "RESIDUAL"
                print(f"    [{tag}] {t}: pos={actual:+,}")

            # 6) Log
            active_tasks = engine.get_active_tasks()
            writer.writerow([
                datetime.now().isoformat(timespec="seconds"),
                tick, subheat_num, len(active_tasks),
                sum(t.remaining for t in active_tasks),
                tick_seen, tick_accepted,
                limits['net'], limits['gross'], risk.total_pnl(),
            ])
            log_f.flush()

            elapsed = time.time() - loop_start
            target_sleep = max(0.02, SLEEP_SEC - elapsed)
            if engine.get_active_tasks() or endgame:
                target_sleep = max(0.02, SLEEP_SEC / 2 - elapsed)
            time.sleep(target_sleep)

    finally:
        log_f.close()

    return "ENDED"


# ============================================================
# MAIN
# ============================================================

def setup_logging():
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = f"liquidity_bot_{ts}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_file


def main():
    print(BANNER.format(url=API_BASE_URL, key=API_KEY))
    log_file = setup_logging()
    print(f"Logging to: {log_file}\n")

    api = RITApi()
    ac = AlmgrenChriss()
    analyzer = TenderAnalyzer(api)
    risk = RiskManager()

    # Connection - same as volatility algo
    wait_for_connection(api)

    trader = api.get_trader()
    if trader:
        print(f"Trader: {trader.get('trader_id', '?')} | "
              f"NLV: ${trader.get('nlv', 0):,.2f}")

    print("\nBot ready. Waiting for case...\n")
    print("-" * 80)

    session_results = []
    session_num = 0

    try:
        while True:
            if not wait_for_active(api):
                print("Case stopped. Waiting for next sub-heat...")
                time.sleep(2)
                continue

            session_num += 1
            engine = ExecutionEngine(api, ac)

            # Detect sub-heat
            securities = api.get_securities() or []
            subheat_num, ticker_configs = detect_subheat(securities)

            start_nlv = api.get_nlv()
            case = api.get_case()
            period = case.get("period", "?") if case else "?"

            print(f"\n{'='*60}")
            print(f"  SESSION {session_num} | SUB-HEAT {subheat_num} "
                  f"(period {period})")
            print(f"  Tickers: {', '.join(sorted(ticker_configs.keys()))}")
            for t, cfg in sorted(ticker_configs.items()):
                print(f"    {t}: vol={cfg.volatility} liq={cfg.liquidity} "
                      f"comm=${cfg.commission}")
            print(f"  Start NLV: ${start_nlv:,.2f}")
            print(f"{'='*60}\n")

            run_trading_loop(api, engine, analyzer, risk, ac,
                             ticker_configs, subheat_num)

            end_nlv = api.get_nlv()
            pnl = end_nlv - start_nlv
            session_results.append({
                "session": session_num, "period": period,
                "start_nlv": start_nlv, "end_nlv": end_nlv, "pnl": pnl,
            })

            print(f"\n  Session {session_num} ended.")
            print(f"  P&L: ${pnl:>+,.2f}")
            print(f"\n  --- SESSION HISTORY ---")
            for s in session_results:
                print(f"  S{s['session']:>2} (P{s['period']}): "
                      f"P&L ${s['pnl']:>+10,.2f}")
            total_pnl = sum(s["pnl"] for s in session_results)
            print(f"  {'':->40}")
            print(f"  Total: ${total_pnl:>+,.2f}")
            print(f"{'='*60}\n")

            time.sleep(3)

    except KeyboardInterrupt:
        print("\n\nShutting down...")
        api.cancel_all_orders()
        print("Cancelled all open orders.")
        nlv = api.get_nlv()
        if nlv:
            print(f"Final NLV: ${nlv:,.2f}")
        if session_results:
            total = sum(s["pnl"] for s in session_results)
            print(f"Total P&L: ${total:>+,.2f}")
        print("Goodbye.")


if __name__ == "__main__":
    main()
