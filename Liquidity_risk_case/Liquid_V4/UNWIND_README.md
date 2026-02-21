# LT3 Tender + Unwind Logic (Current Behavior)

This document describes how the bot currently evaluates tender offers and unwinds positions in `LT3`.

## 1. Offer (Tender) Logic

The bot checks tenders each loop and only handles tickers in `WATCHLIST`.

- Already-processed tender IDs are skipped.
- Position limits are pre-checked before tender acceptance (`RiskManager.can_accept_tender`).
- Order book is fetched for evaluation; if empty, synthetic top-of-book can be used.

### 1.1 Private Tender (fixed bid)

Private tender evaluation is done by `LiquidityAnalyzer.evaluate_tender_offer(...)`.

It evaluates:

- Unwind direction (`SELL` for tender `BUY`, `BUY` for tender `SELL`)
- Visible depth and depth ratio
- Impact-adjusted expected net profit
- Confidence score

Take rule used in `main.py`:

- `should_take = (decision == "ACCEPT") and (confidence >= MIN_TENDER_CONFIDENCE)`
- If `should_take` is true: tender is accepted.
- If `decision` is `ACCEPT` but confidence is below threshold: tender is skipped.

### 1.2 Competitive Auction Tender

Auction evaluation is done by `LiquidityAnalyzer.calculate_auction_bid_price(...)`.

It computes whether a profitable bid exists under liquidity constraints and returns:

- `decision`: `BID` or `REJECT`
- `bid_price`
- `breakeven_price`

If `decision == BID`, the bot submits the bid price.

### 1.3 Offer-to-Unwind Handoff

After a private tender is accepted or an auction bid is successfully submitted:

- Bot reads live position with `get_position(...)`.
- If position is non-zero, creates unwind task:
  - long position -> `SELL` task
  - short position -> `BUY` task
- If position is zero, no unwind task is created.

## 2. How Unwind Tasks Are Created

Unwind tasks are created in two paths from `LT3/main.py`:

- After a tender is accepted/bid-filled.
- Residual auto-unwind (all ticks):
  - For each ticker in `WATCHLIST`, if there is no active task and there is non-zero position (snapshot + live check), a new unwind task is created.

## 3. Core Safety Rules (No Speculation)

Inside `ExecutionEngine.tick_tasks(...)`:

- A task only trades when status is `ACTIVE`.
- It fetches real position before trading.
- Direction must match exposure:
  - `SELL` only if position `> 0`.
  - `BUY` only if position `< 0`.
- It fetches live order book each cycle.
- If book fetch fails or visible depth is zero, it skips the cycle.
- Right before sending, it fetches position again and clamps quantity to avoid crossing past flat.

Result: if there is no position, it does not trade.

## 4. Normal-Window Unwind Logic (Non-Endgame)

A batch size is computed with multiple caps:

- `base = min(task.batch_size, task.remaining, unwindable, max_order)`
- Time pacing:
  - `normal_window_left = max(1, ticks_left - ENDGAME_UNWIND_TICKS)`
  - `virtual_ticks_left = max(UNWIND_VIRTUAL_TICKS_FLOOR, normal_window_left)`
  - `target_per_tick = ceil(task.remaining / virtual_ticks_left)`
- Risk-aware pacing:
  - estimate short-term volatility from recent mid-price history
  - classify as `LOW / MED / HIGH`
  - apply risk multiplier (`UNWIND_RISK_MULT_LOW/MED/HIGH`) on inventory urgency
  - combine with baseline pace target
- Depth participation:
  - participation increases with urgency from `UNWIND_BASE_PARTICIPATION` to `UNWIND_MAX_PARTICIPATION`
  - then capped by `MAX_DEPTH_RATIO`
  - `depth_cap = int(total_depth * participation)`
- Minimum pace for large remaining size:
  - if remaining > `UNWIND_MIN_ORDER_SIZE`, enforce at least `UNWIND_MIN_ORDER_SIZE` pace target
