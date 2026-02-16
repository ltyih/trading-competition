# Claude Code Command: Fix Algo Market Making Bot

Read all files in this directory (config.py, algo_mm.py, api.py) and make the following precise changes. Do NOT rewrite from scratch — edit the existing code surgically.

---

## CHANGE 1: config.py — Widen Spreads Dramatically

The bot lost $77K because spreads are too narrow for the market volatility. Observed market spreads: SPNG=$0.15, SMMR=$0.07, ATMN=$0.25, WNTR=$0.23. We must quote WIDER than the market to avoid adverse selection. Change BASE_SPREAD to:

```python
BASE_SPREAD = {
    "WNTR": 0.28,
    "SMMR": 0.16,
    "ATMN": 0.30,
    "SPNG": 0.22,
}
```

## CHANGE 2: config.py — Reduce Order Sizes

Each adverse fill at 3000 shares costs $300-900. Cut sizes to reduce per-fill risk:

```python
ORDER_SIZE = {
    "WNTR": 1500,
    "SMMR": 1200,
    "ATMN": 1000,
    "SPNG": 800,
}
```

## CHANGE 3: config.py — Fix Vol Spread Multiplier

HIGH vol multiplier of 2.0 is not enough when prices jump $0.20-0.50. Change:

```python
VOL_SPREAD_MULT = {
    "LOW": 0.85,
    "MEDIUM": 1.0,
    "HIGH": 2.5,
}

VOL_SIZE_MULT = {
    "LOW": 1.3,
    "MEDIUM": 1.0,
    "HIGH": 0.3,
}
```

## CHANGE 4: config.py — Move Pre-Close Cancel Later

Cancelling at second 50 means we're dark for 10 seconds (17% of each day). Change:

```python
PRE_CLOSE_CANCEL_SEC = 56
PRE_CLOSE_FLATTEN_SEC = 57
POST_CLOSE_RECOVERY_SEC = 8
```

## CHANGE 5: config.py — Increase Skew Factor

Positions flip wildly (+3000 to -3000) because skew isn't strong enough to discourage fills that increase position. Change:

```python
SKEW_FACTOR = 0.008
```

## CHANGE 6: config.py — Lower Per-Stock Limit

With 50k aggregate across 4 stocks, no single stock should hold more than 6000. This prevents the wild oscillations seen in logs (WNTR swinging ±4500):

```python
PER_STOCK_POS_LIMIT = 6000
```

## CHANGE 7: algo_mm.py — Fix the Requote Threshold (CRITICAL)

In the `quote_ticker` method, the requote threshold of `half_spread * 0.4` is WAY too tight. The bot re-quotes on every 2-3 cent move, destroying queue priority and chasing adverse moves. Change the threshold to only re-quote when mid has moved more than the FULL half-spread:

Find this block in `quote_ticker`:
```python
        requote_threshold = half_spread * 0.4
        if mid_moved < requote_threshold and last_mid > 0:
            return  # Keep existing quotes - they're still good
```

Replace with:
```python
        requote_threshold = half_spread * 0.9
        if mid_moved < requote_threshold and last_mid > 0:
            return  # Keep existing quotes — preserve queue priority
```

## CHANGE 8: algo_mm.py — Fix Aggregate Limit Glitch

In `update_limits`, the aggregate limit glitches to 13,000 at day boundaries (visible in logs: `Agg=3600/13000`). Cache the limit from heat start and only allow it to change if the new value is reasonable. Replace the `update_limits` method with:

```python
    def update_limits(self):
        """Fetch position limits from the API.
        
        Guards against transient glitch reads at day boundaries
        where gross_limit briefly reads as a smaller number.
        """
        limits = self.api.get_limits()
        if not limits:
            return

        for lim in limits:
            gl = lim.get("gross_limit", 0)
            if gl and isinstance(gl, (int, float)) and gl > 0:
                new_limit = int(gl)
                # Only accept if >= 80% of current known limit
                # This prevents the 13000 glitch at day boundaries
                if self.aggregate_limit > 0:
                    if new_limit >= self.aggregate_limit * 0.8:
                        self.aggregate_limit = new_limit
                        self.gross_limit = new_limit
                else:
                    # First read — accept anything reasonable
                    if new_limit >= 5000:
                        self.aggregate_limit = new_limit
                        self.gross_limit = new_limit

            nl = lim.get("net_limit", 0)
            if nl and isinstance(nl, (int, float)) and nl > 0:
                self.net_limit = int(nl)

            cur_gross = lim.get("gross", 0)
            cur_net = lim.get("net", 0)
            if cur_gross:
                logger.debug("Limits: gross=%.0f/%d net=%.0f/%d",
                            cur_gross, self.aggregate_limit,
                            cur_net, self.net_limit)
```

## CHANGE 9: algo_mm.py — Don't Quote Tighter Than Market Spread

