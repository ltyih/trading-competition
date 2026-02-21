# -*- coding: utf-8 -*-
"""LT3 Tender Liquidity Bot — Execution engine.

TWAP-based unwind execution with batch ordering across ticks.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any
from time import sleep
from statistics import pstdev

from config import (
    BASE_URL,
    COMMISSION_PER_SHARE,
    MAX_ORDER_SIZE,
    MAX_DEPTH_RATIO,
    MIN_PROFIT_PER_SHARE,
    TWAP_BATCH_SIZE,
    TWAP_TICK_INTERVAL,
    TWAP_AGGRESSIVE_OFFSET,
    UNWIND_BASE_PARTICIPATION,
    UNWIND_MAX_PARTICIPATION,
    UNWIND_MIN_ORDER_SIZE,
    UNWIND_VIRTUAL_TICKS_FLOOR,
    UNWIND_VOL_LOOKBACK,
    UNWIND_VOL_LOW,
    UNWIND_VOL_HIGH,
    UNWIND_RISK_MULT_LOW,
    UNWIND_RISK_MULT_MED,
    UNWIND_RISK_MULT_HIGH,
    SOFT_BREAKEVEN_SLIPPAGE,
    SOFT_BREAKEVEN_URGENCY,
    SOFT_BREAKEVEN_BATCH_FRACTION,
    MARKETABLE_LIMIT_EPS,
    UNWIND_TICKER_PROFILE,
    MAX_TICK,
    ENDGAME_UNWIND_TICKS,
    FINAL_FLATTEN_TICKS,
    ENDGAME_MAX_SLICES_PER_TICK,
)
from rit_api import get_order_book, get_position


# ========== TWAP Task ==========

@dataclass
class UnwindTask:
    """Tracks a single TWAP unwind job across multiple ticks."""
    task_id: str
    ticker: str
    close_action: str          # 'BUY' or 'SELL'
    total_quantity: int
    quantity_sent: int
    breakeven_price: float
    batch_size: int
    tick_interval: int
    last_send_tick: int
    created_tick: int
    status: str = 'ACTIVE'     # 'ACTIVE', 'COMPLETED'

    @property
    def remaining(self) -> int:
        return max(0, self.total_quantity - self.quantity_sent)


# ========== Execution Engine ==========

class ExecutionEngine:
    """Unwind execution engine with TWAP support."""

    def __init__(self, session):
        self.session = session
        self.active_tasks: List[UnwindTask] = []
        self._task_counter = 0
        self._mid_history: Dict[str, List[float]] = {}

    @staticmethod
    def _available_qty(level: Dict[str, Any]) -> int:
        q = int(level.get('quantity', 0))
        qf = int(level.get('quantity_filled', 0))
        return max(0, q - qf)

    def _update_mid(self, ticker: str, bid_levels: List[Dict[str, Any]],
                    ask_levels: List[Dict[str, Any]]) -> None:
        if not bid_levels or not ask_levels:
            return
        bid = float(bid_levels[0].get('price', 0.0))
        ask = float(ask_levels[0].get('price', 0.0))
        if bid <= 0 or ask <= 0:
            return
        mid = (bid + ask) / 2.0
        hist = self._mid_history.setdefault(ticker, [])
        hist.append(mid)
        if len(hist) > UNWIND_VOL_LOOKBACK:
            del hist[:-UNWIND_VOL_LOOKBACK]

    def _vol_level(self, ticker: str) -> str:
        mids = self._mid_history.get(ticker, [])
        if len(mids) < 3:
            return 'MED'
        rets: List[float] = []
        for i in range(1, len(mids)):
            prev = mids[i - 1]
            cur = mids[i]
            if prev > 0:
                rets.append((cur - prev) / prev)
        if len(rets) < 2:
            return 'MED'
        sigma = pstdev(rets)
        if sigma >= UNWIND_VOL_HIGH:
            return 'HIGH'
        if sigma <= UNWIND_VOL_LOW:
            return 'LOW'
        return 'MED'

    @staticmethod
    def _risk_mult(vol_level: str) -> float:
        if vol_level == 'HIGH':
            return UNWIND_RISK_MULT_HIGH
        if vol_level == 'LOW':
            return UNWIND_RISK_MULT_LOW
        return UNWIND_RISK_MULT_MED

    @staticmethod
    def _profile_for_ticker(ticker: str) -> Dict[str, float]:
        p = UNWIND_TICKER_PROFILE.get(ticker, {})
        return {
            'risk_mult': float(p.get('risk_mult', 1.0)),
            'base_participation': float(
                p.get('base_participation', UNWIND_BASE_PARTICIPATION)),
            'max_participation': float(
                p.get('max_participation', UNWIND_MAX_PARTICIPATION)),
            'normal_limit_min_clip': int(p.get('normal_limit_min_clip', 0)),
            'soft_be_urgency': float(
                p.get('soft_be_urgency', SOFT_BREAKEVEN_URGENCY)),
            'soft_be_slippage': float(
                p.get('soft_be_slippage', SOFT_BREAKEVEN_SLIPPAGE)),
            'soft_be_batch_fraction': float(
                p.get('soft_be_batch_fraction', SOFT_BREAKEVEN_BATCH_FRACTION)),
            'marketable_limit_eps': float(
                p.get('marketable_limit_eps', MARKETABLE_LIMIT_EPS)),
        }

    # ---- Low-level order sending ----

    def _post_order(self, payload: dict) -> bool:
        """Submit order with 429 retry."""
        resp = self.session.post(f'{BASE_URL}/orders', params=payload)
        if resp.status_code == 429:
            try:
                wait = float(resp.json().get('wait', 0.0))
            except Exception:
                wait = float(resp.headers.get('Retry-After', 0.0) or 0.0)
            if wait > 0:
                sleep(wait)
            resp = self.session.post(f'{BASE_URL}/orders', params=payload)
        
        # Debug logging for failed orders
        if not resp.ok:
            print(f"[ORDER_ERROR] Payload: {payload}")
            print(f"[ORDER_ERROR] Status: {resp.status_code}")
            print(f"[ORDER_ERROR] Response: {resp.text[:500]}")
        
        return resp.ok

    def send_market_order(self, ticker: str, action: str,
                          quantity: int) -> bool:
        return self._post_order({
            'ticker': ticker,
            'type': 'MARKET',
            'quantity': int(quantity),
            'action': action.upper(),
        })

    def send_limit_order(self, ticker: str, action: str,
                         quantity: int, price: float) -> bool:
        return self._post_order({
            'ticker': ticker,
            'type': 'LIMIT',
            'quantity': int(quantity),
            'action': action.upper(),
            'price': float(price),
        })

    # ---- TWAP Task Management ----

    def create_unwind_task(
        self,
        ticker: str,
        close_action: str,
        total_quantity: int,
        breakeven_price: float,
        current_tick: int,
        batch_size: int = TWAP_BATCH_SIZE,
        tick_interval: int = TWAP_TICK_INTERVAL,
    ) -> UnwindTask:
        """Create a new TWAP unwind task and add it to the queue."""
        self._task_counter += 1
        task = UnwindTask(
            task_id=f"unwind_{self._task_counter}_{ticker}",
            ticker=ticker,
            close_action=close_action,
            total_quantity=total_quantity,
            quantity_sent=0,
            breakeven_price=breakeven_price,
            batch_size=batch_size,
            tick_interval=tick_interval,
            last_send_tick=current_tick - tick_interval,  # send first batch immediately
            created_tick=current_tick,
        )
        self.active_tasks.append(task)
        return task

    def tick_tasks(self, current_tick: int) -> List[str]:
        """Process all active TWAP tasks. Called once per main loop tick.

        STRICT anti-speculation:
        - Must successfully read actual position (no guessing)
        - Only SELL if position > 0 (long), only BUY if position < 0 (short)
        - Batch capped to actual exposure — never trade past flat

        Returns list of action log strings.
        """
        logs: List[str] = []
        ticks_left_global = max(0, MAX_TICK - current_tick)
        endgame = ticks_left_global <= ENDGAME_UNWIND_TICKS

        for task in self.active_tasks:
            if task.status != 'ACTIVE':
                continue

            if task.remaining <= 0:
                task.status = 'COMPLETED'
                logs.append(f"{task.task_id}: COMPLETED "
                            f"({task.total_quantity:,} shares)")
                continue

            # Check if it's time to send next batch.
            # In endgame, send every tick to pace the remaining position.
            ticks_since = current_tick - task.last_send_tick
            if not endgame and ticks_since < task.tick_interval:
                continue

            # ── STRICT anti-speculation: read actual position ──
            try:
                actual_pos = get_position(self.session, task.ticker)
            except Exception:
                # Cannot verify position → do NOT trade
                logs.append(f"{task.task_id}: pos fetch failed, "
                            f"SKIP (no speculation)")
                continue

            # Direction check: only unwind, never open new exposure
            #   close_action='SELL' → we must be long  (pos > 0)
            #   close_action='BUY'  → we must be short (pos < 0)
            if task.close_action == 'SELL' and actual_pos <= 0:
                task.status = 'COMPLETED'
                logs.append(f"{task.task_id}: pos={actual_pos}, "
                            f"no long to SELL, done")
                continue
            if task.close_action == 'BUY' and actual_pos >= 0:
                task.status = 'COMPLETED'
                logs.append(f"{task.task_id}: pos={actual_pos}, "
                            f"no short to BUY, done")
                continue

            # How many shares we actually need to unwind
            unwindable = abs(actual_pos)

            # Read current book for algorithmic unwind controls.
            try:
                book = get_order_book(self.session, task.ticker)
            except Exception:
                logs.append(f"{task.task_id}: book fetch failed, SKIP")
                continue

            bid_levels = book.get('bid', [])
            ask_levels = book.get('ask', [])
            self._update_mid(task.ticker, bid_levels, ask_levels)

            levels = bid_levels if task.close_action == 'SELL' else ask_levels
            total_depth = sum(self._available_qty(lvl) for lvl in levels)
            best_px = float(levels[0].get('price', 0.0)) if levels else 0.0

            if total_depth <= 0:
                logs.append(f"{task.task_id}: zero visible depth, SKIP")
                continue

            # Determine batch size
            # Endgame: spread remaining quantity across remaining ticks.
            max_order = int(MAX_ORDER_SIZE.get(task.ticker, task.batch_size))
            prof = self._profile_for_ticker(task.ticker)
            if endgame:
                ticks_left = max(1, MAX_TICK - current_tick)
                max_tick_capacity = max_order * max(1, ENDGAME_MAX_SLICES_PER_TICK)
                if ticks_left <= FINAL_FLATTEN_TICKS:
                    # Final sprint: prioritize flattening over smooth pacing.
                    batch = min(task.remaining, unwindable, max_tick_capacity)
                    logs.append(f"{task.task_id}: FINAL sprint send {batch:,} "
                                f"(left={task.remaining:,}, ticks_left={ticks_left})")
                else:
                    target_per_tick = max(1, (task.remaining + ticks_left - 1)
                                          // ticks_left)
                    batch = min(task.remaining, unwindable, target_per_tick,
                                max_tick_capacity)
                    logs.append(f"{task.task_id}: ENDGAME paced send {batch:,} "
                                f"(left={task.remaining:,}, ticks_left={ticks_left})")
            else:
                # Normal mode:
                # 1) Depth-aware cap
                # 2) Risk-aware pacing (inventory urgency * volatility multiplier)
                base = min(task.batch_size, task.remaining, unwindable, max_order)
                ticks_left = max(1, MAX_TICK - current_tick)
                normal_window_left = max(1, ticks_left - ENDGAME_UNWIND_TICKS)
                virtual_ticks_left = max(UNWIND_VIRTUAL_TICKS_FLOOR,
                                         normal_window_left)
                target_per_tick = max(1, (task.remaining + virtual_ticks_left - 1)
                                      // virtual_ticks_left)

                # Increase participation as we get closer to endgame.
                if (MAX_TICK - ENDGAME_UNWIND_TICKS) > 0:
                    urgency = 1.0 - min(
                        1.0,
                        normal_window_left / max(1, (MAX_TICK - ENDGAME_UNWIND_TICKS))
                    )
                else:
                    urgency = 1.0
                raw_participation = (
                    prof['base_participation']
                    + (prof['max_participation'] - prof['base_participation']) * urgency
                )
                participation = min(MAX_DEPTH_RATIO, max(0.01, raw_participation))
                depth_cap = max(1, int(total_depth * participation))

                inv_urgency = task.remaining / max(1, ticks_left)
                vol_level = self._vol_level(task.ticker)
                risk_pace = max(1, int(
                    inv_urgency * self._risk_mult(vol_level) * prof['risk_mult']))

                paced_target = max(target_per_tick, risk_pace)
                if task.remaining > UNWIND_MIN_ORDER_SIZE:
                    paced_target = max(paced_target, UNWIND_MIN_ORDER_SIZE)
                batch = min(base, depth_cap, paced_target)
                logs.append(
                    f"{task.task_id}: NORMAL paced send {batch:,} "
                    f"(part={participation:.1%}, depth={total_depth:,}, "
                    f"vol={vol_level}, virtual_ticks_left={virtual_ticks_left})")

                # Soft breakeven in normal mode.
                if task.breakeven_price > 0 and best_px > 0:
                    if (task.close_action == 'SELL'
                            and best_px < task.breakeven_price):
                        gap = task.breakeven_price - best_px
                        if inv_urgency < prof['soft_be_urgency'] or gap > prof['soft_be_slippage']:
                            logs.append(
                                f"{task.task_id}: bid {best_px:.2f} < BE "
                                f"{task.breakeven_price:.2f}, wait")
                            continue
                        batch = max(1, min(batch, int(batch * prof['soft_be_batch_fraction'])))
                        logs.append(
                            f"{task.task_id}: soft-BE SELL breach {gap:.3f}, "
                            f"reduced batch={batch:,}")
                    if (task.close_action == 'BUY'
                            and best_px > task.breakeven_price):
                        gap = best_px - task.breakeven_price
                        if inv_urgency < prof['soft_be_urgency'] or gap > prof['soft_be_slippage']:
                            logs.append(
                                f"{task.task_id}: ask {best_px:.2f} > BE "
                                f"{task.breakeven_price:.2f}, wait")
                            continue
                        batch = max(1, min(batch, int(batch * prof['soft_be_batch_fraction'])))
                        logs.append(
                            f"{task.task_id}: soft-BE BUY breach {gap:.3f}, "
                            f"reduced batch={batch:,}")

            if batch <= 0:
                task.status = 'COMPLETED'
                logs.append(f"{task.task_id}: nothing to unwind")
                continue

            # Send market order
            # Final guard: re-check position right before sending so we never
            # trade when position is already flat.
            latest_pos = get_position(self.session, task.ticker)
            if task.close_action == 'SELL' and latest_pos <= 0:
                task.status = 'COMPLETED'
                logs.append(f"{task.task_id}: pre-send pos={latest_pos}, "
                            f"skip SELL and close task")
                continue
            if task.close_action == 'BUY' and latest_pos >= 0:
                task.status = 'COMPLETED'
                logs.append(f"{task.task_id}: pre-send pos={latest_pos}, "
                            f"skip BUY and close task")
                continue

            batch = min(batch, abs(latest_pos))
            if batch <= 0:
                task.status = 'COMPLETED'
                logs.append(f"{task.task_id}: pre-send flat, nothing to unwind")
                continue

            max_slices = 1
            if endgame:
                max_slices = max(1, ENDGAME_MAX_SLICES_PER_TICK)

            limit_px = 0.0
            use_limit = (not endgame) and best_px > 0
            if use_limit:
                min_clip = int(prof['normal_limit_min_clip'])
                cap_for_floor = min(
                    task.remaining,
                    abs(latest_pos),
                    max_order,
                    int(total_depth * min(MAX_DEPTH_RATIO, max(0.01, prof['max_participation']))),
                )
                if min_clip > 0 and cap_for_floor >= min_clip and batch < min_clip:
                    batch = min_clip
            if use_limit:
                if task.close_action == 'SELL':
                    limit_px = round(max(0.01, best_px - prof['marketable_limit_eps']), 2)
                else:
                    limit_px = round(best_px + prof['marketable_limit_eps'], 2)

            remaining_send = batch
            sent_qty = 0
            slices_sent = 0
            while remaining_send > 0 and slices_sent < max_slices:
                clip = min(remaining_send, max_order)
                if use_limit:
                    ok = self.send_limit_order(
                        task.ticker, task.close_action, clip, limit_px)
                else:
                    ok = self.send_market_order(task.ticker, task.close_action, clip)
                if not ok:
                    break
                sent_qty += clip
                remaining_send -= clip
                slices_sent += 1

            if sent_qty > 0:
                task.quantity_sent += sent_qty
                task.last_send_tick = current_tick
                order_kind = "LIMIT" if use_limit else "MARKET"
                logs.append(f"{task.task_id}: {order_kind} {task.close_action} "
                            f"{sent_qty:,} "
                            f"(slices={slices_sent}) "
                            f"({task.quantity_sent:,}/{task.total_quantity:,})"
                            f" [pos={actual_pos}]")
            else:
                logs.append(f"{task.task_id}: order REJECTED, will retry")

            if task.remaining <= 0:
                task.status = 'COMPLETED'
                logs.append(f"{task.task_id}: COMPLETED")

        # Clean up completed tasks
        self.active_tasks = [t for t in self.active_tasks
                             if t.status == 'ACTIVE']
        return logs

    def _calc_batch_price(self, task: UnwindTask, book: dict,
                          endgame: bool) -> float:
        """Determine limit price for a TWAP batch.

        Normal: price at best level but no worse than breakeven.
        Endgame: cross the spread more aggressively to ensure fill.
        """
        if task.close_action == 'SELL':
            levels = book.get('bid', [])
            if not levels:
                return task.breakeven_price
            best = float(levels[0].get('price', 0.0))
            if best <= 0:
                return task.breakeven_price
            if endgame:
                # Sell aggressively — accept worse price to guarantee fill
                return round(best - TWAP_AGGRESSIVE_OFFSET, 2)
            return round(max(task.breakeven_price, best), 2)
        else:  # BUY
            levels = book.get('ask', [])
            if not levels:
                return task.breakeven_price
            best = float(levels[0].get('price', 0.0))
            if best <= 0:
                return task.breakeven_price
            if endgame:
                return round(best + TWAP_AGGRESSIVE_OFFSET, 2)
            return round(min(task.breakeven_price, best), 2)

    def get_active_tasks(self) -> List[UnwindTask]:
        return [t for t in self.active_tasks if t.status == 'ACTIVE']

    def cancel_all_tasks(self):
        """Emergency: cancel all pending TWAP tasks."""
        for t in self.active_tasks:
            t.status = 'COMPLETED'
        self.active_tasks.clear()

    # ---- Legacy instant plan (kept for reference) ----

    def build_unwind_limit_plan(
        self,
        ticker: str,
        tender_price: float,
        tender_action: str,
        quantity: int,
        order_book: dict,
        min_profit_per_share: float = MIN_PROFIT_PER_SHARE,
    ) -> Dict[str, Any]:
        """Non-speculative unwind plan (legacy — use create_unwind_task for TWAP)."""
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
