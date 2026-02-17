# -*- coding: utf-8 -*-
"""
COMPREHENSIVE MONITOR v2
========================
Real-time monitoring of the Liquidity Bot. Run ALONGSIDE main.py.
Captures EVERYTHING: positions, P&L, full order book, all orders (open/filled/
cancelled), tenders, execution quality, slippage, API latency, and more.

Monitors the CURRENT session immediately (no skip).

Storage:
  monitor_logs/
    tick_data_{ts}.csv       - Per-tick market data + positions
    orders_{ts}.csv          - Every order seen (open, filled, cancelled)
    tenders_{ts}.csv         - Every tender with analysis
    book_snapshots_{ts}.csv  - Full order book depth each tick
    execution_{ts}.csv       - Fill-level slippage analysis
    api_health_{ts}.csv      - API latency per call
    events_{ts}.log          - Human-readable event stream
    summary_{ts}.json        - End-of-session summary
"""

import sys
import os
import time
import csv
import json
from datetime import datetime
from pathlib import Path
from collections import defaultdict

os.environ['PYTHONUNBUFFERED'] = '1'
_orig_print = print
def print(*args, **kwargs):
    kwargs.setdefault('flush', True)
    _orig_print(*args, **kwargs)

from config import API_BASE_URL, API_KEY, MAX_TICK, NET_LIMIT, GROSS_LIMIT
from api import RITApi


# ============================================================
# HELPERS
# ============================================================

class TimedCall:
    """Context manager to measure API call latency."""
    def __init__(self):
        self.elapsed_ms = 0
    def __enter__(self):
        self._start = time.perf_counter()
        return self
    def __exit__(self, *args):
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000


def safe_div(a, b):
    return a / b if b else 0.0


# ============================================================
# MAIN MONITOR
# ============================================================