In `quote_ticker`, we currently set `our_spread = max(our_spread, market_spread * 0.85)` which means we quote 15% INSIDE the market. We should quote AT or OUTSIDE the market spread. Change:

Find:
```python
        if market_spread > 0:
            our_spread = max(our_spread, market_spread * 0.85)
```

Replace with:
```python
        if market_spread > 0:
            our_spread = max(our_spread, market_spread * 1.05)
```

## CHANGE 10: algo_mm.py — Add Position-Based Size Reduction

In `compute_order_size`, add a reduction when the stock already has a large position. After the line that computes `close_mult`, add position-based reduction:

Find the end of `compute_order_size` where it has:
```python
        size = int(base_size * vol_mult * util_mult * close_mult)

        # Clamp
        return max(100, min(size, MAX_ORDER_SIZE))
```

Replace with:
```python
        # Position-based reduction: as position grows, shrink new order size
        pos = abs(self.positions.get(ticker, 0))
        pos_limit = PER_STOCK_POS_LIMIT
        if pos > pos_limit * 0.3:
            pos_mult = max(0.2, 1.0 - (pos / pos_limit))
        else:
            pos_mult = 1.0

        size = int(base_size * vol_mult * util_mult * close_mult * pos_mult)

        # Clamp
        return max(100, min(size, MAX_ORDER_SIZE))
```

## CHANGE 11: algo_mm.py — Smarter should_quote_side at UTIL_REDUCE

The current UTIL_REDUCE logic blocks buying if pos > 500 and selling if pos < -500. This is too aggressive — it essentially stops quoting both sides when position is only 500. Change the threshold to be proportional:

Find in `should_quote_side`:
```python
        if utilization > UTIL_REDUCE:
            if pos > 500 and side == "BUY":
                return False
            if pos < -500 and side == "SELL":
                return False
```

Replace with:
```python
        if utilization > UTIL_REDUCE:
            if pos > 2000 and side == "BUY":
                return False
            if pos < -2000 and side == "SELL":
                return False
```

## CHANGE 12: algo_mm.py — Reduce Proactive Reduction Aggressiveness

The `reduce_large_positions` method fires at 60% of PER_STOCK_POS_LIMIT and places aggressive near-market limit orders. With the new lower PER_STOCK_POS_LIMIT of 6000, the threshold would be 3600 which is fine. But the method places orders that compete with our normal quotes, potentially doubling our exposure. Change the method to only fire at 80% and reduce the qty:

Find:
```python
    def reduce_large_positions(self, tick: int):
        """If any stock has position > PER_STOCK_POS_LIMIT * 0.6,
        place aggressive limit orders to reduce it."""
        threshold = int(PER_STOCK_POS_LIMIT * 0.6)  # ~3000 shares
```

Replace with:
```python
    def reduce_large_positions(self, tick: int):
        """If any stock has position > PER_STOCK_POS_LIMIT * 0.8,
        place aggressive limit orders to reduce it."""
        threshold = int(PER_STOCK_POS_LIMIT * 0.8)  # ~4800 shares
```

Also find the reduce_qty lines and cap at 1000:

Find both instances of:
```python
                    reduce_qty = min(abs(pos) - threshold // 2, 2000, MAX_ORDER_SIZE)
```

Replace both with:
```python
                    reduce_qty = min(abs(pos) - threshold // 2, 1000, MAX_ORDER_SIZE)
```

---

## Summary of Why These Changes Fix the $77K Loss

1. **Wider spreads (Changes 1, 3, 9)**: The #1 problem was adverse selection. Every fill was a loser because our quotes were inside the market. Now we quote outside, so we only get filled when the price moves through us — meaning we capture real spread.

2. **Less re-quoting (Change 7)**: The bot was cancelling and re-placing orders every time the mid moved 2-3 cents, which destroyed queue priority and chased every adverse move. Now it only re-quotes when the mid has moved almost a full half-spread.

3. **Smaller sizes (Changes 2, 10)**: Each adverse fill costs less. A 1500-share fill losing $0.10 costs $150 instead of $300 at 3000 shares.

4. **Stronger skew (Change 5)**: Position flips from +3000 to -3000 were costing $600+ per oscillation. Stronger skew means once you accumulate a position, your quotes aggressively push back.

5. **Limit glitch fix (Change 8)**: The 13,000 transient read was causing unnecessary concern at day boundaries.

6. **Later pre-close cancel (Change 4)**: Recovers ~6 seconds of trading per day (30 seconds per heat). At even modest fill rates that's meaningful P&L.

After making all changes, verify by running a test heat. Expected behavior:
- P&L should be slightly positive or near zero (not -$77K)
- Orders placed should be FEWER (maybe 400-600 instead of 1178) because requote threshold is higher
- Positions should oscillate less violently (not ±4500 on WNTR)
- Aggregate utilization can be higher (15-30%) which is fine — we were way too conservative at 2-15%