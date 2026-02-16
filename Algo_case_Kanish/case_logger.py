# -*- coding: utf-8 -*-
"""
ALGO MARKET MAKING - COMPREHENSIVE DATA LOGGER
================================================
Run this alongside (or instead of) the trading bot to capture:
- Price data every tick (bid, ask, mid, spread) for all 4 stocks
- Position data every tick
- Order book depth (top 5 levels)
- Limit utilization
- Time & sales (recent trades)
- News events
- P&L tracking

Output: CSV files in logs/ directory for post-heat analysis.
Use this data to tune the V12 algorithm's parameters.

Usage:
    python logger.py              # Log during active heat
    python logger.py --passive    # Just log, don't interfere with trading
"""

import sys
import os
import csv
import time
import json
import argparse
from datetime import datetime
from typing import Dict, List, Optional

# Reuse our API config
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import TICKERS, API_BASE_URL, API_KEY, DAY_LENGTH, REBATES
from api import RITApi


class DataLogger:
    def __init__(self, output_dir: str = "logs"):
        self.api = RITApi()
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.prefix = ts

        # CSV writers
        self.price_file = None
        self.price_writer = None
        self.position_file = None
        self.position_writer = None
        self.book_file = None
        self.book_writer = None
        self.trade_file = None
        self.trade_writer = None
        self.limit_file = None
        self.limit_writer = None
        self.summary_file = None
        self.summary_writer = None

        # Track last known state for change detection
        self.last_positions: Dict[str, int] = {t: 0 for t in TICKERS}
        self.last_trade_ids: Dict[str, set] = {t: set() for t in TICKERS}

    def setup_files(self):
        """Create CSV files with headers."""
        # Prices (every tick)
        self.price_file = open(
            os.path.join(self.output_dir, f"{self.prefix}_prices.csv"), "w", newline="")
        self.price_writer = csv.writer(self.price_file)
        self.price_writer.writerow([
            "tick", "second_in_day", "day",
            "ticker", "bid", "ask", "mid", "spread",
            "last_price", "volume"
        ])

        # Positions (every tick)
        self.position_file = open(
            os.path.join(self.output_dir, f"{self.prefix}_positions.csv"), "w", newline="")
        self.position_writer = csv.writer(self.position_file)
        self.position_writer.writerow([
            "tick", "second_in_day", "day",
            "ticker", "position", "position_change",
            "unrealized_pnl", "realized_pnl"
        ])

        # Order book depth (sampled)
        self.book_file = open(
            os.path.join(self.output_dir, f"{self.prefix}_book_depth.csv"), "w", newline="")
        self.book_writer = csv.writer(self.book_file)
        self.book_writer.writerow([
            "tick", "second_in_day", "day", "ticker",
            "bid_1_price", "bid_1_size", "bid_2_price", "bid_2_size",
            "bid_3_price", "bid_3_size", "bid_4_price", "bid_4_size",
            "bid_5_price", "bid_5_size",
            "ask_1_price", "ask_1_size", "ask_2_price", "ask_2_size",
            "ask_3_price", "ask_3_size", "ask_4_price", "ask_4_size",
            "ask_5_price", "ask_5_size",
            "bid_total_size", "ask_total_size", "imbalance"
        ])

        # Limits
        self.limit_file = open(
            os.path.join(self.output_dir, f"{self.prefix}_limits.csv"), "w", newline="")
        self.limit_writer = csv.writer(self.limit_file)
        self.limit_writer.writerow([
            "tick", "second_in_day", "day",
            "aggregate_position", "gross_limit", "net_limit",
            "utilization_pct", "net_position",
            "nlv", "pnl_estimate"
        ])

        # Summary (per-day rollup)
        self.summary_file = open(
            os.path.join(self.output_dir, f"{self.prefix}_summary.csv"), "w", newline="")
        self.summary_writer = csv.writer(self.summary_file)
        self.summary_writer.writerow([
            "day", "end_tick",
            "WNTR_end_pos", "SMMR_end_pos", "ATMN_end_pos", "SPNG_end_pos",
            "aggregate_at_close", "aggregate_limit",
            "over_limit_shares", "estimated_penalty",
            "nlv", "pnl_from_start"
        ])

        print(f"  Log files created in {self.output_dir}/ with prefix {self.prefix}")

    def close_files(self):
        for f in [self.price_file, self.position_file, self.book_file,
                  self.limit_file, self.summary_file]:
            if f:
                f.close()

    def log_tick(self, tick: int, case_data: dict):
        """Log all data for a single tick."""
        second_in_day = tick % DAY_LENGTH
        day = tick // DAY_LENGTH + 1

        # Fetch securities data
        securities = self.api.get_securities()
        if not securities:
            return

        # Log prices and positions for each ticker
        positions = {}
        for sec in securities:
            t = sec.get("ticker", "")
            if t not in TICKERS:
                continue

            bid = sec.get("bid", 0) or 0
            ask = sec.get("ask", 0) or 0
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0
            spread = ask - bid if bid > 0 and ask > 0 else 0
            last_price = sec.get("last", 0) or 0
            volume = sec.get("volume", 0) or 0
            position = sec.get("position", 0) or 0
            unrealized = sec.get("unrealized", 0) or 0
            realized = sec.get("realized", 0) or 0

            positions[t] = position
            pos_change = position - self.last_positions.get(t, 0)

            # Price data
            self.price_writer.writerow([
                tick, second_in_day, day,
                t, f"{bid:.2f}", f"{ask:.2f}", f"{mid:.4f}", f"{spread:.4f}",
                f"{last_price:.2f}", volume
            ])

            # Position data
            self.position_writer.writerow([
                tick, second_in_day, day,
                t, position, pos_change,
                f"{unrealized:.2f}", f"{realized:.2f}"
            ])

        self.last_positions = positions

        # Log book depth (every 3 ticks to reduce API load)
        if tick % 3 == 0:
            for t in TICKERS:
                book = self.api.get_book(t, limit=5)
                bids = book.get("bids", [])
                asks = book.get("asks", [])

                row = [tick, second_in_day, day, t]
                bid_total = 0
                for i in range(5):
                    if i < len(bids):
                        p = bids[i].get("price", 0)
                        s = bids[i].get("quantity", 0) - bids[i].get("quantity_filled", 0)
                        bid_total += s
                        row.extend([f"{p:.2f}", s])
                    else:
                        row.extend(["", ""])

                ask_total = 0
                for i in range(5):
                    if i < len(asks):
                        p = asks[i].get("price", 0)
                        s = asks[i].get("quantity", 0) - asks[i].get("quantity_filled", 0)
                        ask_total += s
                        row.extend([f"{p:.2f}", s])
                    else:
                        row.extend(["", ""])

                total = bid_total + ask_total
                imbalance = (bid_total - ask_total) / total if total > 0 else 0
                row.extend([bid_total, ask_total, f"{imbalance:.4f}"])

                self.book_writer.writerow(row)

        # Log limits (every 5 ticks)
        if tick % 5 == 0:
            limits = self.api.get_limits()
            gross_limit = 50000
            net_limit = 30000
            if limits:
                for lim in limits:
                    gl = lim.get("gross_limit", 0)
                    nl = lim.get("net_limit", 0)
                    if gl and gl > 0:
                        gross_limit = int(gl)
                    if nl and nl > 0:
                        net_limit = int(nl)

            aggregate = sum(abs(positions.get(t, 0)) for t in TICKERS)
            net = abs(sum(positions.get(t, 0) for t in TICKERS))
            util = aggregate / gross_limit if gross_limit > 0 else 0

            trader = self.api.get_trader()
            nlv = trader.get("nlv", 0) if trader else 0

            self.limit_writer.writerow([
                tick, second_in_day, day,
                aggregate, gross_limit, net_limit,
                f"{util:.4f}", net,
                f"{nlv:.2f}", ""
            ])

        # Log summary at market close (second 59)
        if second_in_day == 59:
            aggregate = sum(abs(positions.get(t, 0)) for t in TICKERS)
            limits = self.api.get_limits()
            agg_limit = 50000
            if limits:
                for lim in limits:
                    gl = lim.get("gross_limit", 0)
                    if gl and gl > 0:
                        agg_limit = int(gl)

            over = max(0, aggregate - agg_limit)
            penalty = over * 10.0

            trader = self.api.get_trader()
            nlv = trader.get("nlv", 0) if trader else 0

            self.summary_writer.writerow([
                day, tick,
                positions.get("WNTR", 0), positions.get("SMMR", 0),
                positions.get("ATMN", 0), positions.get("SPNG", 0),
                aggregate, agg_limit,
                over, f"{penalty:.2f}",
                f"{nlv:.2f}", ""
            ])

            print(f"  [DAY {day} CLOSE] agg={aggregate}/{agg_limit} "
                  f"over={over} penalty=${penalty:,.0f} "
                  f"NLV=${nlv:,.2f}")

        # Flush every 10 ticks
        if tick % 10 == 0:
            for f in [self.price_file, self.position_file, self.book_file,
                      self.limit_file, self.summary_file]:
                if f:
                    f.flush()

    def run(self):
        """Main logging loop."""
        print("\n" + "=" * 60)
        print("  ALGO MARKET MAKING - DATA LOGGER")
        print("  Capturing: prices, positions, book depth, limits")
        print(f"  Tickers: {', '.join(TICKERS)}")
        print(f"  API: {API_BASE_URL}")
        print("=" * 60 + "\n")

        # Wait for connection
        print("  Waiting for RIT connection...")
        while True:
            if self.api.get_case():
                print("  Connected!")
                break
            time.sleep(1.0)

        # Wait for active
        print("  Waiting for ACTIVE status...")
        while True:
            case = self.api.get_case()
            if case and case.get("status") == "ACTIVE":
                break
            if case and case.get("status") == "STOPPED":
                print("  Simulation already STOPPED.")
                return
            time.sleep(0.5)

        print("  Simulation is ACTIVE! Starting data capture...")
        self.setup_files()

        last_tick = -1
        start_time = time.time()

        try:
            while True:
                case = self.api.get_case()
                if not case:
                    time.sleep(0.3)
                    continue

                status = case.get("status", "")
                if status != "ACTIVE":
                    if status == "STOPPED":
                        print("\n  [SIMULATION ENDED]")
                        elapsed = time.time() - start_time
                        print(f"  Logged {last_tick + 1} ticks in {elapsed:.1f}s")
                        break
                    time.sleep(0.3)
                    continue

                tick = case.get("tick", 0)
                if tick == last_tick:
                    time.sleep(0.05)
                    continue

                last_tick = tick
                self.log_tick(tick, case)

                # Print progress every 30 ticks
                if tick % 30 == 0:
                    elapsed = time.time() - start_time
                    print(f"  Tick {tick}/300 ({elapsed:.1f}s elapsed)")

                time.sleep(0.08)  # ~12 samples per second

        except KeyboardInterrupt:
            print("\n  [INTERRUPTED]")
        finally:
            self.close_files()
            print(f"\n  Data saved to {self.output_dir}/")
            print(f"  Files: {self.prefix}_prices.csv, "
                  f"{self.prefix}_positions.csv, "
                  f"{self.prefix}_book_depth.csv, "
                  f"{self.prefix}_limits.csv, "
                  f"{self.prefix}_summary.csv")


def main():
    parser = argparse.ArgumentParser(description="RITC Algo Market Making Data Logger")
    parser.add_argument("--output-dir", default="logs", help="Output directory for CSV files")
    args = parser.parse_args()

    logger = DataLogger(output_dir=args.output_dir)

    while True:
        try:
            logger.run()
        except Exception as e:
            print(f"  Error: {e}")

        print("\n  Waiting for next simulation...")
        time.sleep(3)

        # Create new logger for next heat
        logger = DataLogger(output_dir=args.output_dir)


if __name__ == "__main__":
    main()