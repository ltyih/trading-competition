# -*- coding: utf-8 -*-
"""LT3 Tender Liquidity Bot (CRZY/TAME)

Single-file script that:
- Monitors CRZY/TAME
- Evaluates tender offers using full order book depth (no speculation)
- Accepts/declines tenders based on coverability and net profit after costs
- After accepting, generates a NON-SPECULATIVE unwind LIMIT order plan:
    Phase 1: marketable limits that lock >= break-even
    Phase 2: passive limits at break-even (do not lock losses)

Notes
-----
This script intentionally avoids price prediction / alpha signals.
All trading decisions are justified as inventory unwind from accepted tenders.

You MUST adapt the tender endpoints to match your actual RIT API.

@author: oliverzhoumac
Created on Fri Jan 16 22:33:07 2026
"""

import signal
import requests
import pandas as pd
import numpy as np
from time import sleep, time
from typing import Dict, List, Any

# ========== Configuration ==========
API_KEY = {'X-API-Key': 'ZXA2ZHL2'}
BASE_URL = 'http://localhost:9999/v1'
shutdown = False

WATCHLIST = {'CRZY', 'TAME'}

# Trading parameters (LT3)
COMMISSION_PER_SHARE = 0.02
MAX_ORDER_SIZE = {'CRZY': 25000, 'TAME': 10000}
POSITION_LIMITS = {'net': 100000, 'gross': 250000}
TENDER_DECISION_TIME = 30  # seconds (not used for throttling decisions anymore; evaluation runs continuously)

# Risk / execution knobs
BOOK_LIMIT = 50
MIN_PROFIT_PER_SHARE = 0.00
MAX_DEPTH_RATIO = 0.7
MIN_NET_PROFIT_PER_SHARE = 0.01

SLEEP_SEC = 0.5

EPS = 1e-9
DEBUG_BOOK = False  # keep False to avoid verbose book outputs


def synthetic_book_from_quotes(bid: float, bid_size: int, ask: float, ask_size: int) -> dict:
    """Fallback when /securities/book returns empty.

    Uses top-of-book quotes from /securities to create a 1-level book.
    This is NOT speculative; it reflects the best visible liquidity.
    """
    book = {'bid': [], 'ask': []}
    if bid and bid > 0 and bid_size and bid_size > 0:
        book['bid'].append({'price': float(bid), 'quantity': int(bid_size), 'quantity_filled': 0})
    if ask and ask > 0 and ask_size and ask_size > 0:
        book['ask'].append({'price': float(ask), 'quantity': int(ask_size), 'quantity_filled': 0})
    return book


# ========== Exceptions ==========
class ApiException(Exception):
    pass


# ========== Core API functions ==========
def get_tick(session) -> int:
    """Get current tick."""
    resp = session.get(f'{BASE_URL}/case')
    if resp.ok:
        return int(resp.json().get('tick', 0))
    raise ApiException('Failed to get tick')


def get_securities(session) -> list:
    """Get securities info."""
    resp = session.get(f'{BASE_URL}/securities')
    if resp.ok:
        return resp.json()
    raise ApiException('Failed to get securities data')


def get_order_book(session, ticker: str, limit: int = BOOK_LIMIT) -> dict:
    """Get full order book.

    Some builds return 'bids'/'asks'. Normalize to {'bid': [...], 'ask': [...]}.
    """
    resp = session.get(f'{BASE_URL}/securities/book', params={'ticker': ticker, 'limit': limit})
    if not resp.ok:
        raise ApiException(f'Failed to get order book for {ticker}')

    book = resp.json()
    if isinstance(book, dict) and ('bids' in book or 'asks' in book):
        return {
            'bid': book.get('bids', []) or [],
            'ask': book.get('asks', []) or [],
        }
    return {
        'bid': book.get('bid', []) if isinstance(book, dict) else [],
        'ask': book.get('ask', []) if isinstance(book, dict) else [],
    }


# ---- Tender endpoints (RIT Client REST API v1.0.3) ----
def get_tender_offers(session) -> list:
    """Gets a list of all active tenders."""
    resp = session.get(f'{BASE_URL}/tenders')
    if resp.ok:
        return resp.json()
    return []


def accept_tender(session, tender_id: int, price: float = None) -> bool:
    """Accept the tender."""
    params = {}
    if price is not None:
        params['price'] = float(price)
    resp = session.post(f'{BASE_URL}/tenders/{int(tender_id)}', params=params)
    return resp.ok


