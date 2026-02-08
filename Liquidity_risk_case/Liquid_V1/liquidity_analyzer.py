# -*- coding: utf-8 -*-
"""LT3 Tender Liquidity Bot — Liquidity analysis.

Non-speculative evaluation of tender offers based on order-book depth,
impact cost, and commission.
"""

from typing import Dict, Any

from config import (
    EPS,
    COMMISSION_PER_SHARE,
    MAX_DEPTH_RATIO,
    MIN_NET_PROFIT_PER_SHARE,
    AUCTION_PROFIT_MARGIN,
    AUCTION_SAFETY_MARGIN,
)


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
        q = int(level.get('quantity', 0))
        qf = int(level.get('quantity_filled', 0))
        return max(0, q - qf)

    def estimate_impact_cost(self, order_book: dict, quantity: int,
                             side: str) -> dict:
        """Estimate average execution price vs best level for given qty.

        side: 'BUY' or 'SELL' meaning *your* unwind action.
        """
        side = side.upper()
        levels = order_book.get('bid' if side == 'SELL' else 'ask', [])

        ref_price = float(levels[0].get('price', 0.0)) if levels else 0.0
        if not levels or ref_price <= 0:
            return {'avg_price': 0.0, 'impact_pct': 0.0,
                    'can_execute': False, 'executed_qty': 0}

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

        # Conservative fill beyond last visible level
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

        return {'avg_price': avg_price, 'impact_pct': impact_pct,
                'can_execute': True, 'executed_qty': executed}

    def _total_depth(self, order_book: dict, side: str) -> int:
        side = side.upper()
        key = 'bid' if side == 'SELL' else 'ask'
        total = 0
        for lvl in order_book.get(key, []):
            total += self._available_qty(lvl)
        return total

    def evaluate_tender_offer(self, tender: dict,
                              order_book: dict) -> dict:
        """Evaluate tender without speculation (liquidity + costs only)."""
        ticker = str(tender.get('ticker') or tender.get('caption') or '')
        if not ticker:
            ticker = str(tender.get('caption', ''))

        tender_action = str(tender.get('action', '')).upper()
        tender_price = float(tender.get('price', 0.0))
        quantity = int(tender.get('quantity', 0))

        if tender_action == 'BUY':
            close_action = 'SELL'
            ref_px = float(
                order_book.get('bid', [{}])[0].get('price', 0.0))
        elif tender_action == 'SELL':
            close_action = 'BUY'
            ref_px = float(
                order_book.get('ask', [{}])[0].get('price', 0.0))
        else:
            return self._reject('Unknown tender action', 'UNKNOWN', 0)

        total_depth = self._total_depth(order_book, close_action)

        if ref_px <= 0.0:
            return self._reject(
                'Missing reference price (empty/invalid order book)',
                close_action, total_depth)
        if total_depth <= 0:
            return self._reject(
                'Zero visible depth (cannot unwind)',
                close_action, total_depth)

        theoretical_spread = abs(ref_px - tender_price)
        theoretical_profit = theoretical_spread * quantity

        impact_result = self.estimate_impact_cost(
            order_book, quantity, close_action)

        commission = COMMISSION_PER_SHARE * quantity
        impact_cost = abs(
            impact_result['impact_pct'] / 100.0 * ref_px * quantity)

        net_profit = theoretical_profit - impact_cost - commission
        net_profit_per_share = net_profit / max(quantity, 1)
        depth_ratio = quantity / max(total_depth, 1)

        decision = 'REJECT'
        reason = ''
        confidence = 0.0

        if depth_ratio > MAX_DEPTH_RATIO:
            reason = 'Liquidity risk too high (tender too large vs visible depth)'
        elif net_profit_per_share < 0:
            reason = 'Negative expected net profit after impact + commission'
        elif net_profit_per_share < MIN_NET_PROFIT_PER_SHARE:
            reason = 'Expected net profit per share too thin'
        else:
            decision = 'ACCEPT'
            reason = 'Meets liquidity coverage and net profit thresholds'
            confidence = (float(min(0.95, max(
                0.0, net_profit_per_share / max(tender_price, EPS) * 200)))
                * (1 - min(1.0, depth_ratio)))

        return {
            'decision': decision,
            'reason': reason,
            'confidence': confidence,
            'metrics': {
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

    def calculate_auction_bid_price(
        self,
        tender: dict,
        order_book: dict,
        profit_margin: float = AUCTION_PROFIT_MARGIN,
        safety_margin: float = AUCTION_SAFETY_MARGIN,
    ) -> dict:
        """Calculate optimal bid price for a competitive auction tender.

        Works backwards from estimated unwind cost to determine the most
        aggressive tender price that still yields a profit.

        Returns dict with 'decision' ('BID' or 'REJECT'), 'bid_price',
        'breakeven_price', and 'metrics'.
        """
        tender_action = str(tender.get('action', '')).upper()
        quantity = int(tender.get('quantity', 0))

        if tender_action == 'BUY':
            close_action = 'SELL'
        elif tender_action == 'SELL':
            close_action = 'BUY'
        else:
            return {'decision': 'REJECT', 'bid_price': 0.0,
                    'breakeven_price': 0.0, 'reason': 'Unknown action',
                    'metrics': {'close_action': 'UNKNOWN', 'total_depth': 0}}

        total_depth = self._total_depth(order_book, close_action)
        depth_ratio = quantity / max(total_depth, 1)

        if total_depth <= 0:
            return {'decision': 'REJECT', 'bid_price': 0.0,
                    'breakeven_price': 0.0,
                    'reason': 'Zero visible depth',
                    'metrics': {'close_action': close_action,
                                'total_depth': 0, 'depth_ratio': 1.0}}
        if depth_ratio > MAX_DEPTH_RATIO:
            return {'decision': 'REJECT', 'bid_price': 0.0,
                    'breakeven_price': 0.0,
                    'reason': 'Liquidity risk too high',
                    'metrics': {'close_action': close_action,
                                'total_depth': total_depth,
                                'depth_ratio': depth_ratio}}

        # Estimate average unwind price by walking the book
        impact = self.estimate_impact_cost(order_book, quantity, close_action)
        avg_unwind = impact['avg_price']

        if avg_unwind <= 0 or not impact['can_execute']:
            return {'decision': 'REJECT', 'bid_price': 0.0,
                    'breakeven_price': 0.0,
                    'reason': 'Cannot estimate unwind price',
                    'metrics': {'close_action': close_action,
                                'total_depth': total_depth,
                                'depth_ratio': depth_ratio}}

        # Calculate breakeven and bid price
        fee = COMMISSION_PER_SHARE
        if tender_action == 'BUY':
            # You buy at bid_price, sell at avg_unwind
            # Profit = (avg_unwind - bid_price - fee) * qty
            breakeven = avg_unwind - fee
            bid_price = breakeven - profit_margin
        else:  # SELL
            # You sell at bid_price, buy back at avg_unwind
            # Profit = (bid_price - avg_unwind - fee) * qty
            breakeven = avg_unwind + fee
            bid_price = breakeven + profit_margin

        # Sanity: expected profit must be positive
        if tender_action == 'BUY':
            expected_profit_ps = avg_unwind - bid_price - fee
        else:
            expected_profit_ps = bid_price - avg_unwind - fee

        if expected_profit_ps <= 0:
            return {'decision': 'REJECT', 'bid_price': 0.0,
                    'breakeven_price': breakeven,
                    'reason': 'No profitable bid possible',
                    'metrics': {'close_action': close_action,
                                'total_depth': total_depth,
                                'depth_ratio': depth_ratio,
                                'avg_unwind_price': avg_unwind}}

        return {
            'decision': 'BID',
            'bid_price': round(bid_price, 2),
            'breakeven_price': round(breakeven, 2),
            'reason': 'Profitable auction bid calculated',
            'expected_profit_per_share': round(expected_profit_ps, 4),
            'metrics': {
                'close_action': close_action,
                'total_depth': total_depth,
                'depth_ratio': depth_ratio,
                'avg_unwind_price': avg_unwind,
                'impact_pct': impact['impact_pct'],
            }
        }

    @staticmethod
    def _reject(reason: str, close_action: str,
                total_depth: int) -> dict:
        return {
            'decision': 'REJECT',
            'reason': reason,
            'confidence': 0.0,
            'metrics': {
                'net_profit_per_share': 0.0,
                'depth_ratio': 1.0,
                'close_action': close_action,
                'total_depth': total_depth,
            }
        }