- Final normal batch:
  - `batch = min(base, depth_cap, paced_target)`

Soft breakeven in normal mode:

- If `breakeven_price > 0`, the bot waits unless price is acceptable:
  - `SELL`: best bid should be `>= breakeven`
  - `BUY`: best ask should be `<= breakeven`
- If urgency is high and breach is small, it allows a reduced-size breach:
  - controlled by `SOFT_BREAKEVEN_URGENCY`
  - max breach controlled by `SOFT_BREAKEVEN_SLIPPAGE`
  - reduced size controlled by `SOFT_BREAKEVEN_BATCH_FRACTION`

## 5. Endgame Logic (Last Ticks)

Endgame is active when remaining ticks are within `ENDGAME_UNWIND_TICKS`.

- Standard endgame pacing:
  - `target_per_tick = ceil(task.remaining / ticks_left)`
  - capped by per-tick capacity
- Final sprint (`ticks_left <= FINAL_FLATTEN_TICKS`):
  - prioritize flattening over smooth pacing
  - use max per-tick capacity directly

Per-tick capacity in endgame:

- `max_tick_capacity = MAX_ORDER_SIZE[ticker] * ENDGAME_MAX_SLICES_PER_TICK`

## 6. Order Submission Behavior

- In normal mode, bot uses pseudo-marketable LIMIT orders:
  - `SELL` uses `best_bid - MARKETABLE_LIMIT_EPS`
  - `BUY` uses `best_ask + MARKETABLE_LIMIT_EPS`
- Endgame uses MARKET orders for certainty.
- In normal mode, max one slice per tick.
- In endgame, up to `ENDGAME_MAX_SLICES_PER_TICK` slices per tick.
- Each slice is capped by `MAX_ORDER_SIZE[ticker]`.
- If API returns `429`, it waits and retries once.

## 7. Key Config Parameters

From `LT3/config.py`:

- `MIN_TENDER_CONFIDENCE`
- `MAX_DEPTH_RATIO`
- `TWAP_BATCH_SIZE`
- `TWAP_TICK_INTERVAL`
- `MAX_ORDER_SIZE`
- `UNWIND_BASE_PARTICIPATION`
- `UNWIND_MAX_PARTICIPATION`
- `UNWIND_MIN_ORDER_SIZE`
- `UNWIND_VIRTUAL_TICKS_FLOOR`
- `UNWIND_VOL_LOOKBACK`
- `UNWIND_VOL_LOW`
- `UNWIND_VOL_HIGH`
- `UNWIND_RISK_MULT_LOW`
- `UNWIND_RISK_MULT_MED`
- `UNWIND_RISK_MULT_HIGH`
- `SOFT_BREAKEVEN_SLIPPAGE`
- `SOFT_BREAKEVEN_URGENCY`
- `SOFT_BREAKEVEN_BATCH_FRACTION`
- `MARKETABLE_LIMIT_EPS`
- `ENDGAME_UNWIND_TICKS`
- `FINAL_FLATTEN_TICKS`
- `ENDGAME_MAX_SLICES_PER_TICK`

## 8. Logging / Monitoring

- Terminal is tender-focused; only key unwind events are printed.
- Per-tick performance metrics are written to `LT3/performance_log.csv`.

## 9. Sub-Heat 1 Ticker Differentiation (Implemented)

Ticker-specific execution profiles are configured in `UNWIND_TICKER_PROFILE`:

- `COMP` (higher liquidity, medium vol):
  - faster risk pace (`risk_mult` higher)
  - higher participation
  - softer breakeven (higher tolerance and batch fraction)
  - wider pseudo-marketable limit offset
- `RITC` (medium liquidity, lower vol):
  - slower risk pace (`risk_mult` lower)
  - lower participation
  - stricter breakeven
  - tighter pseudo-marketable limit offset

This lets the same engine execute different liquidation styles per ticker without changing the core safety constraints.
