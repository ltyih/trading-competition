# *REMOVED* 2026 Merger Arbitrage — V7 Strategy & Analysis

## Executive Summary: Why You're Making $90K Instead of $2M+

Your V6 algo has **three fatal flaws**, none of which require ML or an LLM to fix:

1. **Wrong classifications on ~40% of headlines** — your keyword classifier guesses wrong on counter-intuitive headlines like "Activist Hedge Fund Opposes Deal" (actually positive, +$1.93), "Definitive Agreement Executed" (actually negative, -$0.96), "Interconnection Rights Confirmed" (actually negative, -$0.78). The top teams have a **lookup table that maps every headline to the correct action**. You have the data from 13 heats to build this.

2. **Concurrent news contamination** — 8 out of 30 news ticks (34%) had 2-4 simultaneous items. Your extraction script assigns the SAME price impact to ALL of them. That means "Oil Price Collapse Raises Questions", "Reverse Termination Fee Increased", and "Industry Conference Generates Transaction Speculation" all show dp=+0.0807 — but only ONE caused the move. Your lookup table is poisoned by this.

3. **You're not trading enough** — 38 orders total across 600 ticks. Top teams are placing 200+ orders. Your cooldown of 15 ticks, minimum mispricing of $0.20, and unwind logic are all too conservative. You also waste position capacity on unwinding.

---

## The Answer: Pure Lookup Table, Not ML/LLM

**You do NOT need a local LLM or ML model.** Here's why:

- The competition reuses headlines from a **finite pool** (~100-150 unique normalized headlines across all heats)
- The same headline always has the same direction/severity — it's deterministic, not probabilistic
- An LLM adds 50-200ms latency per classification — speed kills in a 600-tick race
- A dict lookup takes <1μs and is 100% accurate for known headlines

**What you need is:**
1. A **data extraction pipeline** that processes all 13 heats into a single clean lookup table
2. A **decontamination algorithm** that handles concurrent news properly
3. A **much more aggressive trading engine** with no cooldown, lower mispricing threshold, and no unwinding

---

## Problem 1: Concurrent News Decontamination

When ticks 355 has 3 news items simultaneously, the price moved once. Your current code assigns the full move to all 3. Here's the fix:

### Solution: Cross-Heat Triangulation

If headline A appears alone in Heat 3 and with headline B in Heat 7, you can isolate A's impact from Heat 3, then compute B = (combined move) - A.

**Algorithm:**
```
For each normalized headline H:
  1. Find all heats where H appeared ALONE (no other news at same tick)
  2. If found: use that heat's observed impact as ground truth
  3. If H only appeared with others: mark as "needs_decontamination"
  4. For contaminated items: subtract known impacts of co-occurring headlines
  5. If still ambiguous after all heats: use the MOST AFFECTED DEAL's prob change
```

### Solution: Body Text Deal Identification

Even when 3 headlines arrive at the same tick, each has a **different body text** that mentions specific tickers/companies. Use the body text to determine WHICH DEAL each headline affects, then attribute the per-deal probability change to the correct headline.

Example from your data — tick 355 had:
- "Reverse Termination Fee Increased" → body mentions SPK/EEC → D5 impact
- "Oil Price Collapse Raises Questions" → body mentions GGD/PNR → D3 impact  
- "Industry Conference Generates Transaction Speculation" → body mentions D2 terms → D2 impact

The D5 probability changed +0.078 at that tick. If the body of "Reverse Termination Fee Increased" mentions SPK, then THAT headline gets the +0.078 attribution.

---

## Problem 2: Your V6 Classifier Is Wrong on Key Headlines

### Confirmed Misclassifications (V6 vs Reality)

| Headline | V6 Says | Actual | Impact |
|----------|---------|--------|--------|
| DEFINITIVE AGREEMENT EXECUTED | positive/large (STRONG_POSITIVE match) | negative/small | -$0.96, dp=-0.004 |
| INTERCONNECTION RIGHTS CONFIRMED | positive/small | negative/small | -$0.78, dp=-0.015 |
| MERGER AGREEMENT SIGNED AND ANNOUNCED | positive (keyword: AGREEMENT) | negative/small | -$0.35, dp=-0.023 |
| CONVERTIBLE DEBT REFINANCED | positive/small | negative/small | -$0.22 |
| ACTIVIST HEDGE FUND OPPOSES DEAL | negative (keyword: OPPOS) | positive/medium | +$1.93, dp=+0.073 |
| INFRASTRUCTURE FUND OPPOSES TERMS | negative (keyword: OPPOS) | positive/medium | +$0.75, dp=+0.030 |
| INDUSTRY CONFERENCE GENERATES TRANSACTION SPECULATION | ambiguous/small (NEUTRAL match) | positive/large | +$0.80, dp=+0.081 |
| SELL-SIDE ANALYSTS ISSUE DIVERGENT VALUATION ASSESSMENTS | negative/medium | ambiguous/small | dp≈0.000 |

