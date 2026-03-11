# Algorithmic Market Making Strategy
## Master Strategy Document for Claude Code

---

## 1. CASE FUNDAMENTALS AT A GLANCE

- **4 stocks**: SPNG, SMMR, ATMN, WNTR — all start at $25 CAD
- **12 heats**, 5 minutes (300 seconds) each = one trading week (5 days, 60s per day)
- **1 team member** trades all heats via algorithm only (no manual trading)
- **2 minutes** between heats to modify code
- **Max order size**: 10,000 shares per order per stock
- **Market order fee**: $0.02/share (all stocks)
- **Limit order rebates** (when your passive order is filled):
  - SPNG: $0.01/share
  - SMMR: $0.02/share
  - ATMN: $0.015/share
  - WNTR: $0.025/share
- **Aggregate position limit** (announced at heat start): sum of |position| across all 4 stocks, checked at each market close (every 60s). Penalty: **$10/share** over the limit.
- **Gross & net position limits** (intraday): penalty $5/share for exceeding.
- **Position close-out**: non-zero positions closed at last traded price at end of heat.
- **News**: released after each market close (every minute), drives price movements. News is NOT provided directly — you must infer from price action.
- **Scoring**: P&L ranked across 12 heats → average rank → final case rank. Rewards consistency.

---

## 2. THE CORE ECONOMIC INSIGHT

### Why This Case Exists
You are a market maker. The market needs liquidity providers who post bids and asks. You earn the spread. The risk is that news causes sudden price jumps and your stale quotes get picked off (adverse selection). The entire game is: **earn more from spreads and rebates than you lose from adverse selection and penalties.**

### The Fee/Rebate Asymmetry Is Your Edge
This is the single most important structural feature of this case. When you post limit orders and they get filled, you **receive** a rebate. When someone else hits your quote, you are providing liquidity and getting paid for it. When you take liquidity with a market order, you **pay** $0.02/share.

The rebate schedule creates a clear preference ordering for which stocks to prioritize providing liquidity in:

| Stock | Rebate | Fee | Net Cost of Round-Trip (Limit fill + Market close) | Pure Spread (Limit both sides) |
|-------|--------|-----|-----------------------------------------------|-------------------------------|
| WNTR  | $0.025 | $0.02 | You EARN $0.005 net on passive fills | Earn $0.045/share round trip |
| SMMR  | $0.02  | $0.02 | Breakeven on passive fills | Earn $0.04/share round trip |
| ATMN  | $0.015 | $0.02 | Costs $0.005 net if you market-order out | Earn $0.035/share round trip |
| SPNG  | $0.01  | $0.02 | Costs $0.01 net if you market-order out | Earn $0.03/share round trip |

**WNTR is the king stock.** Every limit order fill on WNTR earns $0.025/share. If you can get both sides filled at a 1-cent spread, you earn $0.025 + $0.025 + $0.01 spread = $0.06/share. On 10,000 shares, that's $600 per round trip. Do that a few times per minute across 4 stocks and it adds up fast.

---

## 3. STRATEGY ARCHITECTURE — THREE LAYERS

### Layer 1: Passive Market Making (The Bread and Butter — 70% of P&L)
### Layer 2: Inventory Management & Risk Control (Survival — Prevents Catastrophe)
### Layer 3: Adaptive Spread & News Reaction (Alpha — 30% of P&L)

---

## 4. LAYER 1: PASSIVE MARKET MAKING

### 4.1 The Quote Engine

For each of the 4 stocks, continuously maintain a **bid and ask** in the order book.

**Core loop (every cycle, ~100-250ms depending on rate limits):**
1. Fetch current order book for all 4 tickers (GET /securities/book)
2. Fetch current positions (GET /securities)
3. Compute mid-price for each stock
4. Compute target bid/ask based on mid + spread + inventory skew
5. Cancel stale orders that are away from target
6. Place new orders at target prices

### 4.2 Spread Calculation

The spread you quote determines your profitability vs. fill rate. Too tight = adverse selection kills you. Too wide = no fills = no profit.

