# -*- coding: utf-8 -*-
"""LT3 Tender Liquidity Bot — Entry point.

Monitors tender offers (Private + Competitive Auction), evaluates them,
accepts profitable ones, and unwinds positions via TWAP.

@author: oliverzhoumac
"""

import signal
import requests
from time import sleep
from typing import Dict, Set

from config import (
    API_KEY,
    WATCHLIST,
    POSITION_LIMITS,
    SLEEP_SEC,
    MAX_TICK,
    COMMISSION_PER_SHARE,
    MIN_PROFIT_PER_SHARE,
    MIN_TENDER_CONFIDENCE,
    DEBUG_BOOK,
    ENDGAME_UNWIND_TICKS,
)
from rit_api import (
    ApiException,
    get_tick,
    get_securities,
    get_order_book,
    get_tender_offers,
    accept_tender,
    get_position,
    synthetic_book_from_quotes,
)
from liquidity_analyzer import LiquidityAnalyzer
from risk_manager import RiskManager
from execution_engine import ExecutionEngine

shutdown = False


def _calc_breakeven(tender_price: float, tender_action: str) -> float:
    """Calculate breakeven price for a fixed-bid tender."""
    fee = COMMISSION_PER_SHARE + MIN_PROFIT_PER_SHARE
    if tender_action == 'BUY':
        # Bought at tender_price, need to sell above this
        return tender_price + fee
    else:
        # Sold at tender_price, need to buy below this
        return tender_price - fee