def decline_tender(session, tender_id: int) -> bool:
    """Decline the tender."""
    resp = session.delete(f'{BASE_URL}/tenders/{int(tender_id)}')
    return resp.ok


# ========== Liquidity analysis ==========
class LiquidityAnalyzer:
    """Core liquidity analysis engine (non-speculative: liquidity + costs only)."""

    def calculate_spread_pct(self, bid: float, ask: float) -> float:
        if bid > 0 and ask > 0:
            return (ask - bid) * 100.0 / max(((ask + bid) / 2.0), EPS)
        return 0.0

    def calculate_depth_imbalance(self, bid_size: int, ask_size: int) -> float:
        total = bid_size + ask_size
        return (bid_size - ask_size) / max(total, 1)

    @staticmethod
    def _available_qty(level: Dict[str, Any]) -> int:
        """Best-effort available quantity at a book level."""
        q = int(level.get('quantity', 0))
        qf = int(level.get('quantity_filled', 0))
        return max(0, q - qf)

    def estimate_impact_cost(self, order_book: dict, quantity: int, side: str) -> dict:
        """Estimate average execution price vs best level for given qty.

        side: 'BUY' or 'SELL' meaning *your* unwind action.
        - SELL: consumes bids
        - BUY : consumes asks

        Returns: {'avg_price', 'impact_pct', 'can_execute', 'executed_qty'}
        """
        side = side.upper()
        if side == 'SELL':
            levels = order_book.get('bid', [])
        else:
            levels = order_book.get('ask', [])

        ref_price = float(levels[0].get('price', 0.0)) if levels else 0.0
        if not levels or ref_price <= 0:
            return {'avg_price': 0.0, 'impact_pct': 0.0, 'can_execute': False, 'executed_qty': 0}

        remaining = int(quantity)
        total_cost = 0.0
        executed = 0

        for lvl in levels:
            if remaining <= 0:
                break
            px = float(lvl.get('price', 0.0))
            avail = self._available_qty(lvl)
            if avail <= 0 or px <= 0:
                continue
            take = min(avail, remaining)
            total_cost += take * px
            executed += take
            remaining -= take

        # Conservative fill beyond last visible level if insufficient depth
        if remaining > 0:
            worst_mult = 0.95 if side == 'SELL' else 1.05
            worst_px = float(levels[-1].get('price', ref_price)) * worst_mult
            total_cost += remaining * worst_px
            executed += remaining
            remaining = 0

        avg_price = total_cost / max(quantity, 1)
        if side == 'SELL':
            impact_pct = (ref_price - avg_price) / max(ref_price, EPS) * 100.0
        else:
            impact_pct = (avg_price - ref_price) / max(ref_price, EPS) * 100.0

        return {'avg_price': avg_price, 'impact_pct': impact_pct, 'can_execute': True, 'executed_qty': executed}

    def _total_depth(self, order_book: dict, side: str) -> int:
        """Total visible depth on the side you will consume."""
        side = side.upper()
        key = 'bid' if side == 'SELL' else 'ask'
        total = 0
        for lvl in order_book.get(key, []):
            total += self._available_qty(lvl)
        return total

    def evaluate_tender_offer(self, tender: dict, order_book: dict) -> dict:
        """Evaluate tender without speculation (liquidity + costs only).

        Interpretation:
          - action == 'BUY'  => you BUY via tender (long)  => unwind by SELL (consume bids)
          - action == 'SELL' => you SELL via tender (short) => unwind by BUY  (consume asks)
        """
        ticker = str(tender.get('ticker') or tender.get('caption') or '')
        if not ticker:
            ticker = str(tender.get('caption', ''))

        tender_action = str(tender.get('action', '')).upper()  # YOUR action
        tender_price = float(tender.get('price', 0.0))
        quantity = int(tender.get('quantity', 0))

        # Determine unwind side and market reference (internal only; do not print)
        if tender_action == 'BUY':
            close_action = 'SELL'
            ref_px = float(order_book.get('bid', [{}])[0].get('price', 0.0))
        elif tender_action == 'SELL':
            close_action = 'BUY'
            ref_px = float(order_book.get('ask', [{}])[0].get('price', 0.0))
        else:
            return {
                'decision': 'REJECT',
                'reason': "Unknown tender action",
                'confidence': 0.0,
                'metrics': {
                    'net_profit_per_share': 0.0,
                    'depth_ratio': 1.0,
                    'close_action': 'UNKNOWN',
                    'total_depth': 0,
                }
            }

        total_depth = self._total_depth(order_book, close_action)

        if ref_px <= 0.0:
            return {
                'decision': 'REJECT',
                'reason': 'Missing reference price (empty/invalid order book)',
                'confidence': 0.0,
                'metrics': {
                    'net_profit_per_share': 0.0,
                    'depth_ratio': 1.0,
                    'close_action': close_action,
                    'total_depth': total_depth,
                }
            }
        if total_depth <= 0:
            return {
                'decision': 'REJECT',
                'reason': 'Zero visible depth (cannot unwind)',
                'confidence': 0.0,
                'metrics': {
                    'net_profit_per_share': 0.0,
                    'depth_ratio': 1.0,
                    'close_action': close_action,
                    'total_depth': total_depth,
                }
            }

        theoretical_spread = abs(ref_px - tender_price)
        theoretical_profit = theoretical_spread * quantity

        impact_result = self.estimate_impact_cost(order_book, quantity, close_action)

        commission = COMMISSION_PER_SHARE * quantity
        impact_cost = abs(impact_result['impact_pct'] / 100.0 * ref_px * quantity)

        net_profit = theoretical_profit - impact_cost - commission
        net_profit_per_share = net_profit / max(quantity, 1)

        depth_ratio = quantity / max(total_depth, 1)

        decision = 'REJECT'
        reason = ''
        confidence = 0.0

        if depth_ratio > MAX_DEPTH_RATIO:
            decision = 'REJECT'
            reason = 'Liquidity risk too high (tender too large vs visible depth)'
        elif net_profit_per_share < 0:
            decision = 'REJECT'
            reason = 'Negative expected net profit after impact + commission'
        elif net_profit_per_share < MIN_NET_PROFIT_PER_SHARE:
            decision = 'REJECT'
            reason = 'Expected net profit per share too thin'
        else:
            decision = 'ACCEPT'
            reason = 'Meets liquidity coverage and net profit thresholds'
            confidence = float(min(0.95, max(0.0, net_profit_per_share / max(tender_price, EPS) * 200))) * (1 - min(1.0, depth_ratio))

        return {
            'decision': decision,
            'reason': reason,
            'confidence': confidence,
            'metrics': {
                # Keep metrics for internal use (do not print prices)
                'theoretical_profit': theoretical_profit,
                'impact_cost': impact_cost,
                'commission': commission,
                'net_profit': net_profit,
                'net_profit_per_share': net_profit_per_share,
                'depth_ratio': depth_ratio,
                'impact_pct': impact_result['impact_pct'],
                'avg_close_price': impact_result['avg_price'],
                'ref_px': ref_px,
                'close_action': close_action,
                'total_depth': total_depth,
            }
        }


