# -*- coding: utf-8 -*-
"""Merger Arbitrage Probability Monitor

Real-time calculator that displays market-implied deal completion probabilities
for all 5 M&A deals based on current market prices.

Logic:
------
Target price formula: P = p×K + (1-p)×V
where:
  p = probability of deal closing
  K = deal value (offer price or stock-for-stock value)
  V = standalone value (inferred at t=0, then fixed)

At t=0: Calculate V using initial probability p₀
At any t: Back out current p from observed price P

@author: merger_arb_team
"""

import requests
import signal
from time import sleep
from typing import Dict, List, Any

# ========== Configuration ==========
API_KEY = {'X-API-Key': 'JFDNCO6I'}
BASE_URL = 'http://localhost:9999/v1'
shutdown = False

# Deal structures from case description
DEALS = {
    'D1': {
        'name': 'D1 (TGX/PHR)',
        'target': 'TGX',
        'acquirer': 'PHR',
        'structure': 'ALL_CASH',
        'cash_component': 50.00,
        'exchange_ratio': 0.0,
        'p0': 0.70,  # Initial completion probability
        'starting_target_price': 43.70,
        'starting_acquirer_price': 47.50,
    },
    'D2': {
        'name': 'D2 (BYL/CLD)',
        'target': 'BYL',
        'acquirer': 'CLD',
        'structure': 'STOCK_FOR_STOCK',
        'cash_component': 0.0,
        'exchange_ratio': 0.75,
        'p0': 0.55,
        'starting_target_price': 43.50,
        'starting_acquirer_price': 79.30,
    },
    'D3': {
        'name': 'D3 (GGD/PNR)',
        'target': 'GGD',
        'acquirer': 'PNR',
        'structure': 'MIXED',
        'cash_component': 33.00,
        'exchange_ratio': 0.20,
        'p0': 0.50,
        'starting_target_price': 31.50,
        'starting_acquirer_price': 59.80,
    },
    'D4': {
        'name': 'D4 (FSR/ATB)',
        'target': 'FSR',
        'acquirer': 'ATB',
        'structure': 'ALL_CASH',
        'cash_component': 40.00,
        'exchange_ratio': 0.0,
        'p0': 0.38,
        'starting_target_price': 30.50,
        'starting_acquirer_price': 62.20,
    },
    'D5': {
        'name': 'D5 (SPK/EEC)',
        'target': 'SPK',
        'acquirer': 'EEC',
        'structure': 'STOCK_FOR_STOCK',
        'cash_component': 0.0,
        'exchange_ratio': 1.20,
        'p0': 0.45,
        'starting_target_price': 52.80,
        'starting_acquirer_price': 48.00,
    },
}

SLEEP_SEC = 0.5


# ========== API Functions ==========
class ApiException(Exception):
    pass


def get_tick(session) -> int:
    """Get current tick."""
    resp = session.get(f'{BASE_URL}/case')
    if resp.ok:
        return int(resp.json().get('tick', 0))
    raise ApiException('Failed to get tick')


def get_securities(session) -> list:
    """Get securities info with current prices."""
    resp = session.get(f'{BASE_URL}/securities')
    if resp.ok:
        return resp.json()
    raise ApiException('Failed to get securities data')