def main():
    with requests.Session() as session:
        session.headers.update(API_KEY)

        analyzer = LiquidityAnalyzer()
        risk_manager = RiskManager()
        engine = ExecutionEngine(session)

        processed_tenders: Set[int] = set()
        last_tender_summary: Dict[int, str] = {}

        print("=" * 80)
        print("LT3 Liquidity Bot (Private + Auction + TWAP) Started")
        print("=" * 80)

        tick = 0
        while tick < MAX_TICK and not shutdown:
            try:
                tick = get_tick(session)

                if tick == 0:
                    print("Waiting for case to start...")
                    sleep(1)
                    continue

                # ── 1) Market data ──
                securities_data = get_securities(session)
                risk_manager.update_positions(securities_data)

                # ── 2) Position limits ──
                limits = risk_manager.check_position_limits()
                status = ("OK" if (limits['net_ok'] and limits['gross_ok'])
                          else "WARN")
                print(f"\nTick {tick}/{MAX_TICK} | Limits: {status} "
                      f"(net {limits['net_used']}/{POSITION_LIMITS['net']}, "
                      f"gross {limits['gross_used']}/{POSITION_LIMITS['gross']})")

                # ── 3) TWAP heartbeat — process pending unwind batches ──
                twap_logs = engine.tick_tasks(tick)
                for log in twap_logs:
                    print(f"  [TWAP] {log}")

                # Print active tasks
                for task in engine.get_active_tasks():
                    print(f"  [TASK] {task.task_id}: "
                          f"{task.quantity_sent:,}/{task.total_quantity:,} "
                          f"sent, {task.remaining:,} remaining")

                # ── 4) Tender offers ──
                tenders = get_tender_offers(session)
                for tender in tenders:
                    ticker = str(tender.get('ticker', ''))
                    if ticker not in WATCHLIST:
                        continue

                    tender_id = int(tender.get('tender_id', -1))

                    # Skip already processed
                    if tender_id in processed_tenders:
                        continue

                    tender_action = str(tender.get('action', '')).upper()
                    tender_qty = int(tender.get('quantity', 0))
                    tender_exp = int(tender.get('expires', 0))
                    is_fixed = bool(tender.get('is_fixed_bid', True))

                    # Pre-check position limits (skip this tick, retry next)
                    if not risk_manager.can_accept_tender(
                            tender_qty, tender_action, ticker):
                        continue

                    # Get order book
                    book = get_order_book(session, ticker)
                    if (len(book.get('bid', [])) == 0
                            and len(book.get('ask', [])) == 0):
                        q = risk_manager.positions.get(ticker)
                        if DEBUG_BOOK:
                            print(f"  [DEBUG] Empty book for {ticker}, "
                                  "using synthetic quotes")
                        if q:
                            book = synthetic_book_from_quotes(
                                q['bid'], q['bid_size'],
                                q['ask'], q['ask_size'])

                    # ── Branch on tender type ──

                    if is_fixed:
                        # ====== PRIVATE TENDER (fixed price) ======
                        evaluation = analyzer.evaluate_tender_offer(
                            tender, book)
                        m = evaluation.get('metrics', {})
                        confidence = float(evaluation.get('confidence', 0.0))
                        should_take = (
                            evaluation.get('decision') == 'ACCEPT'
                            and confidence >= MIN_TENDER_CONFIDENCE
                        )

                        # Throttled printing
                        summary = (
                            f"id={tender_id}|{ticker}|{tender_action}"
                            f"|q={tender_qty}|exp={tender_exp}"
                            f"|{evaluation['decision']}"
                            f"|take={should_take}"
                            f"|conf={confidence:.1%}")
                        if last_tender_summary.get(tender_id) != summary:
                            print(f"\n{'!' * 70}")
                            print(f"  PRIVATE Tender #{tender_id} | "
                                  f"{ticker} {tender_action} "
                                  f"{tender_qty:,} "
                                  f"| exp={tender_exp}")
                            print(f"  Decision: "
                                  f"{evaluation['decision']} "
                                  f"| Confidence: "
                                  f"{confidence:.1%}")
                            print(f"  Take rule: "
                                  f"conf >= {MIN_TENDER_CONFIDENCE:.0%} "
                                  f"-> {'TAKE' if should_take else 'SKIP'}")
                            print(f"  Reason: {evaluation['reason']}")
                            print(f"  Depth: "
                                  f"{m.get('total_depth', 0):,} "
                                  f"| Ratio: "
                                  f"{m.get('depth_ratio', 0.0):.1%}")
                            print(f"{'!' * 70}")
                            last_tender_summary[tender_id] = summary

                        if should_take:
                            ok = accept_tender(
                                session, tender_id, price=None)
                            if ok:
                                print(f"  >> ACCEPTED "
                                      f"tender #{tender_id}")
                                processed_tenders.add(tender_id)
                                # Read actual position to determine
                                # unwind direction — don't trust
                                # tender action semantics
                                actual = get_position(
                                    session, ticker)
                                if actual > 0:
                                    close = 'SELL'
                                elif actual < 0:
                                    close = 'BUY'
                                else:
                                    print(f"  >> pos=0 after accept, "
                                          f"nothing to unwind")
                                    continue
                                tender_price = float(
                                    tender.get('price', 0.0))
                                breakeven = _calc_breakeven(
                                    tender_price, close)
                                engine.create_unwind_task(
                                    ticker=ticker,
                                    close_action=close,
                                    total_quantity=abs(actual),
                                    breakeven_price=breakeven,
                                    current_tick=tick,
                                )
                        elif evaluation.get('decision') == 'ACCEPT':
                            print(f"  >> SKIP tender #{tender_id}: "
                                  f"low confidence "
                                  f"({confidence:.1%} < "
                                  f"{MIN_TENDER_CONFIDENCE:.0%})")
                        # else: keep waiting — re-evaluate next tick

                    else:
                        # ====== COMPETITIVE AUCTION ======
                        result = analyzer.calculate_auction_bid_price(
                            tender, book)
                        m = result.get('metrics', {})

                        print(f"\n{'*' * 70}")
                        print(f"  AUCTION Tender #{tender_id} | "
                              f"{ticker} {tender_action} "
                              f"{tender_qty:,} "
                              f"| exp={tender_exp}")
                        print(f"  Decision: {result['decision']}")
                        print(f"  Reason: {result['reason']}")
                        if result['decision'] == 'BID':
                            print(
                                f"  Bid: {result['bid_price']:.2f} "
                                f"| BE: "
                                f"{result['breakeven_price']:.2f} "
                                f"| E[profit/sh]: "
                                f"{result.get('expected_profit_per_share', 0):.4f}")
                        print(f"  Depth: "
                              f"{m.get('total_depth', 0):,} "
                              f"| Ratio: "
                              f"{m.get('depth_ratio', 0.0):.1%}")
                        print(f"{'*' * 70}")

                        if result['decision'] == 'BID':
                            bid_price = result['bid_price']
                            ok = accept_tender(
                                session, tender_id, price=bid_price)
                            if ok:
                                print(f"  >> BID submitted "
                                      f"#{tender_id} @ {bid_price:.2f}")
                                processed_tenders.add(tender_id)
                                # Read actual position for direction
                                actual = get_position(
                                    session, ticker)
                                if actual > 0:
                                    close = 'SELL'
                                elif actual < 0:
                                    close = 'BUY'
                                else:
                                    print(f"  >> pos=0 after bid, "
                                          f"nothing to unwind")
                                    continue
                                engine.create_unwind_task(
                                    ticker=ticker,
                                    close_action=close,
                                    total_quantity=abs(actual),
                                    breakeven_price=result[
                                        'breakeven_price'],
                                    current_tick=tick,
                                )
                            else:
                                print(f"  >> BID FAILED "
                                      f"#{tender_id}")
                        # else: keep waiting — re-evaluate next tick

                # ── 5) Residual position auto-unwind (all ticks) ──
                in_endgame = (MAX_TICK - tick) <= ENDGAME_UNWIND_TICKS
                active_tickers = {t.ticker for t in engine.get_active_tasks()}
                for ticker in WATCHLIST:
                    if ticker in active_tickers:
                        continue

                    snapshot_pos = int(
                        risk_manager.positions.get(ticker, {}).get('position', 0)
                    )
                    if snapshot_pos == 0:
                        continue

                    actual = get_position(session, ticker)
                    if actual == 0:
                        continue

                    close = 'SELL' if actual > 0 else 'BUY'
                    engine.create_unwind_task(
                        ticker=ticker,
                        close_action=close,
                        total_quantity=abs(actual),
                        breakeven_price=0.0,
                        current_tick=tick,
                    )
                    active_tickers.add(ticker)
                    tag = "ENDGAME" if in_endgame else "AUTO"
                    print(f"  [{tag}] Residual pos on {ticker}: "
                          f"{actual:+,}. Created unwind task.")

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