# ========== Position & risk management ==========
class RiskManager:
    """Risk manager."""

    def __init__(self):
        self.positions: Dict[str, Dict[str, Any]] = {}

    def update_positions(self, securities_data: list):
        for sec in securities_data:
            t = str(sec.get('ticker'))
            if t not in WATCHLIST:
                continue
            self.positions[t] = {
                'position': int(sec.get('position', 0)),
                'last': float(sec.get('last', 0.0)),
                'bid': float(sec.get('bid', 0.0)),
                'ask': float(sec.get('ask', 0.0)),
                'bid_size': int(sec.get('bid_size', 0)),
                'ask_size': int(sec.get('ask_size', 0)),
            }

    def check_position_limits(self) -> Dict[str, Any]:
        """LT3 convention:
        - net_used = abs(sum positions)
        - gross_used = sum abs positions
        """
        net_used = abs(sum(p['position'] for p in self.positions.values()))
        gross_used = sum(abs(p['position']) for p in self.positions.values())
        return {
            'net_ok': net_used <= POSITION_LIMITS['net'],
            'gross_ok': gross_used <= POSITION_LIMITS['gross'],
            'net_used': net_used,
            'gross_used': gross_used,
        }


# ========== Execution engine ==========
class ExecutionEngine:
    """Unwind execution engine."""

    def __init__(self, session):
        self.session = session
        self.analyzer = LiquidityAnalyzer()

    def send_limit_order(self, ticker: str, action: str, quantity: int, price: float) -> bool:
        """Place a LIMIT order using RIT v1.0.3 semantics."""
        payload = {
            'ticker': ticker,
            'type': 'LIMIT',
            'quantity': int(quantity),
            'action': action.upper(),
            'price': float(price),
        }

        resp = self.session.post(f'{BASE_URL}/orders', params=payload)
        if resp.status_code == 429:
            try:
                wait = float(resp.json().get('wait', 0.0))
            except Exception:
                wait = float(resp.headers.get('Retry-After', 0.0) or 0.0)
            if wait > 0:
                sleep(wait)
            resp = self.session.post(f'{BASE_URL}/orders', params=payload)

        return resp.ok

    def build_unwind_limit_plan(
        self,
        ticker: str,
        tender_price: float,
        tender_action: str,
        quantity: int,
        order_book: dict,
        min_profit_per_share: float = MIN_PROFIT_PER_SHARE,
    ) -> Dict[str, Any]:
        """Non-speculative unwind plan (uses break-even constraints only)."""
        fee = COMMISSION_PER_SHARE
        tender_action = str(tender_action).upper()

        if tender_action == 'BUY':
            close_action = 'SELL'
            levels = order_book.get('bid', [])
            breakeven = tender_price + fee + min_profit_per_share
        elif tender_action == 'SELL':
            close_action = 'BUY'
            levels = order_book.get('ask', [])
            breakeven = tender_price - fee - min_profit_per_share
        else:
            return {
                'ticker': ticker,
                'close_action': 'UNKNOWN',
                'breakeven_price': 0.0,
                'reference_price': 0.0,
                'immediate_orders': [],
                'passive_orders': [],
            }

        max_order = int(MAX_ORDER_SIZE.get(ticker, 10000))

        def avail(level):
            q = int(level.get('quantity', 0))
            qf = int(level.get('quantity_filled', 0))
            return max(0, q - qf)

        immediate_orders: List[Dict[str, Any]] = []
        passive_orders: List[Dict[str, Any]] = []

        remaining = int(quantity)

        for lvl in levels:
            if remaining <= 0:
                break
            px = float(lvl.get('price', 0.0))
            if px <= 0:
                continue
            a = avail(lvl)
            if a <= 0:
                continue

            ok = (px >= breakeven) if close_action == 'SELL' else (px <= breakeven)
            if not ok:
                break

            take = min(a, remaining, max_order)
            immediate_orders.append({
                'ticker': ticker,
                'action': close_action,
                'type': 'LIMIT',
                'quantity': int(take),
                'price': float(px),
                'note': 'marketable_limit_lock_no_loss',
            })
            remaining -= take

        while remaining > 0:
            take = min(remaining, max_order)
            passive_orders.append({
                'ticker': ticker,
                'action': close_action,
                'type': 'LIMIT',
                'quantity': int(take),
                'price': float(breakeven),
                'note': 'passive_limit_breakeven',
            })
            remaining -= take

        ref_px = float(levels[0].get('price', 0.0)) if levels else 0.0

        return {
            'ticker': ticker,
            'close_action': close_action,
            'breakeven_price': float(breakeven),
            'reference_price': float(ref_px),
            'immediate_orders': immediate_orders,
            'passive_orders': passive_orders,
        }

    def print_plan(self, plan: Dict[str, Any]):
        """Plan printer without any prices (only actions and quantities)."""
        print("\n=== UNWIND LIMIT PLAN (non-speculative) ===")
        print(f"Ticker: {plan['ticker']}")
        print(f"Close action: {plan['close_action']}")
        print("\n[Phase 1] Immediate marketable limits (lock no-loss):")
        if not plan['immediate_orders']:
            print("  (none)")
        for o in plan['immediate_orders']:
            print(f"  {o['action']} {o['quantity']:,}  ({o['note']})")
        print("\n[Phase 2] Passive limits at break-even:")
        if not plan['passive_orders']:
            print("  (none)")
        for o in plan['passive_orders']:
            print(f"  {o['action']} {o['quantity']:,}  ({o['note']})")

    def execute_plan(self, plan: Dict[str, Any], auto_send: bool = False):
        self.print_plan(plan)
        if not auto_send:
            return
        for o in plan['immediate_orders']:
            self.send_limit_order(o['ticker'], o['action'], o['quantity'], o['price'])
        for o in plan['passive_orders']:
            self.send_limit_order(o['ticker'], o['action'], o['quantity'], o['price'])


