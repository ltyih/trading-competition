# -*- coding: utf-8 -*-
"""
PASSIVE MONITOR - Observes the RIT API tick-by-tick without trading.
Skips the current session, waits for a new heat, then logs everything.
Run this ALONGSIDE main.py to capture what the algo sees and does.
"""

import sys
import os
import time
import csv
import json
from datetime import datetime
from pathlib import Path

os.environ['PYTHONUNBUFFERED'] = '1'
_orig_print = print
def print(*args, **kwargs):
    kwargs.setdefault('flush', True)
    _orig_print(*args, **kwargs)

from config import API_BASE_URL, API_KEY, MAX_TICK
from api import RITApi


def main():
    api = RITApi()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = Path(__file__).resolve().parent / "monitor_logs"
    log_dir.mkdir(exist_ok=True)

    # --- Phase 1: Skip current session ---
    print("=" * 70)
    print("  PASSIVE MONITOR - Will skip current session, wait for next heat")
    print(f"  API: {API_BASE_URL} | Key: {API_KEY}")
    print("=" * 70)

    print("\nConnecting to RIT...")
    while True:
        if api.is_connected():
            print("Connected.")
            break
        time.sleep(1)

    # Check if a session is currently running
    case = api.get_case()
    if case and case.get("status") in ("ACTIVE", "RUNNING"):
        current_tick = case.get("tick", 0)
        current_period = case.get("period", "?")
        print(f"\nSession currently ACTIVE at tick {current_tick}, period {current_period}")
        print("Skipping this session... waiting for it to end.")

        # Wait for this session to end
        while True:
            case = api.get_case()
            if not case:
                break
            status = case.get("status", "")
            if status not in ("ACTIVE", "RUNNING"):
                print(f"Session ended (status={status})")
                break
            tick = case.get("tick", 0)
            if tick % 50 == 0:
                print(f"  ... still running, tick={tick}")
            time.sleep(1)

        print("Waiting for next heat to start...")
        time.sleep(2)
    else:
        print("No active session. Waiting for next heat to start...")

    # --- Phase 2: Wait for new heat ---
    while True:
        case = api.get_case()
        if case and case.get("status") in ("ACTIVE", "RUNNING"):
            tick = case.get("tick", 0)
            if tick <= 10:  # Make sure it's a fresh heat
                print(f"\nNEW HEAT DETECTED at tick {tick}!")
                break
        time.sleep(0.5)

    # --- Phase 3: Monitor the heat ---
    case = api.get_case()
    period = case.get("period", "?") if case else "?"
    securities = api.get_securities() or []
    tickers = sorted(set(
        str(s.get('ticker', '')) for s in securities
        if str(s.get('ticker', '')) and str(s.get('ticker', '')) != 'USD'
    ))

    print(f"\n{'='*70}")
    print(f"  MONITORING Heat period={period}")
    print(f"  Tickers: {', '.join(tickers)}")
    print(f"{'='*70}\n")

    # Open CSV log
    csv_path = log_dir / f"monitor_{ts}_p{period}.csv"
    csv_f = csv_path.open("w", newline="")
    writer = csv.writer(csv_f)

    # Header
    base_cols = ["timestamp", "tick", "period", "pnl", "nlv"]
    for t in tickers:
        base_cols.extend([
            f"{t}_pos", f"{t}_bid", f"{t}_ask", f"{t}_spread",
            f"{t}_bid_depth", f"{t}_ask_depth", f"{t}_last",
            f"{t}_unrealized", f"{t}_realized",
        ])
    base_cols.extend([
        "tender_count", "tender_details",
        "net_pos", "gross_pos",
        "open_orders_count",
    ])
    writer.writerow(base_cols)

    # Detailed event log
    event_path = log_dir / f"events_{ts}_p{period}.log"
    event_f = event_path.open("w", encoding="utf-8")

    def log_event(msg):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{timestamp}] {msg}"
        event_f.write(line + "\n")
        event_f.flush()
        print(f"  {line}")

    log_event(f"Monitor started for period {period}, tickers={tickers}")

    last_tick = -1
    seen_tenders = set()
    last_positions = {t: 0 for t in tickers}
    tender_history = []  # track all tenders
    position_history = []  # track position changes

    try:
        while True:
            loop_start = time.time()

            case = api.get_case()
            if not case:
                time.sleep(0.5)
                continue

            status = case.get("status", "")
            if status not in ("ACTIVE", "RUNNING"):
                log_event(f"Heat ended with status={status}")
                break

            tick = case.get("tick", 0)
            if tick == last_tick:
                time.sleep(0.04)
                continue
            last_tick = tick

            if tick >= MAX_TICK:
                log_event(f"Reached MAX_TICK={MAX_TICK}")
                break

            # Gather all data
            securities = api.get_securities() or []
            trader = api.get_trader()
            nlv = trader.get("nlv", 0.0) if trader else 0.0
            tenders = api.get_tenders()

            # Parse securities
            sec_data = {}
            total_pnl = 0.0
            net_pos = 0
            gross_pos = 0
            for sec in securities:
                t = str(sec.get('ticker', ''))
                if not t or t == 'USD':
                    continue
                pos = int(sec.get('position', 0))
                bid = float(sec.get('bid', 0))
                ask = float(sec.get('ask', 0))
                last_px = float(sec.get('last', 0))
                bid_size = int(sec.get('bid_size', 0))
                ask_size = int(sec.get('ask_size', 0))
                unrealized = float(sec.get('unrealized', 0))
                realized = float(sec.get('realized', 0))
                spread = round(ask - bid, 4) if bid > 0 and ask > 0 else 0

                sec_data[t] = {
                    'pos': pos, 'bid': bid, 'ask': ask, 'spread': spread,
                    'bid_size': bid_size, 'ask_size': ask_size,
                    'last': last_px, 'unrealized': unrealized, 'realized': realized,
                }
                total_pnl += unrealized + realized
                net_pos += pos
                gross_pos += abs(pos)

            # Detect position changes
            for t in tickers:
                cur_pos = sec_data.get(t, {}).get('pos', 0)
                prev_pos = last_positions.get(t, 0)
                if cur_pos != prev_pos:
                    delta = cur_pos - prev_pos
                    log_event(f"POSITION CHANGE {t}: {prev_pos:+,} -> {cur_pos:+,} "
                              f"(delta={delta:+,})")
                    position_history.append({
                        'tick': tick, 'ticker': t,
                        'prev': prev_pos, 'new': cur_pos, 'delta': delta,
                    })
                last_positions[t] = cur_pos

            # Parse tenders
            tender_details = []
            for tender in tenders:
                t = str(tender.get('ticker', ''))
                tid = int(tender.get('tender_id', -1))
                action = str(tender.get('action', '')).upper()
                qty = int(tender.get('quantity', 0))
                price = tender.get('price')
                price_str = f"${float(price):.2f}" if price is not None else "AUCTION"
                expires = int(tender.get('expires', 0))
                is_fixed = bool(tender.get('is_fixed_bid', True))
                tender_type = "PRIVATE" if is_fixed else "AUCTION"

                tender_details.append(
                    f"{tender_type}#{tid}:{t} {action} {qty:,}@{price_str} exp={expires}"
                )

                if tid not in seen_tenders:
                    seen_tenders.add(tid)
                    log_event(f"NEW TENDER: {tender_type} #{tid} | {t} {action} "
                              f"{qty:,} @ {price_str} | expires={expires}")

                    # Analyze what the algo SHOULD do
                    sd = sec_data.get(t, {})
                    if sd:
                        mid = round((sd['bid'] + sd['ask']) / 2, 2) if sd['bid'] > 0 and sd['ask'] > 0 else 0
                        close_side = 'SELL' if action == 'BUY' else 'BUY'
                        close_depth = sd['bid_size'] if close_side == 'SELL' else sd['ask_size']
                        if price is not None and mid > 0:
                            fprice = float(price)
                            if action == 'BUY':
                                edge = mid - fprice  # We buy at tender, sell at market
                            else:
                                edge = fprice - mid  # We sell at tender, buy at market
                            log_event(f"  TENDER ANALYSIS: mid={mid:.2f}, edge={edge:.4f}/sh, "
                                      f"close_depth={close_depth:,}, spread={sd['spread']:.4f}")

                    tender_history.append({
                        'tick': tick, 'id': tid, 'type': tender_type,
                        'ticker': t, 'action': action, 'qty': qty,
                        'price': float(price) if price is not None else None,
                        'expires': expires,
                    })

            # Check for open orders (to see how algo is executing)
            open_orders = api.get_orders("OPEN") or []
            open_count = len(open_orders)
            if open_count > 0 and tick % 5 == 0:
                for o in open_orders[:5]:
                    log_event(f"  OPEN ORDER: {o.get('action')} {o.get('quantity')} "
                              f"{o.get('ticker')} @ {o.get('price')} ({o.get('type')})")

            # Write CSV row
            row = [datetime.now().isoformat(timespec="milliseconds"),
                   tick, period, round(total_pnl, 2), round(nlv, 2)]
            for t in tickers:
                sd = sec_data.get(t, {})
                row.extend([
                    sd.get('pos', 0), sd.get('bid', 0), sd.get('ask', 0),
                    sd.get('spread', 0), sd.get('bid_size', 0), sd.get('ask_size', 0),
                    sd.get('last', 0), sd.get('unrealized', 0), sd.get('realized', 0),
                ])
            row.extend([
                len(tender_details),
                "; ".join(tender_details) if tender_details else "",
                net_pos, gross_pos, open_count,
            ])
            writer.writerow(row)
            csv_f.flush()

            # Periodic summary
            if tick % 20 == 0 or tick <= 5:
                pos_str = ", ".join(f"{t}:{sec_data.get(t,{}).get('pos',0):+,}"
                                    for t in tickers
                                    if sec_data.get(t,{}).get('pos',0) != 0) or "flat"
                log_event(f"TICK {tick:>3}/{MAX_TICK} | PnL=${total_pnl:>10,.2f} | "
                          f"NLV=${nlv:>10,.2f} | Pos: {pos_str} | "
                          f"N/G: {net_pos}/{gross_pos} | "
                          f"Tenders active: {len(tender_details)} | "
                          f"Open orders: {open_count}")

            elapsed = time.time() - loop_start
            time.sleep(max(0.04, 0.08 - elapsed))

    except KeyboardInterrupt:
        log_event("Monitor interrupted by user")
    finally:
        csv_f.close()
        event_f.close()

    # --- Phase 4: Summary ---
    print(f"\n{'='*70}")
    print(f"  MONITOR SUMMARY")
    print(f"{'='*70}")
    print(f"  Ticks observed: {last_tick}")
    print(f"  Total tenders seen: {len(tender_history)}")
    print(f"  Position changes: {len(position_history)}")
    print(f"  Final PnL: ${total_pnl:,.2f}")
    print(f"  Final NLV: ${nlv:,.2f}")

    if tender_history:
        print(f"\n  --- TENDER LOG ---")
        accepted_count = 0
        for th in tender_history:
            # Check if position changed for this ticker around this tick
            pos_changes = [p for p in position_history
                           if p['ticker'] == th['ticker']
                           and th['tick'] <= p['tick'] <= th['tick'] + 5]
            was_accepted = len(pos_changes) > 0 and any(
                abs(p['delta']) >= th['qty'] * 0.5 for p in pos_changes
            )
            status = "ACCEPTED" if was_accepted else "REJECTED/MISSED"
            if was_accepted:
                accepted_count += 1
            price_str = f"${th['price']:.2f}" if th['price'] else "AUCTION"
            print(f"  T={th['tick']:>3} | {th['type']:>7} #{th['id']} | "
                  f"{th['ticker']} {th['action']} {th['qty']:>6,} @ {price_str:>8} | "
                  f"{status}")
        print(f"\n  Accepted: {accepted_count}/{len(tender_history)}")

    if position_history:
        print(f"\n  --- POSITION CHANGES ---")
        for p in position_history:
            print(f"  T={p['tick']:>3} | {p['ticker']}: {p['prev']:>+8,} -> "
                  f"{p['new']:>+8,} (delta={p['delta']:>+8,})")

    print(f"\n  Logs saved to:")
    print(f"    CSV: {csv_path}")
    print(f"    Events: {event_path}")
    print(f"{'='*70}")

    # Save summary JSON
    summary_path = log_dir / f"summary_{ts}_p{period}.json"
    summary = {
        "period": period,
        "tickers": tickers,
        "last_tick": last_tick,
        "final_pnl": total_pnl,
        "final_nlv": nlv,
        "tenders": tender_history,
        "position_changes": position_history,
    }
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"  Summary JSON: {summary_path}")


if __name__ == "__main__":
    main()
