# Parameters Explanation

## 📋 Table of Contents
1. [Tender Acceptance Parameters](#tender-acceptance-parameters)
2. [Almgren-Chriss Parameters](#almgren-chriss-parameters)
3. [Execution Profiles](#execution-profiles)

---

## 🎯 Tender Acceptance Parameters

These parameters control **when and how aggressively** the bot accepts tender offers.

### `MIN_PROFIT_PER_SHARE = 0.003` ($0.003/share)
- **What it does**: Minimum net profit per share required to accept a private tender
- **How it works**: 
  - Bot calculates: `net_profit = gross_profit - commission`
  - If `net_profit / quantity >= 0.003`, tender is considered profitable
  - Used in confidence calculation (line 183 in main.py)
- **Current setting**: Very aggressive (accepts almost any profitable tender)
- **Tuning**: 
  - Increase (e.g., 0.01) = more conservative, only take high-profit tenders
  - Decrease (e.g., 0.001) = more aggressive, take marginal tenders

### `MAX_DEPTH_RATIO = 0.50` (50%)
- **What it does**: Maximum ratio of tender quantity to order book depth
- **How it works**:
  - `depth_ratio = tender_quantity / visible_book_depth`
  - If `depth_ratio > 0.50`, tender is rejected (too large relative to liquidity)
  - Also used in confidence calculation (lower ratio = higher confidence)
- **Example**: 
  - Tender: 10,000 shares
  - Book depth: 20,000 shares
  - Ratio: 0.50 (exactly at limit)
- **Tuning**:
  - Increase (e.g., 0.70) = accept larger tenders relative to book
  - Decrease (e.g., 0.30) = only accept small tenders (safer)

### `MIN_CONFIDENCE = 0.05` (5%)
- **What it does**: Minimum confidence threshold to accept a private tender
- **How it works**:
  - Confidence formula (line 181-183):
    ```python
    confidence = min(0.95, max(0.1,
        net_pps / price * 200 * (1 - min(0.9, depth_ratio))
    ))
    ```
  - Higher profit + lower depth ratio = higher confidence
  - Only accepts if `confidence >= 0.05`
- **Current setting**: Very low threshold (accepts almost anything)
- **Tuning**:
  - Increase (e.g., 0.30) = only accept high-confidence tenders
  - Decrease (e.g., 0.01) = accept even marginal tenders

### `AUCTION_AGGRESSION = 0.005` ($0.005/share)
- **What it does**: Bid margin for competitive/winner-take-all auctions
- **How it works**:
  - For BUY tenders: `bid_price = breakeven - 0.005` (bid $0.005 below breakeven)
  - For SELL tenders: `bid_price = breakeven + 0.005` (bid $0.005 above breakeven)
  - This tight margin helps win auctions while maintaining profitability
- **Example**:
  - Breakeven: $50.00
  - Bid: $49.995 (rounded to $50.00) for BUY tender
  - Expected profit: $0.005/share if you win
- **Tuning**:
  - Increase (e.g., 0.01) = more aggressive bids, higher win rate but lower profit
  - Decrease (e.g., 0.002) = more conservative bids, lower win rate but higher profit if you win

---

## 📊 Almgren-Chriss Parameters

These parameters control **how positions are unwound** using optimal execution theory.

### `AC_GRADIENT_LOW_VOL = 0.3`
### `AC_GRADIENT_MED_VOL = 0.5`
### `AC_GRADIENT_HIGH_VOL = 0.7`

- **What it does**: Risk aversion gradient on the efficient frontier
- **How it works**:
  - Almgren-Chriss optimizes: `E[Cost] + λ * Var[Cost]`
  - Gradient = `dE/d√V` (expected cost vs. risk tradeoff)
  - Higher gradient = more risk-averse = more front-loaded execution
- **Volatility mapping**:
  - LOW volatility → 0.3 (less front-loading, more gradual)
  - MEDIUM volatility → 0.5 (balanced)
  - HIGH volatility → 0.7 (more front-loading, execute faster)
- **Why it matters**: 
  - High volatility = prices move more → execute faster to reduce exposure
  - Low volatility = prices stable → can execute gradually to reduce impact
- **Tuning**:
  - Increase = more aggressive front-loading (execute faster)
  - Decrease = more gradual execution (lower market impact)

### `AC_TAU = 1` (1 tick)
- **What it does**: Time step for Almgren-Chriss schedule
- **How it works**: 
  - Schedule is generated with trades every `tau` ticks
  - `tau = 1` means trade every tick (most granular)
- **Example**: 
  - 100 shares over 10 ticks with `tau=1` → 10 trades of ~10 shares each
  - 100 shares over 10 ticks with `tau=2` → 5 trades of ~20 shares each
- **Tuning**: Usually keep at 1 for maximum flexibility

### `AC_MIN_HORIZON = 5` (5 ticks)
- **What it does**: Minimum time horizon for Almgren-Chriss optimization
- **How it works**:
  - If `ticks_remaining < 5`, bot uses immediate execution instead of AC schedule
  - Prevents over-optimization for very short timeframes
- **Example**:
  - 3 ticks left → immediate market order (too short for AC)
  - 10 ticks left → AC schedule generated
- **Tuning**: 
  - Increase = require more time before using AC (more conservative)
  - Decrease = use AC for shorter timeframes (more aggressive)

### `AC_FALLBACK_TWAP = True`
- **What it does**: Fallback to Time-Weighted Average Price if AC calculation fails
- **How it works**:
  - If AC optimization throws an error, use uniform TWAP schedule
  - TWAP = divide quantity evenly across remaining ticks
- **Why it matters**: Safety net if AC math fails (numerical issues, edge cases)
- **Tuning**: Keep `True` for robustness

---

## ⚙️ Execution Profiles

These profiles control **how limit orders are placed** during unwinding.

### Structure: `(volatility, liquidity) → {participation, limit_eps, be_slack}`

### `participation` (0.12 - 0.30)
- **What it does**: Maximum fraction of visible book depth to trade per order
- **How it works**:
  - `max_order_size = visible_depth * participation`
  - Prevents taking too much liquidity at once
- **Examples**:
  - `participation = 0.25`, depth = 10,000 → max 2,500 shares per order
  - `participation = 0.12`, depth = 10,000 → max 1,200 shares per order
- **Pattern**: Higher volatility/liquidity → higher participation (can trade more)

### `limit_eps` ($0.003 - $0.02)
- **What it does**: Price offset for limit orders from best bid/ask
- **How it works**:
  - SELL orders: `limit_price = best_bid - limit_eps`
  - BUY orders: `limit_price = best_ask + limit_eps`
  - Makes orders "marketable" (likely to fill) but not market orders
- **Examples**:
  - Best bid: $50.00, `limit_eps = 0.01` → SELL limit at $49.99
  - Best ask: $50.10, `limit_eps = 0.01` → BUY limit at $50.11
- **Pattern**: Higher volatility → larger `limit_eps` (more aggressive pricing)

### `be_slack` ($0.005 - $0.03)
- **What it does**: Breakeven price tolerance before refusing to trade
- **How it works**:
  - If `best_price` is worse than `breakeven_price` by more than `be_slack`, skip trading
  - Only applies when urgency is low (plenty of time remaining)
- **Example**:
  - Breakeven: $50.00
  - Best bid: $49.95
  - Gap: $0.05
  - If `be_slack = 0.02` and urgency < 5 → skip (wait for better price)
  - If `be_slack = 0.02` and urgency >= 5 → trade anyway (time pressure)
- **Pattern**: Higher volatility → larger `be_slack` (more tolerance for bad prices)

---

## 📈 How They Work Together

### Tender Acceptance Flow:
1. **Evaluate tender** → Calculate `net_pps`, `depth_ratio`, `confidence`
2. **Check thresholds** → `net_pps >= MIN_PROFIT_PER_SHARE`, `depth_ratio <= MAX_DEPTH_RATIO`
3. **Check confidence** → `confidence >= MIN_CONFIDENCE`
4. **Accept or reject** → If all pass, accept tender and create unwind task

### Unwinding Flow:
1. **Create AC schedule** → Based on volatility, quantity, time remaining
2. **Select execution profile** → Based on volatility + liquidity classification
3. **Execute per tick** → Use `participation`, `limit_eps`, `be_slack` to place orders
4. **Monitor and adjust** → Recalculate schedule if needed

### Example Scenario:
- **Tender**: BUY 10,000 *REMOVED* @ $50.00
- **Book depth**: 20,000 shares (ratio = 0.50, passes `MAX_DEPTH_RATIO`)
- **Net profit**: $0.005/share (passes `MIN_PROFIT_PER_SHARE`)
- **Confidence**: 0.15 (passes `MIN_CONFIDENCE`)
- **Accept tender** → Create unwind task
- **Volatility**: LOW → Use `AC_GRADIENT_LOW_VOL = 0.3` (gradual execution)
- **Liquidity**: MEDIUM → Use profile `(LOW, MEDIUM)`:
  - `participation = 0.18` → Max 3,600 shares per order
  - `limit_eps = 0.005` → Limit orders $0.005 from best price
  - `be_slack = 0.01` → Wait if price $0.01+ worse than breakeven

---

## 🎛️ Quick Tuning Guide

### More Aggressive Tender Acceptance:
- Decrease `MIN_PROFIT_PER_SHARE` (e.g., 0.001)
- Increase `MAX_DEPTH_RATIO` (e.g., 0.70)
- Decrease `MIN_CONFIDENCE` (e.g., 0.01)
- Increase `AUCTION_AGGRESSION` (e.g., 0.01)

### More Conservative Tender Acceptance:
- Increase `MIN_PROFIT_PER_SHARE` (e.g., 0.01)
- Decrease `MAX_DEPTH_RATIO` (e.g., 0.30)
- Increase `MIN_CONFIDENCE` (e.g., 0.30)
- Decrease `AUCTION_AGGRESSION` (e.g., 0.002)

### Faster Unwinding:
- Increase `AC_GRADIENT_*` values (e.g., 0.5 → 0.8)
- Increase `participation` in execution profiles
- Increase `limit_eps` (more aggressive pricing)

### Slower, More Careful Unwinding:
- Decrease `AC_GRADIENT_*` values (e.g., 0.5 → 0.3)
- Decrease `participation` in execution profiles
- Decrease `limit_eps` (more patient pricing)