**Base spread by stock (in cents):**
- WNTR: 2-4 cents (tighter because rebate is fattest — you can afford more adverse selection)
- SMMR: 3-5 cents
- ATMN: 3-5 cents  
- SPNG: 4-6 cents (widest because rebate is thinnest)

These are starting points. Dynamically adjust based on:
- **Realized volatility**: If price moved >$0.10 in the last 10 seconds, widen spread by 2-4 cents
- **Time within day**: Widen spread in the last 5-10 seconds before market close (news risk)
- **Inventory level**: Skew quotes (see Layer 2)
- **Order book depth**: If book is thin (few orders near mid), you can quote wider and still get filled

### 4.3 Order Sizing

**DO NOT quote max size (10,000).** This is the #1 mistake beginners make. If news causes a $0.50 move and you have 10,000 shares filled on the wrong side, that's a $5,000 loss on one fill.

**Recommended order sizes:**
- Normal conditions: 1,000-3,000 shares per side
- High volatility / near market close: 500-1,000 shares per side
- When approaching aggregate position limit: 500 shares or less on the side that increases exposure
- When near flat (low inventory): can go up to 5,000 per side

### 4.4 Rebate Farming Priority

Because scoring rewards consistency, not home runs, your goal is to **maximize the number of filled limit orders**, especially on high-rebate stocks.

**Priority of quoting capital allocation:**
1. **WNTR** — Always have quotes live. $0.025 rebate means even getting adversely selected costs less. Quote tighter, quote more.
2. **SMMR** — Always have quotes live. $0.02 rebate = breakeven vs fees.
3. **ATMN** — Quote with moderate size. $0.015 rebate is decent.
4. **SPNG** — Quote with smaller size. $0.01 rebate barely helps.

If you must reduce quoting (near limits, volatile period), pull SPNG quotes first, then ATMN.

---

## 5. LAYER 2: INVENTORY MANAGEMENT & RISK CONTROL

This layer prevents the penalties that destroy your P&L. The $10/share aggregate penalty at market close is **devastating**. A 5,000 share overshoot = $50,000 penalty. That wipes out hours of market making profit.

### 5.1 Aggregate Position Limit Management

The aggregate limit = |pos_SPNG| + |pos_SMMR| + |pos_ATMN| + |pos_WNTR|

This is checked at each market close (every 60 seconds: ticks 60, 120, 180, 240, 300).

**Strategy: Operate at 50-70% of the limit during normal trading. Begin aggressively flattening at 80%.**

Define utilization = current_aggregate / aggregate_limit:

| Utilization | Action |
|-------------|--------|
| 0-50% | Full quoting, normal spreads, normal sizes |
| 50-70% | Normal quoting, begin skewing quotes toward reducing largest positions |
| 70-85% | Reduce quote sizes. Skew aggressively. Only quote the side that reduces position on stocks with large positions. |
| 85-95% | Emergency mode. Cancel all orders that could increase aggregate. Use market orders to flatten largest positions. |
| 95%+ | PANIC FLATTEN. Market-order everything toward zero. The $10/share penalty costs more than any spread loss. |

### 5.2 Quote Skewing (The Avellaneda-Stoikov Intuition)

When you hold inventory, shift your quotes to incentivize fills that reduce your position:

**If you are LONG N shares in stock X:**
- Move your ask price LOWER (more competitive → more likely to sell → reduces long)
- Move your bid price LOWER (less competitive → less likely to buy more)
- Skew magnitude: proportional to position size as fraction of limit

**If you are SHORT N shares in stock X:**
- Move your bid price HIGHER (more competitive → more likely to buy → reduces short)
- Move your ask price HIGHER (less competitive → less likely to sell more)

**Skew formula:**
```
skew_cents = -position * skew_factor
bid_price = mid_price - half_spread + skew_cents
ask_price = mid_price + half_spread + skew_cents
```
Where `skew_factor` ≈ 0.001 to 0.005 (tune this). With position = 5000 and skew_factor = 0.002, skew = -10 cents. Your bid is 10 cents lower, ask is 10 cents lower → much more likely to sell than buy.