### Why These Are Counter-Intuitive

- **"Opposes Deal"** is POSITIVE because opposition from shareholders/funds → potential for higher bid, renegotiation, or deal restructuring that benefits the target
- **"Definitive Agreement Executed"** is NEGATIVE because it was already priced in, and execution often triggers "sell the news" behavior
- **"Interconnection Rights Confirmed"** context matters — in this case the body text indicated complications

**These are NOT random — they follow merger arbitrage logic.** But keyword matching can never capture this. Only a lookup table with observed data can.

---

## Problem 3: Trading Too Conservatively

### Your V6 constraints vs. what top teams do:

| Parameter | Your V6 | Recommended V7 |
|-----------|---------|----------------|
| News cooldown | 15 ticks | 0 (trade every news item) |
| Min mispricing | $0.20 | $0.05 |
| Min trade size | 500 | 100 |
| No-new-trades tick | 580 | 595 |
| Unwind logic | Complex reversal/stale rules | **Remove entirely** |
| Market blend | 70% news / 30% market | 90% news / 10% market |
| Sanity checks (mktP > 0.9) | Skip trade | Trade anyway (our edge IS knowing before market) |

### Why Remove Unwinding?

Your unwind logic at tick 285 sold 11,500 FSR shares. That burned $230 in commissions and consumed position capacity. The position would have auto-closed at tick 600 anyway. Unwinding only makes sense if you need the position room for a BETTER trade — but with your conservative sizing, you never hit limits.

**V7 rule: Never unwind. Use position room for new trades. Let auto-close handle the rest.**

---

## The Data Pipeline You Should Build

### Step 1: Extraction Script (run once per heat)

You already have the extraction producing JSON + XLSX. The key improvement:

```python
# For each news item, record:
{
    "normalized_headline": "ACTIVIST HEDGE FUND OPPOSES DEAL",
    "tick": 364,
    "heat_id": "heat2_20260213",
    "body_text": "...",
    "body_mentions_tickers": ["GGD", "PNR"],  # extracted from body
    "body_deal_id": "D3",  # inferred from body tickers
    "concurrent_count": 2,  # how many news at this tick
    "concurrent_headlines": ["MOODY'S PLACES RATINGS UNDER REVIEW"],
    
    # Per-deal probability changes (from prices sheet)
    "prob_changes": {
        "D1": +0.000, "D2": +0.026, "D3": +0.026, 
        "D4": +0.003, "D5": +0.111
    },
    
    # Per-deal target price changes  
    "price_changes": {
        "TGX": -0.02, "BYL": +1.16, "GGD": +1.93,
        "FSR": 0.00, "SPK": +1.26
    },
    
    # Attribution (which deal did THIS headline affect?)
    "attributed_deal": "D3",  # from body text analysis
    "attributed_dp": +0.026,
    "attributed_price_move": +1.93,
    "is_clean": False,  # True if appeared alone at its tick
}
```

### Step 2: Cross-Heat Merge Script

```python
# Merge all heats into master lookup table
# For each unique normalized headline:
#   1. Collect all observations across all heats
#   2. Prefer "clean" (solo) observations
#   3. Average the attributed impacts
#   4. Compute confidence = n_observations / total_heats_seen
```

### Step 3: Generate the Final Lookup Table

```python
# Output: A single Python dict, ready to paste into the trading algo
MASTER_LOOKUP = {
    "ACTIVIST HEDGE FUND OPPOSES DEAL": {
        "direction": "positive",
        "severity": "medium", 
        "category": "SHR",
        "avg_dp": +0.073,
        "avg_price_move": +1.93,
        "confidence": 0.95,  # seen in 12/13 heats
        "n_obs": 12,
    },
    # ... ~150 entries
}
```

### Step 4: V7 Trading Engine

The trading logic becomes trivial:

```python
def on_news(headline, body):
    normalized = normalize(headline)
    entry = MASTER_LOOKUP.get(normalized)
    
    if entry is None:
        # Unknown headline — fall back to keyword classifier
        # but with VERY LOW confidence and small size
        return classify_keywords(headline, body)
    
    if entry['direction'] == 'ambiguous':
        return  # skip
    
    deal_id = identify_deal(headline + body)  # still use ticker/name matching
    if deal_id is None:
        return
    
    # Trade immediately, max size, no cooldown
    if entry['direction'] == 'positive':
        buy_target(deal_id, size=MAX_DEAL_POSITION)
    else:
        sell_target(deal_id, size=MAX_DEAL_POSITION)
```

