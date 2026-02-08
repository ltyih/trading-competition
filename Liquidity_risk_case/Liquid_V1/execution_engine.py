# -*- coding: utf-8 -*-
"""LT3 Tender Liquidity Bot — Execution engine.

TWAP-based unwind execution with batch ordering across ticks.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any
from time import sleep

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
    MAX_TICK,
    ENDGAME_UNWIND_TICKS,
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

    @staticmethod
    def _available_qty(level: Dict[str, Any]) -> int:
        q = int(level.get('quantity', 0))
        qf = int(level.get('quantity_filled', 0))
        return max(0, q - qf)

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
        print(f"  [TWAP] Created task {task.task_id}: "
              f"{close_action} {total_quantity:,} {ticker} "
              f"(batch={batch_size}, interval={tick_interval} ticks)")
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

            levels = book.get('bid' if task.close_action == 'SELL' else 'ask', [])
            total_depth = sum(self._available_qty(lvl) for lvl in levels)
            best_px = float(levels[0].get('price', 0.0)) if levels else 0.0

            if total_depth <= 0:
                logs.append(f"{task.task_id}: zero visible depth, SKIP")
                continue

            # Determine batch size
            # Endgame: spread remaining quantity across remaining ticks.
            max_order = int(MAX_ORDER_SIZE.get(task.ticker, task.batch_size))
            if endgame:
                ticks_left = max(1, MAX_TICK - current_tick)
                target_per_tick = max(1, (task.remaining + ticks_left - 1)
                                      // ticks_left)
                batch = min(task.remaining, unwindable, target_per_tick,
                            max_order)
                logs.append(f"{task.task_id}: ENDGAME paced send {batch:,} "
                            f"(left={task.remaining:,}, ticks_left={ticks_left})")
            else:
                # Normal mode:
                # 1) cap by visible depth participation
                # 2) pace by time left before endgame window
                base = min(task.batch_size, task.remaining, unwindable, max_order)
                ticks_left = max(1, MAX_TICK - current_tick)
                horizon = max(1, ticks_left - ENDGAME_UNWIND_TICKS)

                lifetime = max(1, MAX_TICK - task.created_tick)
                elapsed = max(0, current_tick - task.created_tick)
                urgency = min(1.0, max(0.0, elapsed / lifetime))
                participation = (
                    UNWIND_BASE_PARTICIPATION
                    + (UNWIND_MAX_PARTICIPATION - UNWIND_BASE_PARTICIPATION) * urgency
                )
                participation = min(MAX_DEPTH_RATIO, max(0.01, participation))

                depth_cap = max(1, int(total_depth * participation))
                pace_cap = max(1, (task.remaining + horizon - 1) // horizon)
                if task.remaining > UNWIND_MIN_ORDER_SIZE:
                    pace_cap = max(UNWIND_MIN_ORDER_SIZE, pace_cap)

                batch = min(base, depth_cap, pace_cap)
                logs.append(
                    f"{task.task_id}: NORMAL paced send {batch:,} "
                    f"(part={participation:.1%}, depth={total_depth:,}, "
                    f"horizon={horizon})")

                # Respect breakeven in normal mode if provided.
                if task.breakeven_price > 0 and best_px > 0:
                    if (task.close_action == 'SELL'
                            and best_px < task.breakeven_price):
                        logs.append(
                            f"{task.task_id}: bid {best_px:.2f} < BE "
                            f"{task.breakeven_price:.2f}, wait")
                        continue
                    if (task.close_action == 'BUY'
                            and best_px > task.breakeven_price):
                        logs.append(
                            f"{task.task_id}: ask {best_px:.2f} > BE "
                            f"{task.breakeven_price:.2f}, wait")
                        continue

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

            ok = self.send_market_order(
                task.ticker, task.close_action, batch)

            if ok:
                task.quantity_sent += batch
                task.last_send_tick = current_tick
                logs.append(f"{task.task_id}: MARKET {task.close_action} "
                            f"{batch:,} "
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