def main():
    api = RITApi()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = Path(__file__).resolve().parent / "monitor_logs"
    log_dir.mkdir(exist_ok=True)

    print("=" * 70)
    print("  COMPREHENSIVE MONITOR v2")
    print(f"  API: {API_BASE_URL} | Key: {API_KEY}")
    print("  Monitors current session immediately. Run alongside main.py.")
    print("=" * 70)

    # --- Connect ---
    print("\nConnecting to RIT...")
    while True:
        if api.is_connected():
            print("Connected.")
            break
        time.sleep(1)

    # --- Wait for active ---
    print("Waiting for ACTIVE case...")
    while True:
        case = api.get_case()
        if case and case.get("status") in ("ACTIVE", "RUNNING"):
            break
        time.sleep(0.5)

    case = api.get_case()
    period = case.get("period", "?") if case else "?"
    start_tick = case.get("tick", 0) if case else 0

    # Discover tickers
    securities = api.get_securities() or []
    tickers = sorted(set(
        str(s.get('ticker', '')) for s in securities
        if str(s.get('ticker', '')) and str(s.get('ticker', '')) != 'USD'
    ))

    print(f"\n  Session ACTIVE | Period {period} | Tick {start_tick}")
    print(f"  Tickers: {', '.join(tickers)}")
    print(f"  Logging to: {log_dir}")
    print()

    # --------------------------------------------------------
    # Open all CSV files
    # --------------------------------------------------------

    # 1) Tick data - per-tick market snapshot
    tick_csv_path = log_dir / f"tick_data_{ts}.csv"
    tick_f = tick_csv_path.open("w", newline="")
    tick_w = csv.writer(tick_f)
    tick_cols = ["timestamp", "tick", "period", "pnl", "nlv"]
    for t in tickers:
        tick_cols += [
            f"{t}_pos", f"{t}_bid", f"{t}_ask", f"{t}_mid", f"{t}_spread",
            f"{t}_last", f"{t}_bid_depth", f"{t}_ask_depth",
            f"{t}_unrealized", f"{t}_realized",
        ]
    tick_cols += ["net_pos", "gross_pos", "net_pct", "gross_pct",
                  "open_order_count", "active_tender_count", "loop_ms"]
    tick_w.writerow(tick_cols)

    # 2) Orders - every order we observe
    orders_csv_path = log_dir / f"orders_{ts}.csv"
    orders_f = orders_csv_path.open("w", newline="")
    orders_w = csv.writer(orders_f)
    orders_w.writerow([
        "timestamp", "tick", "order_id", "ticker", "type", "action",
        "quantity", "quantity_filled", "price", "status", "vwap",
    ])

    # 3) Tenders
    tenders_csv_path = log_dir / f"tenders_{ts}.csv"
    tenders_f = tenders_csv_path.open("w", newline="")
    tenders_w = csv.writer(tenders_f)
    tenders_w.writerow([
        "timestamp", "tick", "tender_id", "ticker", "type", "action",
        "quantity", "price", "expires", "mid_at_arrival", "edge_per_share",
        "close_side_depth", "spread_at_arrival", "inferred_outcome",
    ])

    # 4) Book snapshots - full depth
    book_csv_path = log_dir / f"book_snapshots_{ts}.csv"
    book_f = book_csv_path.open("w", newline="")
    book_w = csv.writer(book_f)
    book_w.writerow([
        "timestamp", "tick", "ticker", "side", "level",
        "price", "quantity", "cumulative_qty",
    ])

    # 5) Execution / slippage analysis
    exec_csv_path = log_dir / f"execution_{ts}.csv"
    exec_f = exec_csv_path.open("w", newline="")
    exec_w = csv.writer(exec_f)
    exec_w.writerow([
        "timestamp", "tick", "order_id", "ticker", "action", "type",
        "fill_qty", "fill_price", "mid_at_fill", "slippage_per_share",
        "slippage_bps", "total_slippage",
    ])

    # 6) API health
    health_csv_path = log_dir / f"api_health_{ts}.csv"
    health_f = health_csv_path.open("w", newline="")
    health_w = csv.writer(health_f)
    health_w.writerow([
        "timestamp", "tick", "call", "latency_ms", "success",
    ])

    # 7) Event log
    event_path = log_dir / f"events_{ts}.log"
    event_f = event_path.open("w", encoding="utf-8")

    def log_event(msg):
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        line = f"[{timestamp}] {msg}"
        event_f.write(line + "\n")
        event_f.flush()
        print(f"  {line}")

    log_event(f"Monitor v2 started | period={period} | tickers={tickers}")

    # --------------------------------------------------------
    # State tracking
    # --------------------------------------------------------
    last_tick = -1
    seen_tenders = {}          # tender_id -> tender dict at first sight
    seen_orders = {}           # order_id -> last known state
    filled_order_ids = set()   # orders we already logged fills for
    last_positions = {t: 0 for t in tickers}
    last_mids = {t: 0.0 for t in tickers}

    # Aggregated stats
    stats = {
        "ticks_observed": 0,
        "tenders_total": 0,
        "tenders_accepted": 0,
        "tenders_rejected": 0,
        "orders_total": 0,
        "orders_filled": 0,
        "orders_cancelled": 0,
        "total_volume_filled": 0,
        "total_slippage": 0.0,
        "api_calls": 0,
        "api_errors": 0,
        "api_total_latency_ms": 0.0,
        "max_net_pos": 0,
        "max_gross_pos": 0,
        "peak_pnl": -999999,
        "trough_pnl": 999999,
        "position_changes": [],
        "tender_log": [],
    }

    def timed_api(name, func, *args, **kwargs):
        """Call an API function, measure latency, log health."""
        tc = TimedCall()
        with tc:
            result = func(*args, **kwargs)
        success = result is not None
        stats["api_calls"] += 1
        stats["api_total_latency_ms"] += tc.elapsed_ms
        if not success:
            stats["api_errors"] += 1
        health_w.writerow([
            datetime.now().isoformat(timespec="milliseconds"),
            last_tick, name, round(tc.elapsed_ms, 1), success,
        ])
        return result

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------
    try:
        while True:
            loop_start = time.perf_counter()

            case = timed_api("get_case", api.get_case)
            if not case:
                time.sleep(0.5)
                continue

            status = case.get("status", "")
            if status not in ("ACTIVE", "RUNNING"):
                log_event(f"Case ended: status={status}")
                break

            tick = case.get("tick", 0)
            if tick >= MAX_TICK:
                log_event(f"Reached MAX_TICK={MAX_TICK}")
                break
            if tick == last_tick:
                time.sleep(0.03)
                continue
            last_tick = tick
            stats["ticks_observed"] += 1

            now_str = datetime.now().isoformat(timespec="milliseconds")

            # ============ SECURITIES ============
            securities = timed_api("get_securities", api.get_securities) or []
            trader = timed_api("get_trader", api.get_trader)
            nlv = trader.get("nlv", 0.0) if trader else 0.0

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
                mid = round((bid + ask) / 2, 4) if bid > 0 and ask > 0 else 0.0
                spread = round(ask - bid, 4) if bid > 0 and ask > 0 else 0.0

                sec_data[t] = {
                    'pos': pos, 'bid': bid, 'ask': ask, 'mid': mid,
                    'spread': spread, 'last': last_px,
                    'bid_size': bid_size, 'ask_size': ask_size,
                    'unrealized': unrealized, 'realized': realized,
                }
                total_pnl += unrealized + realized
                net_pos += pos
                gross_pos += abs(pos)
                last_mids[t] = mid

            stats["peak_pnl"] = max(stats["peak_pnl"], total_pnl)
            stats["trough_pnl"] = min(stats["trough_pnl"], total_pnl)
            stats["max_net_pos"] = max(stats["max_net_pos"], abs(net_pos))
            stats["max_gross_pos"] = max(stats["max_gross_pos"], gross_pos)

            # Detect position changes
            for t in tickers:
                cur_pos = sec_data.get(t, {}).get('pos', 0)
                prev_pos = last_positions.get(t, 0)
                if cur_pos != prev_pos:
                    delta = cur_pos - prev_pos
                    log_event(f"POSITION {t}: {prev_pos:+,} -> {cur_pos:+,} "
                              f"(delta={delta:+,})")
                    stats["position_changes"].append({
                        'tick': tick, 'ticker': t,
                        'prev': prev_pos, 'new': cur_pos, 'delta': delta,
                    })
                last_positions[t] = cur_pos

            # ============ FULL ORDER BOOK ============
            # Capture full book depth every tick for each ticker
            for t in tickers:
                book = timed_api(f"get_book_{t}", api.get_book, t)
                if not book:
                    continue

                bid_depth_total = 0
                ask_depth_total = 0

                for side_key, side_label in [("bid", "BID"), ("ask", "ASK")]:
                    levels = book.get(side_key, [])
                    cum_qty = 0
                    for i, lvl in enumerate(levels):
                        px = float(lvl.get("price", 0))
                        qty = max(0, int(lvl.get("quantity", 0))
                                  - int(lvl.get("quantity_filled", 0)))
                        cum_qty += qty
                        book_w.writerow([
                            now_str, tick, t, side_label, i,
                            px, qty, cum_qty,
                        ])
                        if side_label == "BID":
                            bid_depth_total += qty
                        else:
                            ask_depth_total += qty

                # Update sec_data with full depth (overwrite top-of-book sizes)
                if t in sec_data:
                    sec_data[t]['bid_depth'] = bid_depth_total
                    sec_data[t]['ask_depth'] = ask_depth_total

            # ============ TENDERS ============
            tenders = timed_api("get_tenders", api.get_tenders) or []

            for tender in tenders:
                t = str(tender.get('ticker', ''))
                tid = int(tender.get('tender_id', -1))
                action = str(tender.get('action', '')).upper()
                qty = int(tender.get('quantity', 0))
                raw_price = tender.get('price')
                price = float(raw_price) if raw_price is not None else None
                expires = int(tender.get('expires', 0))
                is_fixed = bool(tender.get('is_fixed_bid', True))
                tender_type = "PRIVATE" if is_fixed else "AUCTION"

                if tid not in seen_tenders:
                    seen_tenders[tid] = {
                        'tick': tick, 'ticker': t, 'type': tender_type,
                        'action': action, 'qty': qty, 'price': price,
                        'expires': expires, 'outcome': 'PENDING',
                    }
                    stats["tenders_total"] += 1

                    # Analysis at arrival
                    sd = sec_data.get(t, {})
                    mid = sd.get('mid', 0)
                    spread = sd.get('spread', 0)
                    close_side = 'SELL' if action == 'BUY' else 'BUY'
                    close_depth = sd.get('bid_depth', sd.get('bid_size', 0)) if close_side == 'SELL' \
                        else sd.get('ask_depth', sd.get('ask_size', 0))

                    edge = 0.0
                    if price is not None and mid > 0:
                        if action == 'BUY':
                            edge = mid - price
                        else:
                            edge = price - mid

                    price_str = f"${price:.2f}" if price is not None else "AUCTION"
                    log_event(f"NEW TENDER: {tender_type} #{tid} | {t} {action} "
                              f"{qty:,} @ {price_str} | exp={expires} | "
                              f"mid={mid:.2f} edge={edge:.4f}/sh depth={close_depth:,}")

                    tenders_w.writerow([
                        now_str, tick, tid, t, tender_type, action,
                        qty, price, expires, mid, round(edge, 4),
                        close_depth, spread, "PENDING",
                    ])

            # Check for expired/disappeared tenders -> infer outcome
            active_tender_ids = {int(td.get('tender_id', -1)) for td in tenders}
            for tid, tinfo in list(seen_tenders.items()):
                if tinfo['outcome'] != 'PENDING':
                    continue
                if tid not in active_tender_ids:
                    # Tender disappeared - check if position changed
                    t = tinfo['ticker']
                    recent_changes = [
                        p for p in stats["position_changes"]
                        if p['ticker'] == t
                        and tinfo['tick'] <= p['tick'] <= tick
                    ]
                    was_accepted = any(
                        abs(p['delta']) >= tinfo['qty'] * 0.3
                        for p in recent_changes
                    )
                    outcome = "ACCEPTED" if was_accepted else "EXPIRED/REJECTED"
                    tinfo['outcome'] = outcome
                    if was_accepted:
                        stats["tenders_accepted"] += 1
                    else:
                        stats["tenders_rejected"] += 1
                    log_event(f"TENDER RESOLVED: #{tid} {tinfo['ticker']} -> {outcome}")

            # ============ ORDERS (all states) ============
            for order_status in ["OPEN", "TRANSACTED", "CANCELLED"]:
                orders = timed_api(f"get_orders_{order_status}",
                                   api.get_orders, order_status) or []

                for o in orders:
                    oid = o.get("order_id", o.get("id", "?"))
                    oid_str = str(oid)
                    ticker = o.get("ticker", "?")
                    otype = o.get("type", "?")
                    oaction = o.get("action", "?")
                    oqty = int(o.get("quantity", 0))
                    ofilled = int(o.get("quantity_filled", 0))
                    oprice = o.get("price", 0)
                    ostatus = order_status
                    vwap = float(o.get("vwap", 0)) if o.get("vwap") else 0.0

                    prev = seen_orders.get(oid_str)
                    is_new = prev is None
                    status_changed = prev and prev.get("status") != ostatus
                    fill_changed = prev and prev.get("filled", 0) != ofilled

                    if is_new or status_changed or fill_changed:
                        orders_w.writerow([
                            now_str, tick, oid, ticker, otype, oaction,
                            oqty, ofilled, oprice, ostatus, vwap,
                        ])

                        if is_new:
                            stats["orders_total"] += 1

                    # Slippage analysis for filled orders
                    if ostatus == "TRANSACTED" and oid_str not in filled_order_ids:
                        filled_order_ids.add(oid_str)
                        stats["orders_filled"] += 1
                        stats["total_volume_filled"] += ofilled

                        mid = last_mids.get(ticker, 0)
                        fill_price = vwap if vwap > 0 else float(oprice) if oprice else 0
                        if mid > 0 and fill_price > 0:
                            if str(oaction).upper() == "BUY":
                                slip = fill_price - mid
                            else:
                                slip = mid - fill_price
                            slip_bps = safe_div(slip, mid) * 10000
                            total_slip = slip * ofilled
                            stats["total_slippage"] += total_slip

                            exec_w.writerow([
                                now_str, tick, oid, ticker, oaction, otype,
                                ofilled, fill_price, mid,
                                round(slip, 4), round(slip_bps, 2),
                                round(total_slip, 2),
                            ])

                            if abs(slip_bps) > 10:
                                log_event(f"SLIPPAGE: {oaction} {ofilled:,} {ticker} "
                                          f"@ {fill_price:.2f} vs mid {mid:.2f} "
                                          f"({slip_bps:+.1f}bps, ${total_slip:+.2f})")

                    if ostatus == "CANCELLED" and is_new:
                        stats["orders_cancelled"] += 1

                    seen_orders[oid_str] = {
                        "status": ostatus, "filled": ofilled,
                    }

            # ============ WRITE TICK ROW ============
            loop_ms = (time.perf_counter() - loop_start) * 1000

            row = [now_str, tick, period, round(total_pnl, 2), round(nlv, 2)]
            for t in tickers:
                sd = sec_data.get(t, {})
                row += [
                    sd.get('pos', 0), sd.get('bid', 0), sd.get('ask', 0),
                    sd.get('mid', 0), sd.get('spread', 0), sd.get('last', 0),
                    sd.get('bid_depth', sd.get('bid_size', 0)),
                    sd.get('ask_depth', sd.get('ask_size', 0)),
                    sd.get('unrealized', 0), sd.get('realized', 0),
                ]
            row += [
                net_pos, gross_pos,
                round(safe_div(abs(net_pos), NET_LIMIT) * 100, 1),
                round(safe_div(gross_pos, GROSS_LIMIT) * 100, 1),
                sum(1 for o in seen_orders.values() if o["status"] == "OPEN"),
                len(active_tender_ids),
                round(loop_ms, 1),
            ]
            tick_w.writerow(row)

            # Flush all files periodically
            if tick % 10 == 0:
                for f in [tick_f, orders_f, tenders_f, book_f, exec_f, health_f]:
                    f.flush()

            # ============ TERMINAL DASHBOARD ============
            ticks_left = max(0, MAX_TICK - tick)
            if tick % 10 == 0 or tick <= 3:
                pos_str = ", ".join(
                    f"{t}:{sec_data.get(t,{}).get('pos',0):+,}"
                    for t in tickers
                    if sec_data.get(t, {}).get('pos', 0) != 0
                ) or "flat"

                print(f"\n  {'─'*66}")
                print(f"  T={tick:>3}/{MAX_TICK} ({ticks_left} left) | "
                      f"PnL ${total_pnl:>+10,.2f} | NLV ${nlv:>10,.2f}")
                print(f"  Pos: {pos_str}")
                print(f"  Net/Gross: {abs(net_pos):,}/{gross_pos:,} "
                      f"({safe_div(abs(net_pos),NET_LIMIT)*100:.0f}%/"
                      f"{safe_div(gross_pos,GROSS_LIMIT)*100:.0f}% of limit)")
                print(f"  Tenders: {stats['tenders_accepted']}A/"
                      f"{stats['tenders_rejected']}R/"
                      f"{stats['tenders_total']}T | "
                      f"Orders: {stats['orders_filled']}F/"
                      f"{stats['orders_cancelled']}C/"
                      f"{stats['orders_total']}T | "
                      f"Vol: {stats['total_volume_filled']:,}")
                print(f"  Slippage: ${stats['total_slippage']:+,.2f} | "
                      f"API: {stats['api_calls']} calls, "
                      f"{stats['api_errors']} err, "
                      f"avg {safe_div(stats['api_total_latency_ms'], max(stats['api_calls'],1)):.0f}ms")

                # Per-ticker book depth
                depth_parts = []
                for t in tickers:
                    sd = sec_data.get(t, {})
                    bd = sd.get('bid_depth', sd.get('bid_size', 0))
                    ad = sd.get('ask_depth', sd.get('ask_size', 0))
                    depth_parts.append(f"{t}[B:{bd:,}/A:{ad:,}]")
                print(f"  Depth: {' | '.join(depth_parts)}")

                # Spreads
                spread_parts = []
                for t in tickers:
                    sd = sec_data.get(t, {})
                    sp = sd.get('spread', 0)
                    mid = sd.get('mid', 0)
                    sp_bps = safe_div(sp, mid) * 10000 if mid > 0 else 0
                    spread_parts.append(f"{t}:{sp:.3f}({sp_bps:.0f}bp)")
                print(f"  Spread: {' | '.join(spread_parts)}")
                print(f"  Loop: {loop_ms:.0f}ms")

            # Pacing
            elapsed = time.perf_counter() - loop_start
            time.sleep(max(0.02, 0.08 - elapsed))

    except KeyboardInterrupt:
        log_event("Monitor interrupted by user")
    finally:
        # Flush and close all files
        for f in [tick_f, orders_f, tenders_f, book_f, exec_f, health_f, event_f]:
            f.flush()
            f.close()

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------
    final_pnl = total_pnl
    final_nlv = nlv
    avg_slip_per_share = safe_div(stats["total_slippage"],
                                   max(stats["total_volume_filled"], 1))

    print(f"\n{'='*70}")
    print(f"  MONITOR v2 - SESSION SUMMARY")
    print(f"{'='*70}")
    print(f"  Period: {period}")
    print(f"  Ticks observed: {stats['ticks_observed']} "
          f"(from {start_tick} to {last_tick})")
    print(f"  Final PnL: ${final_pnl:>+,.2f} | NLV: ${final_nlv:>,.2f}")
    print(f"  Peak PnL: ${stats['peak_pnl']:>+,.2f} | "
          f"Trough: ${stats['trough_pnl']:>+,.2f}")
    print(f"  Max Net: {stats['max_net_pos']:,} | "
          f"Max Gross: {stats['max_gross_pos']:,}")

    print(f"\n  --- TENDERS ---")
    print(f"  Total: {stats['tenders_total']} | "
          f"Accepted: {stats['tenders_accepted']} | "
          f"Rejected/Expired: {stats['tenders_rejected']}")
    if stats['tenders_total'] > 0:
        print(f"  Accept rate: "
              f"{safe_div(stats['tenders_accepted'], stats['tenders_total'])*100:.0f}%")
        for tid, tinfo in sorted(seen_tenders.items()):
            price_str = f"${tinfo['price']:.2f}" if tinfo['price'] else "AUCTION"
            print(f"    T={tinfo['tick']:>3} #{tid} {tinfo['type']:>7} "
                  f"{tinfo['ticker']} {tinfo['action']} {tinfo['qty']:>6,} "
                  f"@ {price_str:>8} -> {tinfo['outcome']}")

    print(f"\n  --- ORDERS ---")
    print(f"  Total: {stats['orders_total']} | "
          f"Filled: {stats['orders_filled']} | "
          f"Cancelled: {stats['orders_cancelled']}")
    print(f"  Volume filled: {stats['total_volume_filled']:,}")

    print(f"\n  --- EXECUTION QUALITY ---")
    print(f"  Total slippage: ${stats['total_slippage']:>+,.2f}")
    print(f"  Avg slippage/share: ${avg_slip_per_share:>+.4f}")

    print(f"\n  --- API HEALTH ---")
    print(f"  Calls: {stats['api_calls']} | Errors: {stats['api_errors']}")
    avg_lat = safe_div(stats["api_total_latency_ms"], max(stats["api_calls"], 1))
    print(f"  Avg latency: {avg_lat:.1f}ms")

    if stats["position_changes"]:
        print(f"\n  --- POSITION CHANGES ({len(stats['position_changes'])}) ---")
        for p in stats["position_changes"]:
            print(f"    T={p['tick']:>3} {p['ticker']}: "
                  f"{p['prev']:>+8,} -> {p['new']:>+8,} "
                  f"(delta={p['delta']:>+8,})")

    print(f"\n  --- FILES ---")
    print(f"    Tick data:   {tick_csv_path}")
    print(f"    Orders:      {orders_csv_path}")
    print(f"    Tenders:     {tenders_csv_path}")
    print(f"    Book depth:  {book_csv_path}")
    print(f"    Execution:   {exec_csv_path}")
    print(f"    API health:  {health_csv_path}")
    print(f"    Events:      {event_path}")
    print(f"{'='*70}")

    # Save summary JSON
    summary = {
        "period": period,
        "tickers": tickers,
        "start_tick": start_tick,
        "last_tick": last_tick,
        "ticks_observed": stats["ticks_observed"],
        "final_pnl": round(final_pnl, 2),
        "final_nlv": round(final_nlv, 2),
        "peak_pnl": round(stats["peak_pnl"], 2),
        "trough_pnl": round(stats["trough_pnl"], 2),
        "max_net_pos": stats["max_net_pos"],
        "max_gross_pos": stats["max_gross_pos"],
        "tenders_total": stats["tenders_total"],
        "tenders_accepted": stats["tenders_accepted"],
        "tenders_rejected": stats["tenders_rejected"],
        "tender_details": [
            {**v, "id": k} for k, v in sorted(seen_tenders.items())
        ],
        "orders_total": stats["orders_total"],
        "orders_filled": stats["orders_filled"],
        "orders_cancelled": stats["orders_cancelled"],
        "total_volume_filled": stats["total_volume_filled"],
        "total_slippage": round(stats["total_slippage"], 2),
        "avg_slippage_per_share": round(avg_slip_per_share, 4),
        "api_calls": stats["api_calls"],
        "api_errors": stats["api_errors"],
        "avg_api_latency_ms": round(avg_lat, 1),
        "position_changes": stats["position_changes"],
    }
    summary_path = log_dir / f"summary_{ts}.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"    Summary:     {summary_path}")


if __name__ == "__main__":
    main()