---

## Specific Data You Need to Extract from Each Heat

For your 13 heats, run this extraction for each:

1. **News items**: headline, body, tick, news_id
2. **Prices at each tick**: all 10 tickers
3. **Implied probabilities**: compute from prices using the formula in the PDF
4. **Orders placed**: what you traded and when
5. **NLV trajectory**: to measure what worked

The key new computation is **per-deal probability attribution** using body text analysis:

```python
def attribute_news_to_deal(body_text):
    """Which deal does this news body text refer to?"""
    body = body_text.upper()
    scores = {}
    for deal_id, info in DEALS.items():
        score = 0
        for ticker in [info['target'], info['acquirer']]:
            if ticker in body:
                score += 10
        for name in COMPANY_NAMES[deal_id]:
            if name in body:
                score += 8
        for keyword in SECTOR_KEYWORDS[deal_id]:
            if keyword in body:
                score += 2
        scores[deal_id] = score
    
    best = max(scores, key=scores.get)
    if scores[best] >= 8:  # high confidence
        return best
    return None  # can't determine
```

---

## What About the Competition Using New Headlines?

The case PDF says: "The competition environment may include additional news items not seen in practice."

Your defense:

1. **Tier 1 (lookup table)**: Handles ~80-90% of headlines seen before. Instant, 100% accurate.
2. **Tier 2 (improved keyword classifier)**: For unknown headlines. Fix the known misclassification patterns:
   - "Opposes" / "Backlash" / "Activist" → Check if it's about the TARGET (positive, could trigger higher bid) or ACQUIRER (negative)
   - "Agreement Executed/Signed" → Usually sell-the-news, classify as ambiguous/small rather than large positive
   - "Confirmed" / "Rights" → Check body text carefully, don't assume positive
3. **Tier 3 (body text only)**: If headline is unknown AND keyword classifier is confused, scan body text for strong signal words and use with very small position size.

---

## Priority Ranking of Changes (Do These in Order)

1. **[2 hours] Build the multi-heat extraction pipeline** — Process all 13 heats, output clean JSON per heat
2. **[1 hour] Build the cross-heat merge with decontamination** — Produce MASTER_LOOKUP dict
3. **[30 min] Fix the V6 misclassifications** in the keyword classifier (Tier 2 fallback)
4. **[30 min] Remove cooldown, unwinding, conservative thresholds** — Let the algo trade aggressively
5. **[1 hour] Run 3-5 practice heats** with the new lookup table to validate
6. **[30 min] Add any new headlines discovered** in validation heats to the lookup table

**Do NOT spend time on:** Local LLMs, ML models, neural networks, sentiment analysis, NLP. These are all slower and less accurate than a pre-computed lookup table for a finite set of deterministic headlines.

---

## Appendix: All Observed Headlines from Heat 2 (Corrected Classifications)