### 5.3 Cross-Stock Hedging

If you are long 5000 SPNG and need to reduce aggregate, you don't have to sell SPNG into a bad market. You can also flatten any other stock. The aggregate limit counts absolute positions, so reducing ANY stock's absolute position helps.

**Priority for flattening:** Flatten whichever stock has the widest market / most inventory first. But also consider: flatten the stock with the worst rebate first (SPNG), because the cost of market-ordering out is highest there.

### 5.4 Market Close Protocol (CRITICAL)

At approximately **tick % 60 ≈ 50-55** (5-10 seconds before each market close):

1. **Cancel ALL open orders** — you do not want to get filled right before close and accidentally blow your limit
2. **Calculate projected aggregate position** if all partial fills complete
3. **If above 90% of limit**: use market orders to flatten immediately, prioritizing the largest absolute position
4. **Do NOT re-enter orders until tick % 60 > 5** (5 seconds into new day)

This "quiet period" around the close is essential. The news comes right after close and causes jumps. You don't want stale quotes sitting in the book.

### 5.5 Gross and Net Limit Management

These are intraday limits with $5/share penalty. Monitor via `GET /limits`:
- `gross_limit`: sum of |long| + |short| across all securities
- `net_limit`: sum of (long + short) across all securities (shorts cancel longs)

Keep a buffer. If gross utilization > 80%, reduce order sizes.

---

## 6. LAYER 3: ADAPTIVE SPREAD & NEWS REACTION

### 6.1 Volatility Regime Detection

Track rolling price changes over the last 5-15 seconds for each stock. Compute realized volatility.

**Three regimes:**
1. **Low vol** (price changes < $0.03 per second): Tighten spreads, increase order sizes. This is harvesting time.
2. **Medium vol** ($0.03-$0.10 per second): Normal spreads. Standard operation.
3. **High vol** (> $0.10 per second): Widen spreads 2-3x. Reduce sizes to 500-1000. Or pull quotes entirely for a few seconds.

### 6.2 News Inference from Price Action

You don't see the news directly. But the price jumps after market close tell you everything. After each close:

1. Record pre-close prices for all 4 stocks
2. Wait 2-3 seconds for new prices to stabilize
3. Compare: compute the return for each stock
4. Use these returns to understand correlations:
   - If all 4 stocks move the same direction → macro news (systematic)
   - If only 1-2 move → idiosyncratic news
   - Track correlation patterns across days — they may shift

### 6.3 Post-News Momentum / Mean-Reversion

After a price jump:
- If jump is large (> $0.20), there may be **continuation** as other participants react
- If jump is small (< $0.10), price often **mean-reverts** as the initial reaction overshoots

**Strategy:** After a large jump, bias your quotes in the direction of the jump for 5-10 seconds (momentum). After a small jump, provide liquidity against the jump (mean reversion). This is hard to get right — if unsure, just widen spreads and wait.

### 6.4 Cross-Stock Signal Exploitation

If you observe that SPNG jumps $0.30 on news but WNTR hasn't moved yet (maybe WNTR is less liquid), you can:
- Quickly lift asks in WNTR if you believe it will follow SPNG up
- Quickly hit bids in WNTR if you believe it will follow SPNG down

This requires tracking lead-lag relationships between the 4 stocks. Build a simple model: for each pair, what's the lagged correlation of returns? If SPNG leads WNTR by 1-2 seconds, there's a stat-arb opportunity.

---

## 7. IMPLEMENTATION PRIORITIES

### Must Have (Ship in v1 — before first heat)
1. Basic quote engine: post bid/ask on all 4 stocks at mid ± spread
2. Position tracking: poll /securities every cycle
3. Aggregate limit monitoring with cancel-all near close
4. Simple inventory skew
5. Bulk cancel + re-quote logic (cancel-and-replace pattern)
6. Rate limit handling (respect 429 / Retry-After headers)

### Should Have (v2 — after first 1-2 heats)
7. Dynamic spread based on recent volatility
8. Adaptive order sizing based on utilization %
9. Pre-close flatten protocol
10. Separate handling of each stock's rebate in spread calculation