# ========== Main ==========
def main():
    with requests.Session() as session:
        session.headers.update(API_KEY)

        analyzer = LiquidityAnalyzer()
        risk_manager = RiskManager()
        execution_engine = ExecutionEngine(session)

        # Cache to avoid spamming identical prints (still evaluates every loop)
        last_tender_summary: Dict[int, str] = {}

        print("=" * 80)
        print("LT3 Liquidity Risk Management System (CRZY/TAME) Started")
        print("=" * 80)

        tick = 0
        while tick < 600 and not shutdown:
            try:
                tick = get_tick(session)

                if tick == 0:
                    print("Waiting for case to start...")
                    sleep(1)
                    continue

                # 1) Market data
                securities_data = get_securities(session)
                risk_manager.update_positions(securities_data)

                # 2) Limits (do not print any prices)
                limits = risk_manager.check_position_limits()
                limits_status = "OK" if (limits['net_ok'] and limits['gross_ok']) else "WARN"
                print(f"\nTick: {tick} | Position limits: {limits_status} (net: {limits['net_used']}/{POSITION_LIMITS['net']}, gross: {limits['gross_used']}/{POSITION_LIMITS['gross']})")

                # 3) Tender offers (evaluate continuously)
                tenders = get_tender_offers(session)
                for tender in tenders:
                    ticker = str(tender.get('ticker'))
                    if ticker not in WATCHLIST:
                        continue

                    tender_id = int(tender.get('tender_id', -1))
                    tender_action = str(tender.get('action', 'UNKNOWN')).upper()
                    tender_qty = int(tender.get('quantity', 0))
                    tender_exp = int(tender.get('expires', 0))

                    # Order book
                    book = get_order_book(session, ticker)

                    # Fallback when normalized book is still empty
                    if len(book.get('bid', [])) == 0 and len(book.get('ask', [])) == 0:
                        q = risk_manager.positions.get(ticker)
                        if DEBUG_BOOK:
                            print(f"[DEBUG] Empty /securities/book for {ticker}. Using synthetic book from quotes.")
                        if q:
                            book = synthetic_book_from_quotes(q['bid'], q['bid_size'], q['ask'], q['ask_size'])

                    evaluation = analyzer.evaluate_tender_offer(tender, book)

                    # Build a price-free summary string for throttled printing
                    m = evaluation.get('metrics', {})
                    summary = (
                        f"id={tender_id}|t={ticker}|a={tender_action}|q={tender_qty}|exp={tender_exp}"
                        f"|dec={evaluation['decision']}|conf={evaluation['confidence']:.1%}"
                        f"|depth={m.get('total_depth', 0)}|ratio={m.get('depth_ratio', 0.0):.1%}"
                        f"|reason={evaluation['reason']}"
                    )

                    if last_tender_summary.get(tender_id) != summary:
                        print("\n" + "!" * 80)
                        print(f"Tender #{tender_id} | Ticker: {ticker} | Action: {tender_action} | Qty: {tender_qty:,} | Expires(tick): {tender_exp}")
                        print(f"Decision: {evaluation['decision']} | Confidence: {evaluation['confidence']:.1%}")
                        print(f"Reason: {evaluation['reason']}")
                        print(f"Visible depth: {m.get('total_depth', 0):,} | Depth ratio: {m.get('depth_ratio', 0.0):.1%}")
                        print("!" * 80)
                        last_tender_summary[tender_id] = summary

                    # Optional: auto accept based on confidence (still no price outputs)
                    if evaluation['decision'] == 'ACCEPT' and evaluation['confidence'] > 0.5:
                        ok = accept_tender(session, tender_id, price=None)
                        if ok:
                            plan = execution_engine.build_unwind_limit_plan(
                                ticker=ticker,
                                tender_price=float(tender.get('price', 0.0)),
                                tender_action=tender_action,
                                quantity=tender_qty,
                                order_book=book,
                                min_profit_per_share=MIN_PROFIT_PER_SHARE,
                            )
                            execution_engine.execute_plan(plan, auto_send=False)

                sleep(SLEEP_SEC)

            except ApiException as e:
                print(f"API error: {e}")
                sleep(1)
            except KeyboardInterrupt:
                print("\nUser interrupted. Exiting...")
                break
            except Exception as e:
                print(f"Unexpected error: {e}")
                sleep(1)

    print("\n" + "=" * 80)
    print("Trading finished")
    print("=" * 80)


if __name__ == '__main__':
    def _sig_handler(signum, frame):
        global shutdown
        shutdown = True
        print("\nShutdown signal received...")

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    main()