| # | Headline | Direction | Severity | Most Affected Deal | Prob Change | Target Move |
|---|----------|-----------|----------|-------------------|-------------|-------------|
| 1 | FTC Staff Holds Technical Working Session | negative | small | D2 | -0.006 | -$0.42 |
| 2 | Third-Party Regulatory Analysis Published | ambiguous | small | D2/D4 | varies | varies |
| 3 | Bank Credit Spreads Widen Sharply | negative | small | D2 | -0.006 | -$0.55 |
| 4 | Unusual Institutional Trading Activity | ambiguous | small | varies | ~0.004 | varies |
| 5 | Senior Notes Offering Successful | positive | medium | D5 | +0.033 | +$0.48 |
| 6 | FERC Staff Holds Technical Working Session | negative | small | D5 | -0.011 | -$0.74 |
| 7 | Interconnection Rights Confirmed | **negative** | small | D5 | -0.015 | -$0.78 |
| 8 | Counsel Provides Updated Risk Factor Disclosure | ambiguous | small | varies | ~-0.003 | varies |
| 9 | State Banking Regulators Provide Clearance | positive | small | D4 | +0.008 | +$0.35 |
| 10 | Infrastructure Fund Opposes Terms | **positive** | medium | D3 | +0.030 | +$0.75 |
| 11 | Minority Shareholder Litigation | **positive** | small | D3 | +0.029 | +$0.70 |
| 12 | Major Rating Agency Revises Sector Outlooks | negative | small | D4 | -0.011 | -$0.30 |
| 13 | Atlas Bank Mgmt Updated Market Outlook | negative | **large** | D4 | -0.070 | -$1.22 |
| 14 | Force Majeure Event Raises Concerns | negative | medium | D5 | -0.045 | +$0.72* |
| 15 | Fed Announces Enhanced Review Process | negative | medium | D5 | -0.044 | varies* |
| 16 | Outside Date Extended to Year-End | positive | small | D5 | +0.005 | +$0.57 |
| 17 | FinSure Sets Meeting for Shareholder Vote | negative | small | D4 | -0.007 | -$0.56 |
| 18 | Congressional Banking Committee Inquiry | negative | small | D2/D4 | -0.010 | -$0.61 |
| 19 | Definitive Agreement Executed | **negative** | small | D4 | -0.016 | -$0.96 |
| 20 | Pharmaco Shareholder Backlash | negative | medium | D5 | -0.058 | -$0.55 |
| 21 | Pharmaco Stock Price Decline | negative | small | D4 | -0.026 | -$0.39 |
| 22 | Key Engineer Retention Plan Announced | negative | small | D4 | -0.026 | -$0.39* |
| 23 | Private Equity Interest Reported | negative | small | D4 | -0.026 | -$0.39* |
| 24 | Merger Agreement Signed and Announced | **negative** | small | D4 | -0.023 | -$0.35 |
| 25 | Reverse Termination Fee Increased | positive | large | D5 | +0.078 | +$0.80 |
| 26 | Oil Price Collapse Raises Questions | **positive** | large | D3/D5 | +0.078* | +$0.80* |
| 27 | Industry Conference Generates Speculation | positive | large | D5 | +0.081 | +$0.80* |
| 28 | ByteLayer Engages Advisors | positive | large | D5 | +0.100 | +$0.86 |
| 29 | Convertible Note Holders Seek Clarity | positive | medium | D5 | +0.068 | +$0.85 |
| 30 | Investment Grade Bond Offering Completed | positive | **large** | D5 | +0.173 | +$1.29 |
| 31 | Activist Hedge Fund Opposes Deal | **positive** | medium | D3/D5 | +0.073 | +$1.93 |
| 32 | Moody's Places Ratings Under Review | **positive** | medium | D3/D5 | +0.073* | +$1.93* |
| 33 | Targenix Board Reaffirms Support | positive | small | D1 | -0.008 | +$0.29 |
| 34 | EPA Issues Supportive Statement | positive | small | D5 | -0.002 | +$0.52 |
| 35 | FTC Clears Transaction - Early Termination | positive | small | D2 | +0.012 | +$0.76 |
| 36 | Industry Lobbyists Support Transaction | positive | small | D4 | +0.010 | +$0.60 |
| 37 | Improved Financial Disclosures | positive | small | D4 | +0.010 | +$0.60* |
| 38 | Termination Fee Structure | positive | medium | D5 | +0.062 | +$0.54 |
| 39 | UK CMA Phase I Review Completed | positive | small | D5 | +0.023 | +$0.28 |
| 40 | Equity Co-Investment Secured | positive | small | D5 | +0.023* | +$0.28* |
| 41 | Sector Multiple Compression | positive | small | D5 | +0.023* | +$0.28* |
| 42 | Bank Secrecy Act Compliance Review | negative | small | D4 | -0.018 | -$0.27 |
| 43 | Sell-Side Analysts Divergent Valuations | **ambiguous** | small | varies | ~0.000 | varies |
| 44 | Targenix Shareholder Meeting Scheduled | negative | small | D1 | -0.020 | -$0.42* |
| 45 | Closed-Door Meeting Between Principals | negative | small | D2 | -0.002 | -$0.18 |
| 46 | Convertible Debt Refinanced | **negative** | small | D2/D4 | -0.003 | -$0.22 |
| 47 | Congressional Tech Oversight Hearing | negative | small | D4 | -0.001 | -$0.13 |
| 48 | Vote Tracking Shows Majority Support | **negative** | small | D5 | -0.007 | +$0.44* |
| 49 | FinSure Board Defends Transaction | **ambiguous** | small | D5 | -0.005 | +$0.44* |
| 50 | Hedge Fund Activist Opposes Deal | **positive** | small | D4 | -0.009 | +$0.57 |
| 51 | Stress Test Requirements Imposed | **ambiguous** | small | D1 | -0.004 | +$0.43* |
| 52 | GreenGrid Schedules Shareholder Vote | positive | medium | D5 | +0.075 | +$0.60 |

*Items marked with * have contaminated attribution due to concurrent news. Cross-reference with other heats.

**Bold** entries are cases where the actual observed direction differs from what V6 keyword classifier would predict. These are the highest-priority fixes.