### Nice to Have (v3 — later heats)
11. Cross-stock correlation tracking
12. Post-news momentum/mean-reversion
13. Lead-lag exploitation
14. Adaptive parameters based on heat-to-heat learning

---

## 8. ORDER MANAGEMENT DETAILS

### 8.1 The Cancel-Replace Pattern

You cannot modify orders in RIT. You must cancel and re-place. This means:

1. Track all open order IDs
2. Each cycle: cancel stale orders → wait for confirmation → place new orders
3. Use `POST /commands/cancel?ticker=SPNG` to bulk cancel per ticker
4. Immediately re-submit new limit orders

**Critical timing issue**: Between canceling old orders and placing new ones, you have no quotes in the market. Minimize this window. Consider staggering: cancel+replace one stock at a time rather than all 4 simultaneously.

### 8.2 Avoiding Self-Crossing

If your bid is above your ask (due to a race condition or stale data), you'll trade with yourself and burn fees. Always validate: `bid_price < ask_price` before submitting.

### 8.3 Rate Limit Strategy

The API is rate-limited. Each order submission consumes rate limit budget. With 4 stocks × 2 sides = 8 orders per cycle minimum, plus cancels:

- Use bulk cancel (`POST /commands/cancel?all=1`) instead of individual cancels when possible
- Batch your data reads: one call to `GET /securities` gives all 4 stocks
- If you get 429, back off for the Retry-After duration — don't busy-loop
- Consider using async/concurrent requests to minimize wall-clock time per cycle

### 8.4 Order Type: ALWAYS Use LIMIT Orders

Never use market orders for your regular quoting. Market orders:
- Cost $0.02/share in fees
- Give you no rebate
- Execute at whatever price is available (slippage risk)

Only use market orders for:
- Emergency flattening near market close
- Panic liquidation when about to breach aggregate limit

---

## 9. P&L MATH — HOW TO MAKE $1M+

### Revenue Streams

**Stream 1: Spread Capture**
- Average spread earned: ~$0.03/share (conservative)
- Volume per stock per minute: ~5,000 shares (both sides)
- 4 stocks × 5,000 shares × $0.03 = $600/minute
- 5 minutes/heat × $600/min = $3,000/heat from spreads

**Stream 2: Rebates**
- Passive fills: ~20,000 shares/minute across all stocks
- Average rebate: ~$0.018/share (weighted)
- $0.018 × 20,000 = $360/minute
- 5 minutes = $1,800/heat from rebates

**Stream 3: Transaction Costs Saved**
- By using limit orders exclusively (except emergency), you avoid paying $0.02/share fees
- Emergency market orders maybe 5,000 shares/heat × $0.02 = $100/heat cost

**Estimated P&L per heat: ~$4,500-5,000 if things go well**
**Over 12 heats: ~$54,000-60,000 baseline**

To get to $1M, you need to capture much more volume:
- **Scale up**: closer to 50,000-100,000 shares per minute across stocks
- **Tighter spreads with high fill rates**: if you can quote at 1-2 cents and get filled constantly, with WNTR rebate at $0.025, you're earning $0.035-0.045 per share
- 100,000 shares/min × $0.04/share × 5 min = $20,000/heat
- 12 heats × $20,000 = $240,000

Getting to $1M requires also capturing directional moves and trading much larger size when confidence is high, while avoiding any penalties. The volume in the market depends on how many bot traders and other participants are present.

### Cost Avoidance

**The real differentiator is NOT losing money:**
- One aggregate limit breach of 10,000 shares = -$100,000. That wipes 2-5 heats of profit.
- One bad directional bet of 10,000 shares × $0.50 adverse move = -$5,000
- Transaction costs on unnecessary market orders: easily -$1,000/heat if sloppy

---

## 10. MINUTE-BY-MINUTE GAME PLAN