# ========== Probability Calculator ==========
class MergerArbCalculator:
    """Calculate market-implied deal completion probabilities."""
    
    def __init__(self):
        self.standalone_values = {}  # V for each deal (calculated once at t=0)
        self.initialized = False
    
    def calculate_deal_value(self, deal_info: dict, acquirer_price: float) -> float:
        """Calculate current deal value K.
        
        For all-cash: K = cash_component
        For stock-for-stock: K = exchange_ratio × acquirer_price
        For mixed: K = cash_component + exchange_ratio × acquirer_price
        """
        cash = deal_info['cash_component']
        ratio = deal_info['exchange_ratio']
        return cash + (ratio * acquirer_price)
    
    def initialize_standalone_values(self, market_data: Dict[str, float]):
        """Calculate and store standalone values V for all deals using t=0 prices.
        
        Formula: V = (P₀ - p₀ × K₀) / (1 - p₀)
        """
        print("\n" + "="*80)
        print("INITIALIZING STANDALONE VALUES (t=0)")
        print("="*80)
        
        for deal_id, deal in DEALS.items():
            target = deal['target']
            acquirer = deal['acquirer']
            
            # Get initial prices (use starting prices from case if not in market data)
            P0 = market_data.get(target, deal['starting_target_price'])
            acquirer_price = market_data.get(acquirer, deal['starting_acquirer_price'])
            
            # Calculate initial deal value K₀
            K0 = self.calculate_deal_value(deal, acquirer_price)
            
            # Calculate standalone value V (stays fixed)
            p0 = deal['p0']
            V = (P0 - p0 * K0) / (1 - p0)
            
            self.standalone_values[deal_id] = V
            
            print(f"{deal['name']}: V = ${V:.2f} (P₀=${P0:.2f}, K₀=${K0:.2f}, p₀={p0:.1%})")
        
        print("="*80 + "\n")
        self.initialized = True
    
    def calculate_implied_probability(
        self, 
        deal_id: str, 
        target_price: float, 
        acquirer_price: float
    ) -> Dict[str, Any]:
        """Calculate market-implied completion probability from current prices.
        
        Formula: p = (P - V) / (K - V)
        
        Returns dict with probability and supporting metrics.
        """
        deal = DEALS[deal_id]
        
        # Get standalone value (calculated at t=0)
        V = self.standalone_values.get(deal_id, 0.0)
        
        # Calculate current deal value K
        K = self.calculate_deal_value(deal, acquirer_price)
        
        # Back out implied probability
        # p = (P - V) / (K - V)
        denominator = K - V
        
        if abs(denominator) < 0.01:  # Avoid division by zero
            p_implied = 0.5  # Default to 50% if calculation is unstable
            stable = False
        else:
            p_implied = (target_price - V) / denominator
            stable = True
            
            # Clamp probability to [0, 1]
            p_implied = max(0.0, min(1.0, p_implied))
        
        return {
            'probability': p_implied,
            'deal_value': K,
            'standalone_value': V,
            'target_price': target_price,
            'acquirer_price': acquirer_price,
            'spread': K - target_price,  # Deal spread (opportunity if positive)
            'stable': stable,
        }
    
    def calculate_all_probabilities(self, market_data: Dict[str, float]) -> Dict[str, Dict]:
        """Calculate implied probabilities for all deals."""
        if not self.initialized:
            self.initialize_standalone_values(market_data)
        
        results = {}
        
        for deal_id, deal in DEALS.items():
            target = deal['target']
            acquirer = deal['acquirer']
            
            # Get current prices from market data
            target_price = market_data.get(target)
            acquirer_price = market_data.get(acquirer)
            
            if target_price is None or acquirer_price is None:
                # Skip if prices not available
                continue
            
            # Calculate implied probability
            result = self.calculate_implied_probability(
                deal_id, 
                target_price, 
                acquirer_price
            )
            result['deal_name'] = deal['name']
            result['structure'] = deal['structure']
            
            results[deal_id] = result
        
        return results


# ========== Display Functions ==========
def display_probabilities(tick: int, probabilities: Dict[str, Dict]):
    """Display all deal probabilities in a clean table format."""
    print("\n" + "="*100)
    print(f"TICK {tick:3d} | MARKET-IMPLIED DEAL COMPLETION PROBABILITIES")
    print("="*100)
    print(f"{'Deal':<15} {'Probability':<12} {'Deal Value':<12} {'Spread':<12} {'Structure':<15}")
    print("-"*100)
    
    for deal_id in ['D1', 'D2', 'D3', 'D4', 'D5']:
        if deal_id not in probabilities:
            continue
        
        p = probabilities[deal_id]
        prob_str = f"{p['probability']:.1%}" if p['stable'] else "UNSTABLE"
        
        print(f"{p['deal_name']:<15} {prob_str:<12} ${p['deal_value']:>8.2f}   ${p['spread']:>8.2f}   {p['structure']:<15}")
    
    print("="*100 + "\n")


# ========== Main Loop ==========
def main():
    """Main monitoring loop."""
    with requests.Session() as session:
        session.headers.update(API_KEY)
        
        calculator = MergerArbCalculator()
        
        print("\n" + "="*100)
        print("MERGER ARBITRAGE PROBABILITY MONITOR - STARTING")
        print("="*100)
        print(f"Server: {BASE_URL}")
        print("Monitoring 5 M&A Deals: D1-D5")
        print("="*100 + "\n")
        
        tick = 0
        
        while tick < 600 and not shutdown:
            try:
                tick = get_tick(session)
                
                if tick == 0:
                    print("Waiting for case to start...")
                    sleep(1)
                    continue
                
                # Get market data
                securities = get_securities(session)
                
                # Build price dictionary
                market_data = {}
                for sec in securities:
                    ticker = sec.get('ticker')
                    last = sec.get('last', 0.0)
                    if ticker and last > 0:
                        market_data[ticker] = float(last)
                
                # Calculate probabilities
                probabilities = calculator.calculate_all_probabilities(market_data)
                
                # Display results
                if probabilities:
                    display_probabilities(tick, probabilities)
                
                sleep(SLEEP_SEC)
                
            except ApiException as e:
                print(f"API error: {e}")
                sleep(1)
            except KeyboardInterrupt:
                print("\nUser interrupted. Exiting...")
                break
            except Exception as e:
                print(f"Unexpected error: {e}")
                import traceback
                traceback.print_exc()
                sleep(1)
        
        print("\n" + "="*100)
        print("MONITORING FINISHED")
        print("="*100)


if __name__ == '__main__':
    def _sig_handler(signum, frame):
        global shutdown
        shutdown = True
        print("\nShutdown signal received...")
    
    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)
    
    main()