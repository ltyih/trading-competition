# Volatility Algo Guide (V8.1)

## How to Run
```
cd "Volatility_case/algo_for_volatility"
python main.py
```
Make sure RIT is running and connected first. The algo auto-waits for connection, then auto-starts each sub-heat.

---

## File Overview

| File | Purpose |
|---|---|
| `config.py` | All tunable parameters - **this is the main file you edit** |
| `main.py` | Entry point, main loop, status printing |
| `trading_engine.py` | Core strategy logic (position building, hedging, unwinding) |
| `news_parser.py` | Reads RIT news feed, extracts vol estimates + delta limits |
| `rit_api.py` | Thin HTTP wrapper around the RIT REST API |
| `black_scholes.py` | BS pricing, Greeks, implied vol calculation |

---

## The Strategy in Plain English

1. **Read news** to get the analyst's volatility estimate (e.g. 22%)
2. **Compare** that to the market's implied vol (what options are actually priced at)
3. If analyst vol > market IV → **BUY options** (long vol, market is underpricing)
4. If analyst vol < market IV → **SELL options** (short vol, market is overpricing)
5. **Build a big position fast**, then stop touching options
6. **Delta hedge with RTM stock** to stay delta-neutral (profit comes from vol, not direction)
7. **Unwind everything** near the end of the period

---

## Config Parameters Explained

### Position Limits (from case rules - don't change these)

| Param | Value | Meaning |
|---|---|---|
| `OPTIONS_GROSS_LIMIT` | 2500 | Max total option contracts (longs + shorts combined) |
| `OPTIONS_NET_LIMIT` | 1000 | Max net option contracts (longs - shorts) |
| `OPTIONS_MAX_TRADE_SIZE` | 100 | **Max contracts per single order**. The RIT rejects orders >100. The algo loops to fill larger gaps (e.g. wants 300 → sends 3 orders of 100) |
| `OPTIONS_MULTIPLIER` | 100 | Each option contract controls 100 shares of RTM. So 10 contracts = delta exposure on 1000 shares |
| `RTM_MAX_TRADE_SIZE` | 10000 | Max shares per RTM hedge order |
| `RTM_GROSS_LIMIT` | 50000 | Max RTM shares (gross) |
| `OPTIONS_FEE_PER_CONTRACT` | $1.00 | Commission per option contract traded |
| `RTM_FEE_PER_SHARE` | $0.01 | Commission per RTM share traded |

### Strategy Parameters (tune these)

| Param | Value | What it does | How to adjust |
|---|---|---|---|
| `VOL_EDGE_THRESHOLD` | 0.005 (0.5%) | Minimum difference between analyst vol and market IV before the algo takes a position | **Higher** = fewer trades, only act on strong signals. **Lower** = trade on smaller edges, more active |
| `TARGET_NET_POSITION` | 950 | How many net option contracts to aim for (out of 1000 limit) | **Lower** (e.g. 600) = more conservative, less gamma to hedge. **Higher** = more vega exposure = more profit if right, more risk if wrong |
| `NUM_STRIKES` | 5 | How many strikes to spread across (closest to ATM) | **4** = more concentrated on ATM (higher vega per contract). **6-7** = more spread out, easier to fill but lower vega per contract |
| `FULL_EDGE_THRESHOLD` | 0.03 (3%) | Edge at which the algo goes to full position size. Below this, it scales down proportionally | **Higher** = more cautious scaling. **Lower** = reach full size faster |
| `MAX_OPTION_ORDERS_PER_CYCLE` | 30 | Max orders sent per tick | Higher = build faster but risks API rate limits |
| `LOOP_INTERVAL_SEC` | 0.20 | Seconds between cycles | Lower = more responsive hedging but more API calls |

### Delta Hedging (critical for not blowing up)

| Param | Value | What it does |
|---|---|---|
| `HEDGE_TRIGGER_PCT` | 0.70 (70%) | Hedge when abs(delta) exceeds 70% of the delta limit. E.g. if limit is 10,000, hedge triggers at delta 7,000 |
| `HEDGE_TARGET_PCT` | 0.15 (15%) | Hedge *back to* 15% of limit. E.g. hedge from 7,000 delta down to 1,500 |
| `MIN_HEDGE_SIZE` | 800 | Don't bother hedging if the required trade is <800 shares |
| `HEDGE_COOLDOWN_TICKS` | 3 | Wait at least 3 ticks between hedge trades to prevent oscillation |

**The adaptive system** (in `trading_engine.py:389-404`) also adjusts these based on your gamma-to-limit ratio:
- High gamma → tighter trigger (65%), hedges earlier
- Low gamma → wider trigger (85%), saves commissions

### Reversal Protection (prevents whipsawing)

| Param | Value | What it does |
|---|---|---|
| `NO_REVERSAL_AFTER_TICK` | 250 | After tick 250, the algo will NOT flip from long to short vol (or vice versa), even if the edge changes direction |
| `MIN_REVERSAL_EDGE` | 0.05 (5%) | Must have at least 5% edge to justify flipping direction |
| `REVERSAL_COOLDOWN_TICKS` | 35 | After flipping, wait 35 ticks before allowing another flip |

### Timing

| Param | Value | What it does |
|---|---|---|
| `UNWIND_START_TICK` | 272 | Start closing all positions at tick 272 (out of 300). Gradual at first, aggressive in final 5 ticks |

---

## Common Strategy Changes You Might Want

**1. Be more conservative (less risk):**
```python
TARGET_NET_POSITION = 500       # Half the current size
VOL_EDGE_THRESHOLD = 0.02      # Only trade on 2%+ edge
HEDGE_TRIGGER_PCT = 0.50        # Hedge earlier
```

**2. Be more aggressive (current V8 approach):**
```python
TARGET_NET_POSITION = 950
VOL_EDGE_THRESHOLD = 0.005
NUM_STRIKES = 5
```

**3. Start unwinding earlier (safer near expiry):**
```python
UNWIND_START_TICK = 250         # 50 ticks to unwind instead of 28
```

**4. Prevent all reversals (never flip direction):**
```python
NO_REVERSAL_AFTER_TICK = 1      # Block reversals from tick 1 onward
```

**5. Use fewer strikes for higher concentration:**
```python
NUM_STRIKES = 3                 # Only 3 closest to ATM
```

---

## How News Drives the Algo

`news_parser.py` watches for two types of news:

1. **Realized vol**: "realized volatility this week is 22%" → sets `current_vol = 0.22`
2. **Forecast vol**: "next week between 18% and 24%" → sets mid = 0.21

The `best_vol_estimate` property uses **whichever arrived most recently**. This is the number compared against market IV to determine direction.

It also parses the **delta limit** from news (e.g. "delta limit 10,000"), which controls how aggressively the algo sizes positions and hedges.

---

## Key Lessons from Testing (V6 → V8)

- Market orders only for hedging (limit orders go stale and cause delta overshoot)
- Build position once, then hold - churning options destroys profit via $1/contract commissions
- Hedge cooldown prevents buy/sell oscillation death spiral
- Cancel pending RTM orders before submitting new hedge (prevents accumulation)
- ATM strikes give the most vega per contract