### Second 0-5 (Day Open)
- Resume quoting with wide spreads (last day's close may have gapped)
- Observe opening prices, set new mid-prices
- Small order sizes (1,000 shares)

### Second 5-50 (Core Trading)
- Full quoting with normal spreads
- Monitor inventory, skew as needed
- Track volatility regime
- Normal order sizes (2,000-5,000 shares)

### Second 50-55 (Pre-Close Warning)
- Begin tightening: reduce order sizes to 500-1,000
- Start flattening if aggregate utilization > 70%
- Widen spreads slightly

### Second 55-60 (Pre-Close Lockdown)
- **Cancel ALL open orders at second 55**
- Use market orders if necessary to get below aggregate limit
- DO NOT place new orders
- Wait for close + news

### Second 60-65 (Post-Close / New Day Open)
- Observe new prices (news has just been released)
- Compute price changes across all stocks
- Set new baselines
- Resume quoting at second 63-65 with wide spreads

---

## 11. PARAMETER TUNING BETWEEN HEATS

You have 2 minutes between heats. Use this to review:

1. **Aggregate limit**: it changes each heat — read it at heat start via `/limits` or observe in the case info
2. **Review P&L from last heat**: which stocks were most profitable?
3. **Adjust spreads**: if you got adversely selected a lot, widen. If you got few fills, tighten.
4. **Adjust sizes**: scale with what worked
5. **Check if correlation patterns changed**: the case says "correlations among stock movements may vary over time"

### Key Config Parameters to Expose for Quick Tuning
```
BASE_SPREAD = {SPNG: 0.04, SMMR: 0.03, ATMN: 0.03, WNTR: 0.02}
ORDER_SIZE = {SPNG: 2000, SMMR: 3000, ATMN: 2500, WNTR: 3000}
SKEW_FACTOR = 0.002
FLATTEN_THRESHOLD = 0.80  # % of aggregate limit to start flattening
PANIC_THRESHOLD = 0.90    # % of aggregate limit to market-order flatten
CLOSE_CANCEL_TICK = 55    # seconds within a minute to cancel all
VOL_LOOKBACK = 10         # seconds for volatility calculation
VOL_WIDEN_MULTIPLIER = 2.0
```

---

## 12. EDGE CASES & FAILURE MODES

### The Algo Crashes Mid-Heat
- Design for graceful recovery: on startup, immediately poll positions and limits
- Cancel all existing orders on startup (clean slate)
- Resume quoting based on current state

### Rate Limit Exceeded
- Back off exponentially
- Prioritize cancels over new orders (don't want stale quotes)
- Reduce number of stocks quoted temporarily

### Extremely Wide Spreads (Illiquid Market)
- If the best bid-ask is $0.50 wide, you can quote inside and capture a huge spread
- But be careful: wide spreads often mean high adverse selection risk
- Quote small sizes in wide markets

### Flash Crash / Price Goes to $0 or Spikes to $50
- Implement price sanity checks: if mid-price is <$5 or >$50, something is wrong
- Widen spreads to max or stop quoting until prices normalize

### All Positions Get Filled Same Direction
- If aggressive buyer is sweeping all your asks across all stocks, you suddenly go very short across the board
- The auto-skew should kick in, but monitor for sudden jumps in aggregate
- Consider setting a per-stock position limit (e.g., ±5,000) beyond which you don't quote that side

---

## 13. COMPETITIVE DYNAMICS

### You're Competing Against Other Algorithms
- Other teams will also be market-making
- If someone quotes tighter than you, they get fills first (price-time priority)
- Don't get into a spread war — if you can't beat them on spread, beat them on inventory management and penalty avoidance
- The scoring rewards consistency: a team that profits $5k every heat beats a team that profits $20k in 3 heats and loses $10k in 3 others

### The "Do No Harm" Principle
- It is better to make $1,000 with zero penalties than to make $5,000 with $3,000 in penalties
- When in doubt, quote wider and smaller
- Survival is strategy #1

---

## 14. SUMMARY: THE WINNING FORMULA

1. **Always be quoting** — especially WNTR and SMMR (highest rebates)
2. **Use limit orders for everything** except emergency flattening
3. **Skew quotes to manage inventory** — never let positions run wild
4. **Respect the close** — cancel everything 5 seconds before each minute mark
5. **Flatten aggressively** when approaching aggregate limit — $10/share penalty is unforgivable
6. **Start conservative, tighten over heats** — first 2-3 heats learn the market, then optimize
7. **Consistency wins** — the ranking system rewards the team that places 5th every heat over the team that places 1st and 30th alternately
8. **Farm rebates relentlessly** — WNTR at $0.025/share is free money on every passive fill
9. **Trade all 4 stocks** — diversification across stocks reduces the chance that one bad fill ruins your heat
10. **Adapt between heats** — use the 2-minute break to tune parameters based on what you learned

---

## 15. API ENDPOINTS CHEAT SHEET

| What You Need | Endpoint | Frequency |
|---------------|----------|-----------|
| Current tick, status | `GET /case` | Every cycle |
| All stock prices, positions, limits | `GET /securities` | Every cycle |
| Order book depth | `GET /securities/book?ticker=X` | Every cycle (per stock) |
| Your open orders | `GET /orders?status=OPEN` | Every cycle |
| Trading limits (gross/net/aggregate) | `GET /limits` | Every 5 seconds |
| Place order | `POST /orders?ticker=X&type=LIMIT&quantity=N&action=BUY&price=P` | As needed |
| Cancel all orders | `POST /commands/cancel?all=1` | Before close / emergency |
| Cancel per ticker | `POST /commands/cancel?ticker=X` | Cancel-replace cycle |
| Time & sales | `GET /securities/tas?ticker=X` | Optional (vol estimation) |
| OHLC history | `GET /securities/history?ticker=X` | Optional (trend analysis) |
| News (if accessible) | `GET /news` | After each close |

---

## 16. PSEUDOCODE: MAIN LOOP

```
INITIALIZE:
  connect to RIT API
  read aggregate_limit from /limits or case info
  cancel all existing orders
  set positions = {SPNG: 0, SMMR: 0, ATMN: 0, WNTR: 0}

MAIN LOOP (every 100-250ms):
  case_info = GET /case
  if case_info.status != "ACTIVE": sleep; continue
  
  tick = case_info.tick
  second_in_minute = tick % 60
  
  # PRE-CLOSE LOCKDOWN
  if second_in_minute >= 55:
    cancel_all_orders()
    flatten_if_needed(threshold=0.85)
    continue  # don't quote during lockdown
  
  # POST-CLOSE RECOVERY (first 5 seconds of new minute)
  if second_in_minute < 5:
    # observe new prices, set wide spreads
    spread_multiplier = 2.0
  else:
    spread_multiplier = 1.0
  
  # FETCH STATE
  securities = GET /securities
  positions = extract_positions(securities)
  prices = extract_prices(securities)
  aggregate = compute_aggregate(positions)
  utilization = aggregate / aggregate_limit
  
  # COMPUTE QUOTES
  for ticker in [WNTR, SMMR, ATMN, SPNG]:  # priority order
    mid = (prices[ticker].bid + prices[ticker].ask) / 2
    vol = compute_volatility(ticker)
    base_spread = BASE_SPREAD[ticker] * spread_multiplier * vol_adjustment(vol)
    skew = -positions[ticker] * SKEW_FACTOR
    
    bid_price = round(mid - base_spread/2 + skew, 2)
    ask_price = round(mid + base_spread/2 + skew, 2)
    
    size = compute_order_size(ticker, utilization, positions[ticker])
    
    # Don't quote the side that increases aggregate if utilization > 85%
    if utilization > 0.85:
      if positions[ticker] > 0:
        bid_size = 0  # don't buy more
      else:
        ask_size = 0  # don't sell more
    
    cancel_orders_for(ticker)
    if bid_size > 0: place_limit_buy(ticker, bid_price, bid_size)
    if ask_size > 0: place_limit_sell(ticker, ask_price, ask_size)
  
  # EMERGENCY FLATTEN
  if utilization > 0.90:
    emergency_flatten(positions, target_utilization=0.70)
  
  sleep(cycle_interval)
```

---

*This strategy document is a living guide. Update parameters after each heat based on observed market behavior.*