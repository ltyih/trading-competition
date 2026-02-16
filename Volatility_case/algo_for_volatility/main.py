"""
Volatility Trading Algorithm V9 - Liam's Optimal Straddle Method
RITC 2026 Volatility Case

Strategy:
  - Trade delta-hedged straddles with mathematically optimal sizing
  - Reposition at each week boundary (ticks 2/75/150/225)
  - Position size n* maximizes: vol_profit - positioning_cost - rebalancing_cost
  - Delta hedge only when approaching the delta limit
"""

import sys
import time
import logging
from datetime import datetime

from config import LOOP_INTERVAL_SEC, TICKS_PER_SUBHEAT
from rit_api import RITApi
from trading_engine import StraddleEngine, Phase

BANNER = r"""
================================================================
  RITC 2026 - Volatility Algorithm V9
  Liam's Optimal Straddle Method
  Strategy: Eq5 Optimization + MFPT Delta Hedging
================================================================
"""


def setup_logging():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = f"vol_algo_{timestamp}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_file


def wait_for_connection(api: RITApi):
    print("Waiting for RIT connection...")
    while True:
        if api.is_connected():
            print("Connected to RIT.")
            return
        time.sleep(1)


def wait_for_active(api: RITApi):
    status = api.get_status()
    if status in ("ACTIVE", "RUNNING"):
        return True
    print(f"Case status: {status} - waiting...")
    while True:
        status = api.get_status()
        if status in ("ACTIVE", "RUNNING"):
            print("Case is ACTIVE.")
            return True
        if status == "STOPPED":
            return False
        time.sleep(0.5)


def print_cycle(r: dict):
    """Print compact one-line status."""
    tick = r["tick"]
    phase = r["phase"]
    vol = r.get("vol")
    iv = r.get("market_iv")
    edge = r.get("edge", 0)
    delta = r.get("delta", 0)
    dlim = r.get("delta_limit")
    spot = r.get("spot", 0)
    n = r.get("n_straddles", 0)
    direction = r.get("direction", 0)
    strike = r.get("strike")
    gross = r.get("gross", 0)
    net = r.get("net", 0)
    hedges = r.get("hedge_trades", 0)
    opt_trades = r.get("option_trades", 0)
    expected = r.get("expected_pnl", 0)

    vol_str = f"{vol:.1f}%" if vol else "N/A"
    iv_str = f"{iv:.1f}%" if iv else "N/A"
    dir_str = {1: "LONG", -1: "SHORT", 0: "FLAT"}.get(direction, "?")
    strike_str = f"K={strike:.0f}" if strike else "K=-"

    delta_pct = ""
    if dlim and dlim > 0:
        pct = abs(delta) / dlim * 100
        delta_pct = f"({pct:.0f}%)"

    parts = [
        f"T={tick:>3}/{TICKS_PER_SUBHEAT}",
        f"[{phase:<11s}]",
        f"S={spot:.2f}",
        f"Vol={vol_str}",
        f"IV={iv_str}",
        f"Edge={edge:>+.1f}%",
        f"{dir_str} n={n}",
        f"{strike_str}",
        f"D={delta:>+8.0f}{delta_pct}",
    ]

    if opt_trades > 0:
        parts.append(f"Trd={opt_trades}")
    if hedges > 0:
        parts.append(f"Hdg={hedges}")
    if expected > 0 and phase == Phase.HOLDING:
        parts.append(f"E[$]={expected:.0f}")

    print(" | ".join(parts))


def run_trading_loop(api: RITApi, engine: StraddleEngine):
    last_tick = -1
    cycle_count = 0

    while True:
        case = api.get_case()
        if not case:
            time.sleep(0.5)
            continue

        status = case.get("status", "")
        if status not in ("ACTIVE", "RUNNING"):
            if cycle_count > 0:
                print(f"\nCase status: {status}. Sub-heat complete.")
            return status

        tick = case.get("tick", 0)
        if tick == last_tick:
            time.sleep(LOOP_INTERVAL_SEC / 2)
            continue

        last_tick = tick
        cycle_count += 1

        result = engine.execute_cycle(tick)
        print_cycle(result)

        # Detailed summary at key ticks
        if tick % 75 == 1 or tick == 1:
            state = engine.get_market_state(tick)
            if state:
                engine.print_position_summary(state)
                nlv = api.get_nlv()
                if nlv:
                    print(f"  NLV: ${nlv:,.2f}")
                print(f"  RTM Volume: {engine.rtm_volume}")
                print()

        time.sleep(LOOP_INTERVAL_SEC)


def main():
    print(BANNER)
    log_file = setup_logging()
    logger = logging.getLogger(__name__)
    print(f"Logging to: {log_file}\n")

    api = RITApi()
    engine = StraddleEngine(api)

    wait_for_connection(api)

    trader = api.get_trader()
    if trader:
        print(f"Trader: {trader.get('trader_id', '?')} | "
              f"NLV: ${trader.get('nlv', 0):,.2f}")

    print("\nV9 Optimal Straddle engine starting (Ctrl+C to stop)...\n")
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
            engine.reset()

            start_nlv = api.get_nlv()
            case = api.get_case()
            period = case.get("period", "?") if case else "?"

            print(f"\n{'='*60}")
            print(f"  SESSION {session_num} | PERIOD {period} | Start NLV: ${start_nlv:,.2f}")
            print(f"  Strategy: Liam's Eq5 Optimal Straddles")
            print(f"{'='*60}\n")

            run_trading_loop(api, engine)

            end_nlv = api.get_nlv()
            pnl = end_nlv - start_nlv
            session_results.append({
                "session": session_num,
                "period": period,
                "start_nlv": start_nlv,
                "end_nlv": end_nlv,
                "pnl": pnl,
                "rtm_volume": engine.rtm_volume,
            })

            print(f"\n  Session {session_num} ended.")
            print(f"  Start: ${start_nlv:,.2f} -> End: ${end_nlv:,.2f}")
            print(f"  P&L: ${pnl:>+,.2f}")
            print(f"  RTM Volume: {engine.rtm_volume}")
            print(f"\n  --- SESSION HISTORY ---")
            for s in session_results:
                marker = " <--" if s["session"] == session_num else ""
                print(f"  S{s['session']:>2} (P{s['period']}): "
                      f"${s['start_nlv']:>10,.2f} -> ${s['end_nlv']:>10,.2f} "
                      f"| P&L: ${s['pnl']:>+10,.2f} "
                      f"| RTM: {s.get('rtm_volume', 0):>7}{marker}")
            total_pnl = sum(s["pnl"] for s in session_results)
            print(f"  {'':->65}")
            print(f"  Total P&L: ${total_pnl:>+,.2f}")
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
            total_pnl = sum(s["pnl"] for s in session_results)
            print(f"Total P&L: ${total_pnl:>+,.2f}")
        print("Goodbye.")


if __name__ == "__main__":
    main()
