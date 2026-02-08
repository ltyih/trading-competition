# -*- coding: utf-8 -*-
"""LT3 Tender Liquidity Bot — Risk management.

Position tracking and limit enforcement.
"""

from typing import Dict, Any

from config import WATCHLIST, POSITION_LIMITS


class RiskManager:
    """Position and risk manager."""

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
        """LT3 convention: net = abs(sum), gross = sum(abs)."""
        net_used = abs(sum(p['position'] for p in self.positions.values()))
        gross_used = sum(abs(p['position']) for p in self.positions.values())
        return {
            'net_ok': net_used <= POSITION_LIMITS['net'],
            'gross_ok': gross_used <= POSITION_LIMITS['gross'],
            'net_used': net_used,
            'gross_used': gross_used,
        }

    def can_accept_tender(self, quantity: int, action: str,
                          ticker: str) -> bool:
        """Check if accepting this tender would breach position limits."""
        current_pos = self.positions.get(ticker, {}).get('position', 0)
        if action.upper() == 'BUY':
            new_pos = current_pos + quantity
        else:
            new_pos = current_pos - quantity

        # Simulate new positions
        sim_net = 0
        sim_gross = 0
        for t, p in self.positions.items():
            pos = new_pos if t == ticker else p['position']
            sim_net += pos
            sim_gross += abs(pos)
        # If ticker not yet in positions, add it
        if ticker not in self.positions:
            sim_net += new_pos
            sim_gross += abs(new_pos)

        return (abs(sim_net) <= POSITION_LIMITS['net']
                and sim_gross <= POSITION_LIMITS['gross'])
