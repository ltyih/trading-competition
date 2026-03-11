"""
Volatility Trading Algorithm V4 - Maximum Profit
*REMOVED* 2026 Volatility Case

Strategy: Target portfolio approach with aggressive position building,
vega-weighted ATM straddles, gamma-aware delta hedging.
"""

import sys
import time
import logging
from datetime import datetime

from config import LOOP_INTERVAL_SEC, UNWIND_START_TICK, TICKS_PER_SUBHEAT
from rit_api import RITApi
from trading_engine import TradingEngine

BANNER = r"""
============================================================
  *REMOVED* 2026 - Volatility Trading Algorithm V4
  Strategy: Max Profit - Target Portfolio + Gamma Scalping
============================================================
"""


def setup_logging():
    """Configure logging to file and console."""
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
    """Wait until RIT client is reachable."""
    print("Waiting for RIT connection...")
    while True:
        if api.is_connected():
            print("Connected to RIT.")
            return
        time.sleep(1)


def wait_for_active(api: RITApi):
    """Wait until the case is in ACTIVE status."""
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


def print_cycle_status(result: dict):
    """Print a compact V4 status line."""
    tick = result["tick"]
    vol = result.get("vol")
    mkt_iv = result.get("market_iv")
    direction = result.get("direction", 0)
    edge = result.get("edge", 0)
    delta = result.get("delta", 0)
    delta_limit = result.get("delta_limit")
    spot = result.get("spot", 0)
    gross = result.get("gross", 0)
    net = result.get("net", 0)
    opt_trades = result.get("option_trades", 0)
    hedges = result.get("hedge_trades", 0)
    unwinds = result.get("unwind_trades", 0)
    reversal = result.get("reversal", False)

    vol_str = f"{vol:.1f}%" if vol else "N/A"
    iv_str = f"{mkt_iv:.1f}%" if mkt_iv else "N/A"
    dir_str = {1: "LONG", -1: "SHORT", 0: "FLAT"}.get(direction, "?")
    dl_str = f"{delta_limit:.0f}" if delta_limit else "N/A"

    delta_pct = ""
    if delta_limit and delta_limit > 0:
        pct = abs(delta) / delta_limit * 100
        delta_pct = f"({pct:.0f}%)"

    phase = "UNWIND" if tick >= UNWIND_START_TICK else "TRADE"

    parts = [
        f"T={tick:>3}/{TICKS_PER_SUBHEAT}",
        f"[{phase}]",
        f"S={spot:.2f}",
        f"Vol={vol_str}",
        f"IV={iv_str}",
        f"Edge={edge:.1f}%",
        f"Dir={dir_str}",
        f"D={delta:>+8.0f}{delta_pct}",
        f"G={gross}/N={net}",
    ]

    built = result.get("built", False)
    if built:
        parts.append("[BUILT]")
    if opt_trades > 0:
        parts.append(f"Trd={opt_trades}")
    if hedges > 0:
        parts.append(f"Hdg={hedges}")
    if unwinds > 0:
        parts.append(f"Unw={unwinds}")
    if reversal:
        parts.append("**REVERSAL**")

    print(" | ".join(parts))


def run_trading_loop(api: RITApi, engine: TradingEngine):
    """Main trading loop for one sub-heat."""
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
                print(f"\nCase status changed to {status}. Sub-heat complete.")
            return status

        tick = case.get("tick", 0)

        if tick == last_tick:
            time.sleep(LOOP_INTERVAL_SEC / 2)
            continue

        last_tick = tick
        cycle_count += 1

        result = engine.execute_cycle(tick)
        print_cycle_status(result)

        # Detailed table every 50 ticks
        if tick % 50 == 0 or tick == 1:
            state = engine.get_portfolio_state(tick)
            if state:
                engine.print_options_table(state)
                print(f"\n  Portfolio: RTM={int(state.underlying_position):>+6d} | "
                      f"Delta={state.total_delta:>+8.1f} | "
                      f"Gamma={state.total_gamma:>+8.1f} | "
                      f"Vega={state.total_vega:>+8.2f} | "
                      f"OptGross={state.options_gross} OptNet={state.options_net}")
                nlv = api.get_nlv()
                if nlv:
                    print(f"  NLV: ${nlv:,.2f}")
                print()

        time.sleep(LOOP_INTERVAL_SEC)


def main():
    print(BANNER)
    log_file = setup_logging()
    logger = logging.getLogger(__name__)
    print(f"Logging to: {log_file}\n")

    api = RITApi()
    engine = TradingEngine(api)

    wait_for_connection(api)

    trader = api.get_trader()
    if trader:
        print(f"Trader: {trader.get('trader_id', '?')} | "
              f"NLV: ${trader.get('nlv', 0):,.2f}")

    print("\nV4 MAX PROFIT engine starting (Ctrl+C to stop)...\n")
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

            # Reset engine state for new sub-heat
            engine.vol_state = __import__("news_parser").VolatilityState()
            engine.last_tick = 0
            engine.last_direction = 0
            engine.direction_changes = 0
            engine.last_reversal_tick = -100
            engine.position_built = False

            start_nlv = api.get_nlv()

            case = api.get_case()
            period = case.get("period", "?") if case else "?"
            print(f"\n{'='*60}")
            print(f"  SESSION {session_num} | SUB-HEAT {period} | Start NLV: ${start_nlv:,.2f}")
            print(f"{'='*60}\n")

            final_status = run_trading_loop(api, engine)

            end_nlv = api.get_nlv()
            pnl = end_nlv - start_nlv
            session_results.append({
                "session": session_num,
                "period": period,
                "start_nlv": start_nlv,
                "end_nlv": end_nlv,
                "pnl": pnl,
            })

            print(f"\n  Session {session_num} ended.")
            print(f"  Start NLV: ${start_nlv:,.2f} -> End NLV: ${end_nlv:,.2f}")
            print(f"  P&L: ${pnl:>+,.2f}")
            print(f"\n  --- SESSION HISTORY ---")
            for s in session_results:
                marker = " <--" if s["session"] == session_num else ""
                print(f"  S{s['session']:>2} (P{s['period']}): "
                      f"${s['start_nlv']:>10,.2f} -> ${s['end_nlv']:>10,.2f} "
                      f"| P&L: ${s['pnl']:>+10,.2f}{marker}")
            total_pnl = sum(s["pnl"] for s in session_results)
            print(f"  {'':->55}")
            print(f"  Total P&L across {len(session_results)} sessions: ${total_pnl:>+,.2f}")
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
            print(f"Total P&L across {len(session_results)} sessions: ${total_pnl:>+,.2f}")
        print("Goodbye.")


if __name__ == "__main__":
    main()
