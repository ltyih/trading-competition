# Merger Arbitrage Automated Trading System - V6 Strategy Specification

## Table of Contents
0. [THE WINNING STRATEGY - READ THIS FIRST](#0-the-winning-strategy)
1. [Executive Summary](#1-executive-summary)
2. [Why Previous Versions Failed](#2-why-previous-versions-failed)
3. [Core Strategic Insight](#3-core-strategic-insight)
4. [System Architecture](#4-system-architecture)
5. [Module 1: News Engine](#5-module-1-news-engine)
6. [Module 2: Probability Engine](#6-module-2-probability-engine)
7. [Module 3: Trading Engine](#7-module-3-trading-engine)
8. [Module 4: Risk Manager](#8-module-4-risk-manager)
9. [Module 5: Tender Handler](#9-module-5-tender-handler)
10. [Deal Configurations](#10-deal-configurations)
11. [Implementation Priorities](#11-implementation-priorities)
12. [File Structure](#12-file-structure)

---

## 0. THE WINNING STRATEGY - READ THIS FIRST

### How $600k Is Made (The Math)

```
5 sub-heats × ~15 news items per sub-heat = ~75 tradeable events per heat
Each news item moves a target stock by $0.50 to $3.00
If you trade 10,000 shares on the RIGHT side: $5,000 to $30,000 profit per event
Average $8,000 per correctly traded news item × 75 events = $600,000
```

The ENTIRE game is:
1. **News drops** -> you read it
2. **You decide**: Is this GOOD or BAD for the deal?
3. **You trade the TARGET stock**: BUY if good news (deal more likely, target goes UP toward deal price), SELL if bad news (deal less likely, target goes DOWN toward standalone value)
4. **You do this FASTER than other teams**

That's it. Everything else (probability formulas, hedging, position sizing) is optimization. The $30k→$600k gap is **classification accuracy × speed × size**.

### Why V4 Made Only $30k

V4's keyword classifier got direction WRONG on ~30-40% of news items. Every wrong call is a DOUBLE loss:
- You lose what you should have gained (missed the right trade)
- You lose from the wrong position (traded the opposite direction)

Example: "Competing bid emerges for FSR" → V4 classified this as NEGATIVE (because "competing" wasn't in positive list, and news about uncertainty triggered negative words). But this is MASSIVELY POSITIVE for the target - competing bids drive target price UP. V4 sold when it should have bought. On 10,000 shares with a $2 move, that's -$20k instead of +$20k = **$40k swing from ONE misclassification**.

### The Winning Approach: News Template Lookup + Keyword Fallback

**Key insight from the PDF**: "The practice case is designed to help teams become familiar with the market dynamics and news flow. The competition environment may include additional news items not seen in practice, while preserving the same general structure and categories."

This means:
1. **Most news items in competition will be similar to practice news items**
2. The news has a FINITE template set - there are only so many headlines the simulator generates
3. Teams that **record every news item from practice** and build a lookup table will classify 70-80% of competition news INSTANTLY by matching against known templates

### The Two-Tier Strategy

```
TIER 1: LOOKUP TABLE (instant, 100% accurate on known news)
  - Run the observer.py during practice sessions
  - Record every headline + body + the ACTUAL price movement that followed
  - Build a map: headline_template → {deal, direction, severity}
  - At runtime: hash/fuzzy-match incoming headline against lookup table
  - If match found → trade IMMEDIATELY with full confidence

TIER 2: KEYWORD CLASSIFIER (fallback for unknown news)
  - Multi-layer classification pipeline (detailed in Module 1)
  - Only used when Tier 1 has no match
  - Conservative sizing (smaller positions on uncertain classification)
```

### How To Build The Lookup Table

1. **Run `observer.py` during every practice session** (the file already exists in Algo_for_MA/)
2. For each news item, record:
   - `news_id`, `tick`, `headline`, `body`
   - Price of all 10 securities BEFORE the news (2-3 ticks before)
   - Price of all 10 securities AFTER the news (5-10 ticks after)
3. From the price movement, determine:
   - Which deal was affected (which target moved most)
   - Direction: target went UP = positive, DOWN = negative
   - Severity: how much did it move in probability points?
4. Store as a JSON/dict mapping

```python
# Example lookup table entry
NEWS_TEMPLATES = {
    "Regulators indicate remedies framework is acceptable": {
        'deal_pattern': 'D4',  # or None if generic
        'direction': 'positive',
        'severity': 'medium',
        'category': 'REG',
        'confidence': 1.0,
    },
    "Credit conditions deteriorate": {
        'deal_pattern': 'D3',
        'direction': 'negative',
        'severity': 'large',
        'category': 'FIN',
        'confidence': 1.0,
    },
    "Competing bid emerges": {
        'deal_pattern': None,  # could be any deal
        'direction': 'positive',  # POSITIVE for target!
        'severity': 'large',
        'category': 'ALT',
        'confidence': 1.0,
    },
}
```

### Fuzzy Matching for Lookup

News headlines may vary slightly between practice and competition. Use a simple approach:

```python
def normalize_headline(headline):
    """Strip numbers, tickers, and normalize for fuzzy matching."""
    text = headline.upper()
    # Remove ticker symbols (they change per deal but template is same)
    for ticker in ALL_TICKERS:
        text = text.replace(ticker, 'TICKER')
    # Remove company names
    for name in ALL_COMPANY_NAMES:
        text = text.replace(name, 'COMPANY')
    # Remove extra whitespace
    text = ' '.join(text.split())
    return text

def lookup_news(headline):
    normalized = normalize_headline(headline)
    # Exact match first
    if normalized in NEWS_TEMPLATES:
        return NEWS_TEMPLATES[normalized]
    # Substring match (headline contains a known template)
    for template, classification in NEWS_TEMPLATES.items():
        if template in normalized or normalized in template:
            return classification
    # No match - fall back to keyword classifier
    return None
```

### Position Sizing By Confidence

```
Tier 1 match (lookup table):    15,000 shares (full size)
Tier 2 strong signal:           10,000 shares
Tier 2 normal classification:    5,000 shares
Ambiguous/unsure:                    0 shares (SKIP)
```

### The Speed Advantage

```
Poll interval: 150ms
News detection → classification: <10ms (lookup) or <50ms (keyword)
Order submission: <100ms
TOTAL: News to order in <350ms

Other teams (manual): 3-10 seconds
Other teams (bad bots): 0.5-2 seconds
```

If you trade 3 seconds before the crowd, you get filled at pre-news prices. The market then moves to post-news equilibrium and you're sitting on profit.

### The Hedging Decision (Simple Rule)

```
ALL-CASH deals (D1, D4):     Only trade target. No hedge needed.
STOCK-FOR-STOCK (D2, D5):   Trade target + short acquirer (ratio hedge)
MIXED (D3):                  Trade target + small short acquirer (0.20 ratio)
```

Why hedge? In stock-for-stock deals, if the acquirer drops, the deal value K drops, so your long target position loses money even if deal probability hasn't changed. Shorting the acquirer at the exchange ratio neutralizes this risk.

### End-of-Heat Behavior

```
Ticks 1-580:    Normal trading (react to news)
Ticks 580-600:  NO NEW TRADES. Let positions ride to close-out.
                Do NOT unwind early (V4 lesson - wastes commissions).
```

### What To Prioritize In Practice Sessions

1. **RECORD ALL NEWS** - Every headline, every body, every price reaction
2. **Build the lookup table** - This is your #1 competitive advantage
3. **Test classification accuracy** - Run classifier on recorded news, check against actual price moves
4. **Identify misclassifications** - Fix the keyword lists for items the lookup missed
5. **Measure speed** - Time from news appearance to order fill

---

## 1. Executive Summary

**Goal**: Build a fully automated merger arbitrage trading bot for the *REMOVED* 2026 competition that reads news from the RIT API, classifies each news item by deal/direction/severity/category, updates deal completion probabilities, computes intrinsic target prices, and trades on the difference between intrinsic price and market price. Target: $200k+ NLV per heat (top teams hit $600k+).

**The single most important thing**: Getting the news direction RIGHT (positive vs negative vs ambiguous). Every dollar of P&L flows from this one classification. A wrong direction = trading the wrong way = doubled loss (you lose what you should have gained AND lose from the wrong position).

---

## 2. Why Previous Versions Failed

### V4 Results: $30.9k NLV (need $200k+)

| Problem | Root Cause | Impact |
|---------|-----------|--------|
| Convergence fights news trades | Convergence logic reversed news positions | Burned commissions on both sides |
| Many `[???]` unidentified news items | Keyword matching too narrow; body text ignored | Missed 30-40% of tradeable signals |
| `ourP` diverged 20-40% from `mktP` | No anchoring/correction mechanism | Positions grew against market consensus |
| "Competing bid" classified NEGATIVE | Missing from positive phrase list | Sold target on most bullish signal |
| Early unwind | Paid spread + commissions for no benefit | Unnecessary cost drag |
| Position concentration | No per-deal limits | D3 at -23.8k shares, 84k gross on one deal |

### V5 Partial Fixes Applied But Still Insufficient

V5 removed convergence trading, added per-deal limits (15k), added cooldown (20 ticks), added market blending (50/50), and added market sanity checks. These are structural improvements but the **core classifier is still keyword-based** and will continue to misclassify nuanced news.

### The Real Problem

The teams making $600k+ are getting **direction right on nearly every news item**. The keyword approach fails because:

1. **Context matters**: "Regulator DELAYS approval" is negative, but "Regulator DELAYS objection" is positive
2. **Negation**: "No obstacles remain" is positive despite containing "obstacles"
3. **Severity is often wrong**: keyword counting doesn't distinguish a "MAJOR breakthrough" from a "MAJOR setback"
4. **Deal identification fails on sector-only clues**: Many news items don't mention tickers or company names directly

---

## 3. Core Strategic Insight

### The Alpha Is In Speed + Accuracy of News Interpretation

The market (other RIT bots + human players) will react to news over 5-30 seconds. If we:
1. **Correctly classify** the news direction within 0.5 seconds
2. **Trade immediately** with the right size
3. **Get direction right 80%+ of the time**

...we capture the spread between pre-news price and post-news equilibrium.

### Strategy Components (Priority Order)

1. **NEWS CLASSIFICATION** (80% of alpha) - Must be fast and accurate
2. **PROBABILITY UPDATE** (10% of alpha) - Use the PDF formula: `dp = base * cat_mult * deal_mult`
3. **POSITION SIZING** (5% of alpha) - Scale by conviction and available room
4. **HEDGING** (3% of alpha) - Short acquirer on stock-for-stock/mixed deals
5. **TENDER ACCEPTANCE** (2% of alpha) - Accept favorable tenders

### What NOT To Do

- **NO convergence trading** - This fights news signals and burns commissions
- **NO early unwind** - Let close-out at tick 600 handle it (saves spread + commission)
- **NO trading on ambiguous news** - Better to skip than to be wrong
- **NO position reversal within cooldown** - Prevents whiplash

---

## 4. System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    MAIN LOOP (150ms)                     │
│                                                         │
│  ┌──────────┐   ┌──────────────┐   ┌──────────────┐   │
│  │ API      │──>│ News Engine  │──>│ Probability   │   │
│  │ Fetcher  │   │ (Classifier) │   │ Engine        │   │
│  │          │   └──────────────┘   └──────┬───────┘   │
│  │ (parallel│                             │            │
│  │  threads)│   ┌──────────────┐          │            │
│  │          │──>│ Trading      │<─────────┘            │
│  │          │   │ Engine       │                        │
│  │          │   └──────┬───────┘                        │
│  │          │          │                                │
│  │          │   ┌──────▼───────┐   ┌──────────────┐   │
│  │          │   │ Risk         │   │ Tender       │   │
│  │          │   │ Manager      │   │ Handler      │   │
│  └──────────┘   └──────────────┘   └──────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### Main Loop Flow

```
every 150ms:
  1. GET /case -> check tick, status
  2. PARALLEL: GET /securities + GET /news?since=last_id + GET /tenders
  3. Process NEW news items (sorted by news_id ascending):
     a. classify(headline, body) -> {deal_id, direction, severity, category}
     b. compute delta_p from classification
     c. update analyst_prob[deal_id]
     d. compute intrinsic_price vs market_price
     e. if abs(intrinsic - market) > threshold: TRADE
  4. Check tenders -> accept if favorable
  5. Log status every 30 ticks
```

---

## 5. Module 1: News Engine

### This is the most critical module. 80% of development time should go here.

### 5.1 News Classification Pipeline

```python
def classify(headline: str, body: str) -> dict:
    """
    Returns:
        deal_id: str or None (D1-D5)
        direction: 'positive' | 'negative' | 'ambiguous'
        severity: 'small' | 'medium' | 'large'
        category: 'REG' | 'FIN' | 'SHR' | 'ALT' | 'PRC'
        delta_p: float (probability change)
        confidence: float (0-1, how sure we are)
    """
```

### 5.2 Deal Identification Strategy

**Priority scoring system** (highest score wins):

| Signal | Points | Example |
|--------|--------|---------|
| Ticker in text (TGX, PHR, etc.) | +10 | "TGX shares rally" |
| Full company name | +8 | "Targenix announces..." |
| Partial company name | +5 | "Atlas Bank..." |
| Deal-specific sector keyword (strong) | +3 | "FDA approval" -> D1 |
| Deal-specific sector keyword (weak) | +1 | "pharmaceutical" -> D1 |

**CRITICAL**: Search BOTH headline AND body for deal identification. Many news items put tickers only in the body.

**Minimum score threshold**: 3 (to avoid false positives from generic sector words)

#### Deal Keyword Maps

```python
DEAL_KEYWORDS = {
    'D1': {
        'tickers': ['TGX', 'PHR'],
        'names': ['TARGENIX', 'PHARMACO'],
        'strong_sector': ['FDA', 'CLINICAL TRIAL', 'DRUG APPROVAL', 'BIOTECH',
                          'DIVESTITURE', 'PATENT', 'ONCOLOGY', 'THERAPEUTICS',
                          'STATE AG', 'ATTORNEY GENERAL', 'BIOLOGIC', 'PIPELINE'],
        'weak_sector': ['PHARMACEUTICAL', 'DRUG', 'THERAPY', 'MEDICINE',
                        'PHARMA', 'HEALTH', 'DIAGNOSTIC', 'GENERIC'],
    },
    'D2': {
        'tickers': ['BYL', 'CLD'],
        'names': ['BYTELAYER', 'CLOUDSYS'],
        'strong_sector': ['CLOUD COMPUTING', 'DATA CENTER', 'SAAS', 'IAAS',
                          'FTC', 'ANTITRUST REVIEW', 'MARKET SHARE',
                          'TECH ACQUISITION', 'PLATFORM'],
        'weak_sector': ['CLOUD', 'SOFTWARE', 'DIGITAL', 'DEVELOPER',
                        'COMPUTE', 'TECHNOLOGY'],
    },
    'D3': {
        'tickers': ['GGD', 'PNR'],
        'names': ['GREENGRID', 'PETRONORTH'],
        'strong_sector': ['PIPELINE CORRIDOR', 'GRID OPERATOR', 'FOSSIL',
                          'CARBON EMISSIONS', 'ENVIRONMENTAL IMPACT',
                          'ENERGY INFRASTRUCTURE', 'OIL AND GAS'],
        'weak_sector': ['INFRASTRUCTURE', 'OIL', 'GAS', 'PIPELINE', 'GRID',
                        'UTILITY', 'CARBON', 'EMISSIONS', 'ENVIRONMENTAL',
                        'GREEN', 'TRANSITION'],
    },
    'D4': {
        'tickers': ['FSR', 'ATB'],
        'names': ['FINSURE', 'ATLAS BANK', 'ATLAS'],
        'strong_sector': ['FDIC', 'OCC', 'COMMUNITY BANKING', 'BANK MERGER',
                          'COMBINED ASSETS', 'FED ENHANCED', 'ENHANCED REVIEW',
                          'STRESS TEST', 'CAPITAL RATIO', 'DEPOSIT'],
        'weak_sector': ['BANKING', 'FINANCIAL', 'INSURANCE', 'BANK',
                        'BRANCH', 'LENDING', 'CREDIT UNION'],
    },
    'D5': {
        'tickers': ['SPK', 'EEC'],
        'names': ['SOLARPEAK', 'EASTENERGY', 'EAST ENERGY'],
        'strong_sector': ['FERC', 'INTERCONNECT', 'SOLAR FARM', 'PHOTOVOLTAIC',
                          'MEGAWATT', 'GENERATION CAPACITY', 'TAX CREDIT',
                          'RENEWABLE ENERGY'],
        'weak_sector': ['SOLAR', 'RENEWABLE', 'WIND', 'CLEAN ENERGY',
                        'TURBINE', 'PANEL', 'GENERATION'],
    },
}
```

### 5.3 Direction Classification - THE MOST CRITICAL PART

#### Architecture: Multi-Layer Classification

**Layer 1: Neutral/Procedural Filter** (check first, return 'ambiguous' if match)

These phrases in the HEADLINE indicate non-directional/procedural news. Skip them.

```python
NEUTRAL_HEADLINE_PHRASES = [
    'ANALYSIS PUBLISHED', 'WORKING SESSION', 'TECHNICAL SESSION',
    'NOTIFICATION FILED', 'REPORT ISSUED', 'STAFF MEETING',
    'PROCEDURAL', 'ROUTINE', 'SCHEDULED',
    'UNUSUAL INSTITUTIONAL', 'TRADING ACTIVITY',
    'MARKET OUTLOOK', 'UPDATED MARKET', 'UPDATED OUTLOOK',
    'STRATEGIC REVIEW', 'ENGAGES ADVISORS',
    'PREFERRED STOCK ISSUANCE', 'STOCK ISSUANCE',
    'COMMENT PERIOD', 'EXTENDS COMMENT',
    'RISK FACTOR DISCLOSURE', 'UPDATED RISK',
    'BOARD DISCUSSION', 'BOARD DEFENDS',
    'INVESTOR RELATIONS',
]
```

**Layer 2: Strong Signal Phrases** (high-confidence direction indicators)

These are phrases where direction is unambiguous regardless of context:

```python
STRONG_POSITIVE = [
    # Regulatory clearances
    'UNCONDITIONAL APPROVAL', 'UNCONDITIONAL CLEARANCE',
    'REGULATORY CLEARANCE', 'CLEARS WITHOUT CONDITIONS',
    'EARLY TERMINATION',  # HSR early termination = FTC cleared
    'GREEN LIGHT', 'PROVIDES CLEARANCE', 'ISSUES FAVORABLE',
    'REMEDY ACCEPTED', 'REMEDIES ACCEPTED',

    # Competing bids (POSITIVE for target!)
    'COMPETING BID', 'RIVAL BID', 'TOPPING BID', 'COUNTER BID',
    'COUNTEROFFER', 'COUNTER OFFER', 'SUPERIOR PROPOSAL',
    'WHITE KNIGHT', 'RIVAL OFFER', 'COMPETING OFFER',
    'UNSOLICITED BID', 'SWEETENED',

    # Deal milestones
    'DEFINITIVE AGREEMENT', 'AGREEMENT SIGNED', 'AGREEMENT EXECUTED',
    'FINANCING SECURED', 'DEBT COMMITMENT', 'COMMITMENT SECURED',
    'SHAREHOLDER APPROVAL', 'VOTE IN FAVOR', 'UNANIM',

    # Legal resolution
    'SETTLED', 'SETTLEMENT', 'DISMISSED', 'RESOLVED',
    'WAIVED', 'NO OBJECTION',

    # Process acceleration
    'EXPEDIT', 'FAST-TRACK', 'ACCELERAT',
    'OUTSIDE DATE EXTENDED', 'DEADLINE EXTENDED',
]

STRONG_NEGATIVE = [
    # Deal killers
    'BLOCK', 'BLOCKED', 'TERMIN', 'CANCEL',
    'WALK AWAY', 'ABANDON', 'COLLAPSE',

    # Regulatory opposition
    'PHASE II', 'SECOND REQUEST', 'EXTENDED REVIEW',
    'ANTITRUST CONCERN', 'REGULATORY HURDLE',

    # Legal threats
    'INJUNCTION', 'CLASS ACTION', 'SHAREHOLDER LAWSUIT',
    'POISON PILL',

    # Credit/financial deterioration
    'CREDIT DOWNGRADE', 'RATING CUT', 'MATERIAL ADVERSE',

    # Severe process issues
    'DEADLOCK', 'IMPAIR',
]
```

**Layer 3: Context-Aware Phrase Matching**

The standard positive/negative word lists, but with NEGATION HANDLING:

```python
# Check for negation patterns that FLIP meaning:
NEGATION_PREFIXES = ['NO ', 'NOT ', 'WITHOUT ', "DON'T ", 'FAILS TO ', 'UNABLE TO ']

# Before scoring, check if a positive/negative word is preceded by a negation
# "No obstacles" -> positive (negation of negative)
# "Not approved" -> negative (negation of positive)
```

```python
POSITIVE_WORDS = [
    'APPROV', 'SUCCESS', 'SUPPORT', 'FAVOR', 'AGREE', 'ACCEPT',
    'PROGRESS', 'ADVANCE', 'ENDORSE', 'RECOMMEND', 'BOOST',
    'STRONG REVENUE', 'STRONG EARNINGS', 'ABOVE EXPECT', 'EXCEED',
    'COMPLETE', 'FINALIZ', 'CONFIRM', 'ON TRACK',
    'IMPROVED', 'POSITIVE', 'REVENUE GROWTH',
    'SECURED', 'REFINANC',
    'SMOOTH TRANSITION', 'INTEGRATION PLAN',
    'CLEARED', 'CLEARS',
    'INTEREST RATE HEDGE', 'HEDGING STRATEGY',
    'MEETING SCHEDULED', 'SETS MEETING', 'SHAREHOLDER VOTE',
    'DIVIDEND MAINTAINED', 'STRONG QUARTER',
    'LIQUIDITY FACILIT', 'ENHANCED LIQUIDITY',
    'RECOMMENDS APPROVAL', 'FAVORABLE OPINION',
    'REFINANCED', 'STANDSTILL AGREEMENT',
    'RETENTION AGREEMENT', 'EMPLOYEE RETENTION',
]

NEGATIVE_WORDS = [
    'REJECT', 'OPPOS', 'FAIL', 'DECLINE',
    'DELAY', 'OBSTACLE', 'THREAT', 'WITHDRAW',
    'DOUBT', 'LAWSUIT', 'SUE', 'DISAPPROV',
    'BELOW EXPECT', 'WEAK', 'PROBLEM', 'COMPLICATE',
    'PUSHED BACK', 'SIGNALS CONCERN', 'SCRUTIN', 'PROBE',
    'ANTITRUST CONCERN',
    'OVERPAY', 'OVERVALUED', 'DOWNGRADE', 'SELL RATING',
    'UNDERPERFORM', 'SHORTFALL',
    'OUTFLOW', 'DETERIORAT',
    'ADVERSE CHANGE', 'STRICTER', 'TIGHTER',
    'SPREADS WIDEN', 'WIDEN',
]
```

**Layer 4: Headline vs Body Weighting**

```python
# Headline is the PRIMARY signal (2x weight)
# Body is SECONDARY (1x weight, only used if headline is ambiguous)

headline_pos = count_matches(headline, POSITIVE_WORDS) * 2
headline_neg = count_matches(headline, NEGATIVE_WORDS) * 2

# Only check body if headline is tied or has zero matches
if headline_pos == headline_neg:
    body_pos = count_matches(body, POSITIVE_WORDS) * 1
    body_neg = count_matches(body, NEGATIVE_WORDS) * 1
    total_pos = headline_pos + body_pos
    total_neg = headline_neg + body_neg
else:
    total_pos = headline_pos
    total_neg = headline_neg

if total_pos > total_neg:
    return 'positive'
elif total_neg > total_pos:
    return 'negative'
else:
    return 'ambiguous'
```

**Layer 5: Negation Context Pairs**

These are two-word patterns that, when BOTH appear, indicate a negative direction even if one word seems positive:

```python
NEGATION_CONTEXT_PAIRS = [
    ('OUTFLOW', 'ACCELERAT'),    # "Deposit outflows accelerating"
    ('SHORTFALL', 'REVENUE'),     # "Revenue shortfall"
    ('CONCERN', 'RAISE'),         # "Raises concerns"
    ('RISK', 'INCREASE'),         # "Increased risk"
    ('DOUBT', 'GROW'),            # "Growing doubts"
    ('OBSTACLE', 'EMERGE'),       # "Obstacles emerge"
    ('DECLINE', 'ACCELERAT'),     # "Decline accelerating"
    ('LOSS', 'WIDEN'),            # "Losses widen"
    ('MAC', 'QUESTION'),          # "MAC clause questioned"
]
```

### 5.4 Severity Classification

```python
LARGE_INDICATORS = [
    'MAJOR', 'SIGNIFICANT', 'CRITICAL', 'DECISIVE', 'FUNDAMENTAL',
    'BLOCK', 'TERMIN', 'CANCEL', 'FINAL', 'DEFINITIVE',
    'UNCONDITIONAL', 'COMPLET', 'PHASE II', 'UNANIM', 'LANDMARK',
    'COLLAPSE', 'TRANSFORM',
]

MEDIUM_INDICATORS = [
    'IMPORTANT', 'NOTABLE', 'SUBSTANT', 'CONSIDER', 'MATERIAL',
    'MEANINGFUL', 'PRELIMINARY', 'GROWING', 'REVISED', 'UPDATED',
    'EMERGES', 'EXTENDS', 'MODERATE',
]

# Scoring:
# large_count >= 2 -> 'large'
# large_count >= 1 OR medium_count >= 2 -> 'medium'
# else -> 'small'
```

### 5.5 Category Classification

```python
CATEGORY_KEYWORDS = {
    'REG': ['REGULAT', 'ANTITRUST', 'APPROVAL', 'COMMISSION', 'FTC', 'DOJ',
            'COMPLIANCE', 'GOVERNMENT', 'AUTHORITY', 'RULING', 'LEGAL',
            'INVESTIGATION', 'REVIEW', 'FILING', 'REMEDY', 'DIVESTITURE',
            'FDIC', 'OCC', 'FERC', 'PHASE II', 'ISS', 'GLASS LEWIS',
            'CFIUS', 'SEC ', 'SAMR'],
    'FIN': ['FINANC', 'EARNINGS', 'REVENUE', 'PROFIT', 'LOSS', 'DEBT',
            'CREDIT', 'VALUATION', 'CASH FLOW', 'REFINANC', 'RETENTION',
            'SYNERG', 'LEVERAGE', 'DOWNGRADE', 'UPGRADE', 'RATE',
            'BALANCE', 'QUARTER', 'ANNUAL', 'EPS', 'MARKET CAP'],
    'SHR': ['SHAREHOLD', 'VOTE', 'PROXY', 'ACTIVIST', 'BOARD', 'DIRECTOR',
            'DISSENT', 'OPPOSITION', 'STAKE', 'INVESTOR', 'FAIRNESS',
            'MANAGEMENT', 'SPECULATION', 'INSTITUTIONAL', 'ADVOCACY'],
    'ALT': ['ALTERNATIVE', 'COMPETING', 'RIVAL', 'COUNTER', 'BIDDER',
            'HOSTILE', 'UNSOLICITED', 'SWEETENED', 'TOPPING',
            'WHITE KNIGHT', 'SUPERIOR PROPOSAL', 'REVISED'],
    'PRC': ['PRICE', 'PREMIUM', 'DISCOUNT', 'FAIR VALUE', 'SPREAD',
            'BOOK VALUE', 'NAV', 'COST', 'SAVING'],
}
```

### 5.6 Delta-P Computation

Directly from the case PDF:

```python
# From PDF Table 1: Baseline impact by direction and severity
BASELINE_IMPACT = {
    'positive': {'small': 0.03, 'medium': 0.07, 'large': 0.14},
    'negative': {'small': -0.04, 'medium': -0.09, 'large': -0.18},
    'ambiguous': {'small': 0.0, 'medium': 0.0, 'large': 0.0},
}

# From PDF Table 2: Category multipliers
CATEGORY_MULTIPLIERS = {
    'REG': 1.25,
    'FIN': 1.00,
    'SHR': 0.90,
    'ALT': 1.40,
    'PRC': 0.70,
}

# From PDF Table 3: Deal-specific sensitivity multipliers
DEAL_SENSITIVITY = {
    'D1': 1.00,  # Pharma cash
    'D2': 1.05,  # Tech stock-for-stock
    'D3': 1.10,  # Energy mixed
    'D4': 1.30,  # Banking cash (HIGHEST sensitivity)
    'D5': 1.15,  # Renewable stock
}

def compute_delta_p(direction, severity, category, deal_id):
    base = BASELINE_IMPACT[direction][severity]
    cat_mult = CATEGORY_MULTIPLIERS[category]
    deal_mult = DEAL_SENSITIVITY[deal_id]
    return base * cat_mult * deal_mult
```

**Example from PDF**: D4 positive/medium/REG = 0.07 * 1.25 * 1.30 = **+0.11375** (11.4 pp)

---

## 6. Module 2: Probability Engine

### 6.1 Initialization (at t=0 or on first tick)

```python
# For each deal, compute standalone value V (fixed throughout sub-heat)
# Formula: V = (P0 - p0 * K0) / (1 - p0)

# Where:
#   P0 = starting target price
#   p0 = initial completion probability
#   K0 = deal value at t=0

# For all-cash deals: K = cash_component
# For stock-for-stock: K = exchange_ratio * acquirer_price
# For mixed: K = cash + ratio * acquirer_price

DEALS = {
    'D1': {'target': 'TGX', 'acquirer': 'PHR', 'cash': 50.00, 'ratio': 0.0,
            'p0': 0.70, 'target_start': 43.70, 'acquirer_start': 47.50},
    'D2': {'target': 'BYL', 'acquirer': 'CLD', 'cash': 0.0, 'ratio': 0.75,
            'p0': 0.55, 'target_start': 43.50, 'acquirer_start': 79.30},
    'D3': {'target': 'GGD', 'acquirer': 'PNR', 'cash': 33.00, 'ratio': 0.20,
            'p0': 0.50, 'target_start': 31.50, 'acquirer_start': 59.80},
    'D4': {'target': 'FSR', 'acquirer': 'ATB', 'cash': 40.00, 'ratio': 0.0,
            'p0': 0.38, 'target_start': 30.50, 'acquirer_start': 62.20},
    'D5': {'target': 'SPK', 'acquirer': 'EEC', 'cash': 0.0, 'ratio': 1.20,
            'p0': 0.45, 'target_start': 52.80, 'acquirer_start': 48.00},
}
```

### 6.2 Standalone Values (Pre-computed)

| Deal | K0 | V | K-V Range |
|------|----|---|-----------|
| D1 | $50.00 | $36.70 | $13.30 |
| D2 | $59.475 | $24.08 | $35.40 |
| D3 | $44.96 | $18.04 | $26.92 |
| D4 | $40.00 | $24.68 | $15.32 |
| D5 | $57.60 | $49.05 | $8.55 |

**Interpretation**: D2 has the widest K-V range ($35.40), so each probability point moves target price ~$0.35. D5 has the narrowest ($8.55), so each pp moves price ~$0.085. This means:
- **D2 and D3 have the most price impact per news item** -> higher profit potential
- **D4 has highest sensitivity multiplier (1.30)** -> news hits harder
- **D5 has lowest dollar impact** -> less profit per correct trade, but lower risk

### 6.3 Market-Implied Probability

```python
def implied_prob(deal_id, target_price, acquirer_price):
    K = deal_value(deal_id, acquirer_price)
    V = standalone_values[deal_id]
    if abs(K - V) < 0.01:
        return None
    return clamp((target_price - V) / (K - V), 0.0, 1.0)
```

### 6.4 Analyst Probability Update

```python
def update_analyst_prob(deal_id, delta_p, market_implied_p):
    old_p = analyst_prob[deal_id]
    news_p = clamp(old_p + delta_p, 0.0, 1.0)

    # BLEND with market to prevent runaway divergence
    # Use 70% news, 30% market (news is our edge, but market is a sanity check)
    if market_implied_p is not None:
        blended = 0.70 * news_p + 0.30 * market_implied_p
    else:
        blended = news_p

    analyst_prob[deal_id] = blended
    return old_p, blended
```

**Why 70/30 blend**: Pure news-based probability diverges from market. V4 saw 20-40% divergence. Market is noisy but is a consensus signal. Blending keeps us grounded while still giving weight to our news edge.

### 6.5 Intrinsic Price Computation

```python
def intrinsic_target_price(deal_id, acquirer_price):
    p = analyst_prob[deal_id]
    K = deal_value(deal_id, acquirer_price)
    V = standalone_values[deal_id]
    return p * K + (1 - p) * V
```

---

## 7. Module 3: Trading Engine

### 7.1 Trade Signal

```python
def should_trade(deal_id, target_price, acquirer_price):
    intrinsic = intrinsic_target_price(deal_id, acquirer_price)
    mispricing = intrinsic - target_price

    # Minimum mispricing threshold (accounts for spread + commission)
    # Commission is $0.02/share, spread is ~$0.05-0.15
    MIN_MISPRICING = 0.20  # $0.20 minimum edge

    if abs(mispricing) < MIN_MISPRICING:
        return None

    if mispricing > 0:
        return 'BUY'  # Target is cheap relative to our intrinsic
    else:
        return 'SELL'  # Target is expensive relative to our intrinsic
```

### 7.2 Position Sizing

```python
def compute_trade_size(deal_id, direction, severity, category, current_position):
    # Base sizes by severity
    BASE_SIZES = {
        'large': 15000,
        'medium': 10000,
        'small': 5000,
    }

    desired = BASE_SIZES[severity]

    # Boost for high-conviction categories
    if category in ('REG', 'ALT'):
        desired = int(desired * 1.3)  # REG and ALT are highest-impact

    # Per-deal position cap: 15000 shares (long or short)
    MAX_DEAL_POS = 15000

    if direction == 'positive':
        room = max(0, MAX_DEAL_POS - current_position)
        action = 'BUY'
    else:  # negative
        room = max(0, MAX_DEAL_POS + current_position)
        action = 'SELL'

    actual = min(desired, room)

    # Enforce gross/net limits
    actual = min(actual, available_gross_room())
    actual = min(actual, available_net_room(action))

    # Minimum trade size (below this, commissions eat the edge)
    if actual < 500:
        return 0, None

    return actual, action
```

### 7.3 Order Execution

```python
MAX_ORDER_SIZE = 5000  # RIT limit per order

def execute_market_order(ticker, action, quantity):
    remaining = quantity
    while remaining > 0:
        chunk = min(remaining, MAX_ORDER_SIZE)
        resp = session.post(f'{API_BASE}/orders', params={
            'ticker': ticker,
            'type': 'MARKET',
            'quantity': chunk,
            'action': action,
        }, timeout=5)

        if resp.status_code == 429:
            wait = resp.json().get('wait', 0.5)
            time.sleep(wait)
            continue  # Retry same chunk

        if not resp.ok:
            log.error(f"Order failed: {resp.status_code}")
            break

        remaining -= chunk
```

### 7.4 Hedging (Stock-for-Stock and Mixed Deals)

For D2 (ratio 0.75), D3 (ratio 0.20), and D5 (ratio 1.20):

```python
def hedge_acquirer(deal_id, target_action, target_qty):
    deal = DEALS[deal_id]
    if deal['ratio'] == 0:
        return  # All-cash deals don't need hedging

    # If we BUY target, SHORT acquirer (classic merger arb)
    # If we SELL target, BUY acquirer (unwinding the arb)
    hedge_action = 'SELL' if target_action == 'BUY' else 'BUY'
    hedge_qty = int(target_qty * deal['ratio'])

    if hedge_qty >= 100:
        # Check room for hedge
        available = available_room(deal['acquirer'], hedge_action)
        actual_hedge = min(hedge_qty, available)
        if actual_hedge >= 100:
            execute_market_order(deal['acquirer'], hedge_action, actual_hedge)
```

### 7.5 News Trade Cooldown

```python
NEWS_COOLDOWN_TICKS = 15  # Minimum ticks between trades on same deal

last_trade_tick = {did: -100 for did in DEALS}

def can_trade_deal(deal_id, current_tick):
    return (current_tick - last_trade_tick[deal_id]) >= NEWS_COOLDOWN_TICKS
```

### 7.6 Market Sanity Checks

Before trading, verify our direction isn't wildly against market consensus:

```python
def market_sanity_check(deal_id, direction, market_prob):
    """Don't trade against extreme market consensus."""
    if market_prob is None:
        return True  # No market data, proceed

    # Don't buy if market thinks deal is 90%+ done (limited upside)
    if direction == 'positive' and market_prob > 0.90:
        return False

    # Don't sell if market thinks deal is 10%- likely (limited downside left)
    if direction == 'negative' and market_prob < 0.10:
        return False

    # Don't sell when market is very bullish (our classifier might be wrong)
    if direction == 'negative' and market_prob > 0.80:
        log.warning(f"CAUTION: Selling {deal_id} but market p={market_prob:.1%}")
        return False

    # Don't buy when market is very bearish
    if direction == 'positive' and market_prob < 0.20:
        log.warning(f"CAUTION: Buying {deal_id} but market p={market_prob:.1%}")
        return False

    return True
```

---

## 8. Module 4: Risk Manager

### 8.1 Position Limits (From Case PDF)

| Limit | Value |
|-------|-------|
| Gross limit (sum of abs positions) | 100,000 shares |
| Net limit (sum of signed positions) | 50,000 shares |
| Max order size | 5,000 shares |
| Commission | $0.02/share |

### 8.2 Per-Deal Position Limits

```python
MAX_DEAL_POSITION = 15000  # Per deal, target stock

# This leaves room for 5 deals * 15k = 75k gross from targets
# Plus hedging on acquirers: ~20k additional
# Total ~95k vs 100k gross limit
```

### 8.3 Available Room Calculation

```python
def available_gross_room():
    gross = sum(abs(pos) for pos in positions.values())
    return max(0, 100000 - gross)

def available_net_room(action):
    net = sum(positions.values())
    if action == 'BUY':
        return max(0, 50000 - net)
    else:
        return max(0, 50000 + net)

def available_room(ticker, action):
    return min(available_gross_room(), available_net_room(action))
```

### 8.4 No Early Unwind

```python
# V4 lesson learned: unwinding early pays spread + commissions for no benefit
# Let RIT auto close-out at tick 600 handle all positions
# This saves:
#   - ~$0.10/share in spread costs
#   - $0.02/share in commissions (each direction)
#   - Avoids selling winners too early
UNWIND_TICK = 600  # Effectively disabled
```

---

## 9. Module 5: Tender Handler

```python
def check_tenders(session, prices):
    """Accept tenders that are favorable to us."""
    resp = session.get(f'{API_BASE}/tenders', timeout=5)
    if not resp.ok:
        return

    for tender in resp.json():
        ticker = tender.get('ticker', '')
        price = tender.get('price', 0)
        action = tender.get('action', '')
        market_price = prices.get(ticker, 0)

        if market_price <= 0:
            continue

        # Accept if someone wants to buy above market
        if action == 'BUY' and price > market_price * 1.001:
            session.post(f'{API_BASE}/tenders/{tender["tender_id"]}', timeout=5)

        # Accept if someone wants to sell below market
        elif action == 'SELL' and price < market_price * 0.999:
            session.post(f'{API_BASE}/tenders/{tender["tender_id"]}', timeout=5)
```

---

## 10. Deal Configurations

### Pre-Computed Values for Quick Reference

| Deal | Structure | K (at t=0) | V | p0 | K-V | $/pp |
|------|-----------|------------|------|------|------|------|
| D1 TGX/PHR | All-Cash | $50.00 | $36.70 | 70% | $13.30 | $0.133 |
| D2 BYL/CLD | Stock-for-Stock | $59.475 | $24.08 | 55% | $35.40 | $0.354 |
| D3 GGD/PNR | Mixed | $44.96 | $18.04 | 50% | $26.92 | $0.269 |
| D4 FSR/ATB | All-Cash | $40.00 | $24.68 | 38% | $15.32 | $0.153 |
| D5 SPK/EEC | Stock-for-Stock | $57.60 | $49.05 | 45% | $8.55 | $0.086 |

**$/pp** = dollars per probability point = (K-V)/100. This is how much target price moves per 1pp change in completion probability.

### Deal Priority for Trading

1. **D2 (BYL/CLD)**: Highest $/pp ($0.354) - most profitable per correct call
2. **D3 (GGD/PNR)**: Second highest $/pp ($0.269) + 1.10 sensitivity
3. **D4 (FSR/ATB)**: Highest sensitivity (1.30) + reasonable $/pp ($0.153)
4. **D1 (TGX/PHR)**: High p0 (70%) means limited upside but good downside trade
5. **D5 (SPK/EEC)**: Lowest $/pp ($0.086) - least profitable, trade last

---

## 11. Implementation Priorities

### Phase 1: Core Trading Loop (GET THIS WORKING FIRST)
1. API connection, session management, tick/status polling
2. Securities fetching (prices, positions)
3. News fetching with incremental `since` parameter
4. Basic deal identification (ticker + company name matching)
5. Basic direction classification (strong signal phrases + word lists)
6. Delta-p computation using PDF formula
7. Market order execution with chunking
8. Gross/net limit enforcement

### Phase 2: Classification Refinement
1. Negation handling
2. Neutral/procedural filter
3. Body text fallback for ambiguous headlines
4. Negation context pairs
5. Severity classification
6. Category classification

### Phase 3: Trading Intelligence
1. Intrinsic price computation
2. Mispricing-based trade signals
3. Per-deal position limits
4. Cooldown timer
5. Market sanity checks
6. Acquirer hedging for stock-for-stock deals

### Phase 4: Robustness
1. Multi-heat support (reset between sub-heats)
2. Rate limit handling (429 with backoff)
3. Parallel API fetching (ThreadPoolExecutor)
4. Tender handling
5. Status logging every 30 ticks
6. Error recovery

---

## 12. File Structure

```
Merger_Arbitrage_case/
├── Algo_for_MA/
│   ├── ma_trader_v6.py        # Main entry point + trading loop
│   ├── news_classifier.py     # News classification engine
│   ├── probability_engine.py  # Probability computation + updates
│   ├── order_executor.py      # Order execution + chunking
│   ├── risk_manager.py        # Position limits + room calculation
│   ├── config.py              # All constants, deal definitions
│   └── README.md              # How to run
```

### config.py Constants

```python
# API
API_KEY = 'AJDSYHVCES'
API_BASE = 'http://localhost:9998/v1'

# Timing
POLL_INTERVAL = 0.15  # 150ms between cycles
NEWS_COOLDOWN_TICKS = 15

# Position Limits
GROSS_LIMIT = 100000
NET_LIMIT = 50000
MAX_ORDER_SIZE = 5000
MAX_DEAL_POSITION = 15000
COMMISSION = 0.02

# Trading
MIN_MISPRICING = 0.20  # Minimum edge to trade ($)
MARKET_BLEND_WEIGHT = 0.30  # How much to blend toward market prob

# Trade Sizes
TRADE_SIZE = {
    'large': 15000,
    'medium': 10000,
    'small': 5000,
}
```

---

## Appendix: Full Classification Decision Tree

```
INPUT: headline, body

1. full_text = (headline + " " + body).upper()
2. headline_upper = headline.upper()

3. DEAL IDENTIFICATION:
   For each deal D1-D5:
     score = 0
     for ticker in deal.tickers: if ticker in full_text: score += 10
     for name in deal.names: if name in full_text: score += 8
     for kw in deal.strong_sector: if kw in full_text: score += 3
     for kw in deal.weak_sector: if kw in full_text: score += 1
   best_deal = deal with highest score (if score >= 3)

4. NEUTRAL CHECK:
   for phrase in NEUTRAL_HEADLINE_PHRASES:
     if phrase in headline_upper: return direction='ambiguous'

5. STRONG SIGNAL CHECK:
   strong_pos = count(phrase in headline_upper for phrase in STRONG_POSITIVE)
   strong_neg = count(phrase in headline_upper for phrase in STRONG_NEGATIVE)
   if strong_pos > 0 and strong_neg == 0: direction = 'positive'
   if strong_neg > 0 and strong_pos == 0: direction = 'negative'
   if both > 0: fall through to word-level

6. WORD-LEVEL SCORING:
   pos_score = count headline positive words * 2
   neg_score = count headline negative words * 2
   # Check negation (words preceded by NO/NOT/WITHOUT flip their score)
   if pos_score == neg_score:
     pos_score += count body positive words * 1
     neg_score += count body negative words * 1
   # Check negation context pairs
   for (trigger, modifier) in NEGATION_CONTEXT_PAIRS:
     if trigger in full_text and modifier in full_text:
       neg_score += 2  # Context pairs are always negative

7. DIRECTION:
   if pos_score > neg_score: direction = 'positive'
   elif neg_score > pos_score: direction = 'negative'
   else: direction = 'ambiguous'

8. SEVERITY:
   large_count = count large indicators in full_text
   medium_count = count medium indicators in full_text
   if large_count >= 2: severity = 'large'
   elif large_count >= 1 or medium_count >= 2: severity = 'medium'
   else: severity = 'small'

9. CATEGORY:
   For each category (REG, FIN, SHR, ALT, PRC):
     score = count keyword matches in full_text
   category = highest scoring (default 'FIN')

10. DELTA_P:
    base = BASELINE_IMPACT[direction][severity]
    delta_p = base * CATEGORY_MULTIPLIERS[category] * DEAL_SENSITIVITY[deal_id]

11. RETURN: {deal_id, direction, severity, category, delta_p}
```

---

## Appendix: V4/V5 Lessons Learned (Do NOT Repeat)

1. **NEVER do convergence trading** - It fights news signals
2. **NEVER unwind early** - Let close-out handle it
3. **ALWAYS check body text** for deal identification (many `[???]` items had tickers in body)
4. **Competing bids are POSITIVE** for target stock
5. **"Early termination" (HSR)** is VERY POSITIVE (FTC cleared the deal)
6. **Don't trade on ambiguous** - better to skip than be wrong
7. **Blend analyst prob toward market** to prevent runaway divergence
8. **Per-deal position limits** prevent concentration risk
9. **Cooldown between trades** prevents whiplash on rapid news
10. **D4 (Banking) has highest sensitivity** (1.30) - big swings on news