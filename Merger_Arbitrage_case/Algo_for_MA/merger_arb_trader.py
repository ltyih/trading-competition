# -*- coding: utf-8 -*-
"""
MERGER ARBITRAGE TRADING SYSTEM V5 - RITC 2026
================================================
LESSONS FROM V4 ($30.9k NLV - need $200k+):
- Convergence fights news trades (buy 19.8k then sell 19.8k = burned commissions)
- Missing deal-specific sensitivity multipliers from PDF
- Many [???] news items - body text has deal clues being ignored
- ourP diverges 20-40% from mktP with no correction
- "Competing bid" wrongly classified as NEGATIVE
- Unwind too early pays spread + commissions for no benefit
- Position concentration (D3 at -23.8k, using 84k gross)

STRATEGY V5:
1. Initialize analyst prob from market-implied at startup
2. On news: classify -> trade with per-deal limits (15k max)
3. NO convergence trading (was fighting news signals)
4. Blend ourP toward market after each news update (30%)
5. NO early unwind - let auto close-out handle it at tick 600
6. Per-deal cooldown (20 ticks) prevents whiplash
"""

import requests
import signal
import time
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

# =============================================================================
# CONFIGURATION
# =============================================================================
API_KEY = 'AJDSYHVCES'
API_BASE = 'http://localhost:9998/v1'

DEALS = {
    'D1': {
        'name': 'D1 TGX/PHR Pharma', 'target': 'TGX', 'acquirer': 'PHR',
        'structure': 'ALL_CASH', 'cash': 50.00, 'ratio': 0.0,
        'p0': 0.70, 'target_start': 43.70, 'acquirer_start': 47.50,
    },
    'D2': {
        'name': 'D2 BYL/CLD Cloud', 'target': 'BYL', 'acquirer': 'CLD',
        'structure': 'STOCK_FOR_STOCK', 'cash': 0.0, 'ratio': 0.75,
        'p0': 0.55, 'target_start': 43.50, 'acquirer_start': 79.30,
    },
    'D3': {
        'name': 'D3 GGD/PNR Energy', 'target': 'GGD', 'acquirer': 'PNR',
        'structure': 'MIXED', 'cash': 33.00, 'ratio': 0.20,
        'p0': 0.50, 'target_start': 31.50, 'acquirer_start': 59.80,
    },
    'D4': {
        'name': 'D4 FSR/ATB Banking', 'target': 'FSR', 'acquirer': 'ATB',
        'structure': 'ALL_CASH', 'cash': 40.00, 'ratio': 0.0,
        'p0': 0.38, 'target_start': 30.50, 'acquirer_start': 62.20,
    },
    'D5': {
        'name': 'D5 SPK/EEC Solar', 'target': 'SPK', 'acquirer': 'EEC',
        'structure': 'STOCK_FOR_STOCK', 'cash': 0.0, 'ratio': 1.20,
        'p0': 0.45, 'target_start': 52.80, 'acquirer_start': 48.00,
    },
}

CATEGORY_MULTIPLIERS = {'REG': 1.25, 'FIN': 1.00, 'SHR': 0.90, 'ALT': 1.40, 'PRC': 0.70}
NEWS_IMPACT = {
    'positive': {'small': 0.02, 'medium': 0.05, 'large': 0.10},
    'negative': {'small': -0.03, 'medium': -0.06, 'large': -0.10},
}

GROSS_LIMIT = 100000
NET_LIMIT = 50000
MAX_ORDER_SIZE = 5000
POLL_INTERVAL = 0.15  # faster polling for quicker news reaction
UNWIND_START_TICK = 600  # effectively disabled - let auto close-out handle it
UNWIND_CLOSE_TICK = 600  # effectively disabled

# Per-deal position limit (prevents concentration risk, leaves room for all 5 deals)
MAX_DEAL_POSITION = 15000

# Deal-specific sensitivity multipliers from case PDF
DEAL_SENSITIVITY = {'D1': 1.00, 'D2': 1.05, 'D3': 1.10, 'D4': 1.30, 'D5': 1.15}

# News trade cooldown per deal (ticks)
NEWS_COOLDOWN_TICKS = 20

# Position targets per news severity (shares of target to hold)
TRADE_SIZE = {
    'large': 15000,
    'medium': 10000,
    'small': 5000,
}

# =============================================================================
# LOGGING
# =============================================================================
def setup_logging():
    fmt = logging.Formatter('%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
                            datefmt='%H:%M:%S')
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(fmt)
    log = logging.getLogger('MA')
    log.setLevel(logging.INFO)
    if not log.handlers:
        log.addHandler(h)
    return log

log = setup_logging()


# =============================================================================
# NEWS CLASSIFIER V5
# =============================================================================
class NewsClassifier:
    """Context-aware news classifier."""

    DEAL_KEYWORDS = {
        'D1': {
            'tickers': ['TGX', 'PHR'],
            'names': ['TARGENIX', 'PHARMACO'],
            'sector': ['PHARMACEUTICAL', 'DRUG', 'FDA', 'BIOTECH', 'CLINICAL',
                       'TRIAL', 'PATENT', 'THERAPY', 'MEDICINE', 'PHARMA',
                       'DIVESTITURE', 'HEALTH', 'ONCOLOGY', 'THERAPEUTICS',
                       'BIOLOGIC', 'PIPELINE', 'DIAGNOSTIC', 'GENERIC',
                       'DRUG PRICE', 'THERAPEUTIC AREA', 'THERAPEUTIC',
                       'STATE AG', 'ATTORNEY GENERAL'],
        },
        'D2': {
            'tickers': ['BYL', 'CLD'],
            'names': ['BYTELAYER', 'CLOUDSYS'],
            'sector': ['CLOUD', 'SOFTWARE', 'DATA CENTER', 'SAAS',
                       'DEVELOPER', 'DIGITAL', 'FTC', 'ANTITRUST',
                       'PLATFORM', 'COMPUTE', 'IAAS',
                       'TECH ACQUISITION', 'MARKET SHARE'],
        },
        'D3': {
            'tickers': ['GGD', 'PNR'],
            'names': ['GREENGRID', 'PETRONORTH'],
            'sector': ['INFRASTRUCTURE', 'OIL', 'GAS', 'PIPELINE', 'GRID',
                       'UTILITY', 'FOSSIL', 'CARBON', 'EMISSIONS',
                       'ENVIRONMENTAL', 'GREEN', 'TRANSITION',
                       'PIPELINE CORRIDOR', 'GRID OPERATOR',
                       'ENVIRONMENTAL IMPACT', 'ENERGY INFRASTRUCTURE'],
        },
        'D4': {
            'tickers': ['FSR', 'ATB'],
            'names': ['FINSURE', 'ATLAS BANK', 'ATLAS'],
            'sector': ['BANKING', 'FINANCIAL', 'INSURANCE', 'BANK', 'FDIC',
                       'OCC', 'DEPOSIT', 'BRANCH', 'LENDING', 'CREDIT UNION',
                       'CAPITAL RATIO', 'STRESS TEST',
                       'BANK MERGER', 'COMBINED ASSETS', 'COMMUNITY BANKING',
                       'FED ENHANCED', 'ENHANCED REVIEW'],
        },
        'D5': {
            'tickers': ['SPK', 'EEC'],
            'names': ['SOLARPEAK', 'EASTENERGY', 'EAST ENERGY'],
            'sector': ['SOLAR', 'RENEWABLE', 'WIND', 'CLEAN ENERGY',
                       'TAX CREDIT', 'TURBINE', 'PANEL', 'FERC',
                       'INTERCONNECT', 'GENERATION', 'PHOTOVOLTAIC',
                       'MEGAWATT', 'GENERATION CAPACITY',
                       'RENEWABLE ENERGY', 'SOLAR FARM'],
        },
    }

    POSITIVE_PHRASES = [
        'APPROV', 'SUCCESS', 'SUPPORT', 'FAVOR', 'AGREE', 'ACCEPT',
        'PROGRESS', 'ADVANCE', 'GREEN LIGHT', 'ENDORSE',
        'RECOMMEND', 'BOOST', 'STRONG REVENUE', 'STRONG EARNINGS',
        'ABOVE EXPECT', 'EXCEED', 'COMPLETE', 'FINALIZ', 'CONFIRM',
        'ON TRACK', 'SWEETENED', 'IMPROVED', 'POSITIVE', 'REVENUE GROWTH',
        'SECURED', 'REFINANC', 'REMEDY ACCEPTED', 'UNCONDITIONAL',
        'UNANIM', 'EXPEDIT', 'FAST-TRACK', 'WELCOMES', 'BACKS',
        'FACILITAT', 'CLEARS PATH', 'NO OBJECTION', 'SATISFIED',
        'SYNERG', 'ACCRETIVE', 'UPGRADE', 'OUTPERFORM',
        'DEFINITIVE AGREEMENT', 'FINANCING SECURED', 'DEBT COMMITMENT',
        'RETENTION AGREEMENT', 'EMPLOYEE RETENTION',
        'SMOOTH TRANSITION', 'INTEGRATION PLAN', 'REGULATORY CLEARANCE',
        'SHAREHOLDER APPROVAL', 'VOTE IN FAVOR',
        'SETTLED', 'SETTLEMENT', 'RESOLVED', 'DISMISSED',
        'CLEARED', 'CLEARS', 'WAIVED', 'NO ISSUE', 'NO CONCERN',
        # HSR "Early Termination" = FTC cleared the deal (very positive!)
        'EARLY TERMINATION',
        # Competing bids are POSITIVE for target (drives price up)
        'COMPETING BID', 'RIVAL BID', 'COMPETING INTEREST',
        'COUNTER BID', 'COUNTER OFFER', 'COUNTEROFFER',
        'TOPPING BID', 'SUPERIOR PROPOSAL', 'WHITE KNIGHT',
        'RIVAL OFFER', 'COMPETING OFFER', 'UNSOLICITED BID',
        # Interest rate hedge = risk reduction = positive
        'INTEREST RATE HEDGE', 'RATE HEDGE', 'HEDGING STRATEGY',
        # Deal process milestones = positive
        'MEETING SCHEDULED', 'SETS MEETING', 'SHAREHOLDER VOTE',
        'DIVIDEND MAINTAINED', 'DIVIDEND POLICY MAINTAINED',
        'QUARTERLY RESULTS', 'STRONG QUARTER',
        'LIQUIDITY FACILIT', 'ENHANCED LIQUIDITY',
        'CLEARANCE', 'PROVIDE CLEARANCE', 'PROVIDES CLEARANCE',
        'RECOMMENDS APPROVAL', 'ISSUES FAVORABLE',
        'FAVORABLE OPINION',
        'COMMITMENT SECURED', 'FINANCE COMMITMENT',
        'AGREEMENT SIGNED', 'AGREEMENT EXECUTED', 'AGREEMENT ANNOUNCED',
        'REFINANCED', 'CONVERTIBLE DEBT REFINANCED',
        'OUTSIDE DATE EXTENDED', 'EXTENDS OUTSIDE DATE', 'DEADLINE EXTENDED',
        'STANDSTILL', 'STANDSTILL AGREEMENT',
    ]

    NEGATIVE_PHRASES = [
        'REJECT', 'BLOCK', 'OPPOS', 'FAIL', 'DECLINE',
        'DELAY', 'OBSTACLE', 'THREAT', 'WITHDRAW',
        'CANCEL', 'TERMIN', 'DOUBT', 'LAWSUIT', 'SUE',
        'DISAPPROV', 'BELOW EXPECT', 'WEAK',
        'PROBLEM', 'COMPLICATE', 'PUSHED BACK',
        'SIGNALS CONCERN', 'SCRUTIN', 'PROBE',
        'EXTENDED REVIEW', 'PHASE II', 'SECOND REQUEST',
        'ANTITRUST CONCERN', 'SHAREHOLDER LAWSUIT', 'CLASS ACTION',
        'INJUNCTION', 'POISON PILL', 'DEADLOCK', 'IMPAIR',
        'OVERPAY', 'OVERVALUED', 'DOWNGRADE', 'SELL RATING',
        'UNDERPERFORM', 'SHORTFALL',
        'OUTFLOW', 'DETERIORAT',
        'MATERIAL ADVERSE', 'ADVERSE CHANGE',
        'STRICTER', 'TIGHTER',
        'WALK AWAY', 'ABANDON',
        'REGULATORY HURDLE', 'REGULATORY OBSTACLE',
        'CREDIT DOWNGRADE', 'RATING CUT',
        'COLLAPSES', 'COLLAPSE',
        'WIDEN', 'SPREADS WIDEN',
    ]

    NEGATION_CONTEXT = [
        ('OUTFLOW', 'ACCELERAT'),
        ('SHORTFALL', 'RAISE'),
        ('MAC', 'QUESTION'),
        ('CONCERN', 'RAISE'),
        ('RISK', 'INCREASE'),
        ('DOUBT', 'GROW'),
        ('OBSTACLE', 'EMERGE'),
        ('DECLINE', 'ACCELERAT'),
        ('LOSS', 'WIDEN'),
    ]

    # Phrases that are NEUTRAL despite containing positive/negative keywords
    NEUTRAL_PHRASES = [
        'ANALYSIS PUBLISHED', 'WORKING SESSION', 'TECHNICAL SESSION',
        'NOTIFICATION FILED', 'REPORT ISSUED', 'STAFF MEETING',
        'PROCEDURAL', 'ROUTINE', 'SCHEDULED',
        # "Unusual Institutional Trading Activity" = ambiguous, not negative
        'UNUSUAL INSTITUTIONAL', 'TRADING ACTIVITY',
        'INSTITUTIONAL TRADING', 'UNUSUAL TRADING',
        # "Management Provides Updated Market Outlook" = routine, not negative
        'MARKET OUTLOOK', 'UPDATED MARKET', 'UPDATED OUTLOOK',
        # "Engages Advisors for Strategic Review" = could mean exploring sale
        'STRATEGIC REVIEW', 'ENGAGES ADVISORS',
        # Routine filings/events that aren't directional
        'PREFERRED STOCK ISSUANCE', 'STOCK ISSUANCE',
        'COMMENT PERIOD', 'EXTENDS COMMENT',
        'RISK FACTOR DISCLOSURE', 'UPDATED RISK',
        # Board discussions are neutral
        'BOARD DISCUSSION', 'BOARD DEFENDS',
        'INVESTOR RELATIONS',
    ]

    CATEGORY_KEYWORDS = {
        'REG': ['REGULAT', 'ANTITRUST', 'APPROVAL', 'COMMISSION', 'FTC', 'DOJ',
                'COMPLIANCE', 'GOVERNMENT', 'AUTHORITY', 'RULING', 'LEGAL',
                'INVESTIGATION', 'REVIEW', 'FILING', 'REMEDY', 'DIVESTITURE',
                'FDIC', 'OCC', 'FERC', 'PHASE II', 'ISS', 'GLASS LEWIS',
                'CFIUS', 'SEC ', 'SAMR'],
        'FIN': ['FINANC', 'EARNINGS', 'REVENUE', 'PROFIT', 'LOSS', 'DEBT',
                'CREDIT', 'VALUATION', 'CASH FLOW', 'REFINANC', 'RETENTION',
                'SYNERG', 'LEVERAGE', 'DOWNGRADE', 'UPGRADE', 'RATE'],
        'SHR': ['SHAREHOLD', 'VOTE', 'PROXY', 'ACTIVIST', 'BOARD', 'DIRECTOR',
                'DISSENT', 'OPPOSITION', 'STAKE', 'INVESTOR', 'FAIRNESS',
                'MANAGEMENT', 'SPECULATION', 'INSTITUTIONAL'],
        'ALT': ['ALTERNATIVE', 'COMPETING', 'RIVAL', 'COUNTER', 'BIDDER',
                'HOSTILE', 'UNSOLICITED', 'SWEETENED', 'TOPPING',
                'WHITE KNIGHT', 'SUPERIOR PROPOSAL'],
        'PRC': ['PRICE', 'PREMIUM', 'DISCOUNT', 'FAIR VALUE', 'SPREAD',
                'BOOK VALUE', 'NAV'],
    }

    LARGE_WORDS = ['MAJOR', 'SIGNIFICANT', 'CRITICAL', 'DECISIVE', 'FUNDAMENTAL',
                   'BLOCK', 'TERMIN', 'CANCEL', 'FINAL', 'DEFINITIVE',
                   'UNCONDITIONAL', 'COMPLET', 'PHASE II', 'UNANIM', 'LANDMARK']
    MEDIUM_WORDS = ['IMPORTANT', 'NOTABLE', 'SUBSTANT', 'CONSIDER', 'MATERIAL',
                    'MEANINGFUL', 'PRELIMINARY', 'GROWING', 'REVISED', 'UPDATED']

    def classify(self, headline: str, body: str = '') -> Dict[str, Any]:
        full_text = f"{headline} {body}".upper()
        headline_upper = headline.upper()

        deal_id = self._identify_deal(full_text)
        category = self._identify_category(full_text)
        direction = self._identify_direction(headline_upper, full_text)
        severity = self._identify_severity(full_text)
        delta_p = self._compute_delta_p(deal_id, category, direction, severity)

        return {
            'deal_id': deal_id, 'category': category,
            'direction': direction, 'severity': severity, 'delta_p': delta_p,
        }

    def _identify_deal(self, text: str) -> Optional[str]:
        scores = {}
        for deal_id, kw in self.DEAL_KEYWORDS.items():
            score = 0
            for t in kw['tickers']:
                if t in text:
                    score += 10
            for n in kw['names']:
                if n in text:
                    score += 7
            for w in kw['sector']:
                if w in text:
                    score += 1
            if score > 0:
                scores[deal_id] = score
        if scores:
            best = max(scores, key=scores.get)
            if scores[best] >= 1:  # lowered from 2 to catch body-text matches
                return best
        return None

    def _identify_category(self, text: str) -> str:
        scores = {}
        for cat, keywords in self.CATEGORY_KEYWORDS.items():
            s = sum(1 for kw in keywords if kw in text)
            if s > 0:
                scores[cat] = s
        return max(scores, key=scores.get) if scores else 'FIN'

    def _identify_direction(self, headline: str, full_text: str) -> str:
        """Context-aware direction identification."""
        # Check for neutral/procedural phrases first
        for phrase in self.NEUTRAL_PHRASES:
            if phrase in headline:
                return 'ambiguous'

        # Check for negation contexts
        has_negation = False
        for trigger, modifier in self.NEGATION_CONTEXT:
            if trigger in full_text and modifier in full_text:
                has_negation = True
                break

        # Score headline (primary signal)
        pos_h = sum(1 for w in self.POSITIVE_PHRASES if w in headline)
        neg_h = sum(1 for w in self.NEGATIVE_PHRASES if w in headline)

        # If headline is ambiguous, check body with reduced weight
        if pos_h == neg_h:
            pos_b = sum(1 for w in self.POSITIVE_PHRASES if w in full_text)
            neg_b = sum(1 for w in self.NEGATIVE_PHRASES if w in full_text)
            pos_h += pos_b * 0.3
            neg_h += neg_b * 0.3

        # Apply negation context
        if has_negation:
            neg_h += 2

        if pos_h > neg_h:
            return 'positive'
        elif neg_h > pos_h:
            return 'negative'
        return 'ambiguous'

    def _identify_severity(self, text: str) -> str:
        large = sum(1 for w in self.LARGE_WORDS if w in text)
        medium = sum(1 for w in self.MEDIUM_WORDS if w in text)
        if large >= 2:
            return 'large'
        elif large >= 1 or medium >= 2:
            return 'medium'
        return 'small'

    def _compute_delta_p(self, deal_id, category, direction, severity) -> float:
        if deal_id is None or direction == 'ambiguous':
            return 0.0
        base = NEWS_IMPACT.get(direction, {}).get(severity, 0.0)
        cat_mult = CATEGORY_MULTIPLIERS.get(category, 1.0)
        deal_sens = DEAL_SENSITIVITY.get(deal_id, 1.0)
        return round(base * cat_mult * deal_sens, 4)


# =============================================================================
# PROBABILITY ENGINE
# =============================================================================
class ProbabilityEngine:
    def __init__(self):
        self.analyst_prob = {}
        self.standalone_values = {}
        for did, d in DEALS.items():
            self.analyst_prob[did] = d['p0']
            K0 = d['cash'] + d['ratio'] * d['acquirer_start']
            p0 = d['p0']
            V = (d['target_start'] - p0 * K0) / (1 - p0) if p0 < 1 else d['target_start']
            self.standalone_values[did] = V

    def deal_value(self, did: str, ap: float) -> float:
        d = DEALS[did]
        return d['cash'] + d['ratio'] * ap

    def implied_prob(self, did: str, tp: float, ap: float) -> Optional[float]:
        K = self.deal_value(did, ap)
        V = self.standalone_values[did]
        denom = K - V
        if abs(denom) < 0.01:
            return None
        return max(0.0, min(1.0, (tp - V) / denom))

    def apply_news(self, did: str, dp: float) -> Tuple[float, float]:
        old = self.analyst_prob[did]
        new = max(0.0, min(1.0, old + dp))
        self.analyst_prob[did] = new
        return old, new


# =============================================================================
# ORDER EXECUTOR
# =============================================================================
class OrderExecutor:
    def __init__(self, session):
        self.s = session
        self.total_orders = 0

    def market(self, ticker: str, action: str, qty: int) -> bool:
        if qty <= 0:
            return False
        remaining = qty
        while remaining > 0:
            chunk = min(remaining, MAX_ORDER_SIZE)
            try:
                r = self.s.post(f'{API_BASE}/orders', params={
                    'ticker': ticker, 'type': 'MARKET',
                    'quantity': chunk, 'action': action,
                }, timeout=5)
                if r.ok:
                    log.info(f"  >> {action} {chunk} {ticker}")
                    self.total_orders += 1
                elif r.status_code == 429:
                    time.sleep(r.json().get('wait', 0.5))
                    continue
                else:
                    log.error(f"  FAIL: {r.status_code} {r.text[:80]}")
                    return False
            except Exception as e:
                log.error(f"  ERR: {e}")
                return False
            remaining -= chunk
        return True


# =============================================================================
# MAIN TRADING ENGINE V5
# =============================================================================
class MergerArbTrader:

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({'X-API-Key': API_KEY})

        self.classifier = NewsClassifier()
        self.prob = ProbabilityEngine()
        self.executor = OrderExecutor(self.session)

        self.running = False
        self.last_news_id = 0
        self.tick = 0
        self.prices = {}
        self.positions = {}
        self.news_traded = 0
        self.start_time = None

        # Target positions per deal (positive = long target, negative = short)
        self.deal_targets = {did: 0 for did in DEALS}
        # Per-deal cooldown: last tick a news trade was made
        self.deal_last_trade_tick = {did: -100 for did in DEALS}

    def start(self):
        self.running = True
        self.start_time = datetime.now()

        log.info("=" * 70)
        log.info("  MERGER ARB TRADER V5 - NEWS ONLY + DEAL LIMITS + NO UNWIND")
        log.info("=" * 70)
        for did, d in DEALS.items():
            log.info(f"  {d['name']}: {d['structure']} p0={d['p0']:.0%}")
        log.info("=" * 70)

        # Wait for case
        while self.running:
            try:
                r = self.session.get(f'{API_BASE}/case', timeout=5)
                if r.ok and r.json().get('status') in ('ACTIVE', 'RUNNING'):
                    self.tick = r.json().get('tick', 0)
                    log.info(f"Connected! Tick={self.tick}")
                    break
                time.sleep(1)
            except:
                time.sleep(2)

        self._init_from_market()

        last_status = 0
        last_tender = 0

        while self.running:
            try:
                t0 = time.perf_counter()

                r = self.session.get(f'{API_BASE}/case', timeout=5)
                if not r.ok:
                    time.sleep(0.5)
                    continue
                case = r.json()
                self.tick = case.get('tick', 0)
                status = case.get('status', '')

                # Reset counters when a new heat starts (tick < old values)
                if self.tick < last_status:
                    last_status = 0
                    last_tender = 0

                if status not in ('ACTIVE', 'RUNNING'):
                    if status == 'STOPPED':
                        log.info("Heat ended. Resetting for next...")
                        self._reset()
                        time.sleep(3)
                    else:
                        time.sleep(1)
                    continue

                # Parallel fetch
                with ThreadPoolExecutor(max_workers=2) as pool:
                    f_sec = pool.submit(self.session.get,
                                        f'{API_BASE}/securities', timeout=5)
                    f_news = pool.submit(self.session.get,
                                         f'{API_BASE}/news',
                                         params={'since': self.last_news_id,
                                                 'limit': 100},
                                         timeout=5)
                    sec_resp = f_sec.result()
                    news_resp = f_news.result()

                # Update state
                if sec_resp.ok:
                    for sec in sec_resp.json():
                        t = sec.get('ticker')
                        bid, ask = sec.get('bid', 0), sec.get('ask', 0)
                        if bid and ask:
                            self.prices[t] = round((bid + ask) / 2, 4)
                        elif sec.get('last', 0) > 0:
                            self.prices[t] = sec['last']
                        self.positions[t] = sec.get('position', 0)

                # ===== UNWIND MODE =====
                if self.tick >= UNWIND_CLOSE_TICK:
                    self._unwind()
                elif self.tick >= UNWIND_START_TICK:
                    # No new trades, but don't start unwinding yet
                    # (skip news to avoid opening new positions)
                    if news_resp.ok:
                        # Still track news IDs to avoid processing later
                        for n in news_resp.json():
                            nid = n.get('news_id', 0)
                            if nid > self.last_news_id:
                                headline = n.get('headline', '')
                                log.info(f"  SKIP (unwind): {headline[:60]}")
                                self.last_news_id = max(self.last_news_id, nid)
                else:
                    # ===== ACTIVE TRADING =====
                    # NEWS TRADING - sole trading signal (convergence removed in V5)
                    if news_resp.ok:
                        self._process_news(news_resp.json())

                    # TENDERS
                    if self.tick - last_tender >= 3:
                        self._check_tenders()
                        last_tender = self.tick

                # STATUS
                if self.tick - last_status >= 30:
                    self._status()
                    last_status = self.tick

                elapsed = time.perf_counter() - t0
                time.sleep(max(0.01, POLL_INTERVAL - elapsed))

            except KeyboardInterrupt:
                self.running = False
            except Exception as e:
                log.error(f"Error: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(1)

        self._status()
        log.info("Trader stopped.")

    def _init_from_market(self):
        """Initialize from market prices. Skip historical news."""
        log.info("INITIALIZING FROM MARKET PRICES...")
        try:
            r = self.session.get(f'{API_BASE}/securities', timeout=5)
            if r.ok:
                for sec in r.json():
                    t = sec.get('ticker')
                    bid, ask = sec.get('bid', 0), sec.get('ask', 0)
                    if bid and ask:
                        self.prices[t] = round((bid + ask) / 2, 4)
                    elif sec.get('last', 0) > 0:
                        self.prices[t] = sec['last']
                    self.positions[t] = sec.get('position', 0)

            for did, deal in DEALS.items():
                tp = self.prices.get(deal['target'], 0)
                ap = self.prices.get(deal['acquirer'], 0)
                if tp > 0 and ap > 0:
                    mp = self.prob.implied_prob(did, tp, ap)
                    if mp is not None:
                        self.prob.analyst_prob[did] = mp
                        K = self.prob.deal_value(did, ap)
                        V = self.prob.standalone_values[did]
                        log.info(f"  {did}: p={mp:.1%} T=${tp:.2f} A=${ap:.2f} "
                                 f"K=${K:.2f} V=${V:.2f} range=${K-V:.2f}")

            # Skip all historical news
            r = self.session.get(f'{API_BASE}/news', params={'limit': 200}, timeout=5)
            if r.ok and r.json():
                self.last_news_id = max(n.get('news_id', 0) for n in r.json())
                log.info(f"  Skipped {len(r.json())} old news items")
        except Exception as e:
            log.error(f"Init error: {e}")
        log.info("Initialization complete.")

    def _reset(self):
        """Reset for next heat."""
        self.last_news_id = 0
        self.prob = ProbabilityEngine()
        self.positions = {}
        self.prices = {}
        self.news_traded = 0
        self.deal_targets = {did: 0 for did in DEALS}
        self.deal_last_trade_tick = {did: -100 for did in DEALS}

    def _process_news(self, news_items):
        new = [n for n in news_items if n.get('news_id', 0) > self.last_news_id]
        if not new:
            return

        for news in sorted(new, key=lambda x: x.get('news_id', 0)):
            nid = news.get('news_id', 0)
            headline = news.get('headline', '')
            body = news.get('body', '')

            c = self.classifier.classify(headline, body)
            did = c['deal_id']
            direction = c['direction']
            dp = c['delta_p']

            tag = f"[{did}]" if did else "[???]"
            log.info(f"NEWS {tag} {c['category']} {direction}/{c['severity']} "
                     f"dp={dp:+.4f}: {headline[:90]}")

            if did and dp != 0:
                self._trade_on_news(did, c)

            self.last_news_id = max(self.last_news_id, nid)

    def _trade_on_news(self, did: str, classification: Dict):
        """Trade on news with per-deal limits, cooldown, and market blending."""
        deal = DEALS[did]
        direction = classification['direction']
        dp = classification['delta_p']
        severity = classification['severity']
        category = classification['category']

        # Fix 8: Check cooldown - skip if traded this deal too recently
        ticks_since = self.tick - self.deal_last_trade_tick[did]
        if ticks_since < NEWS_COOLDOWN_TICKS:
            log.info(f"  COOLDOWN: {did} traded {ticks_since} ticks ago, need {NEWS_COOLDOWN_TICKS}")
            # Still apply news to probability even if we don't trade
            self.prob.apply_news(did, dp)
            return

        old_p, new_p = self.prob.apply_news(did, dp)

        # Fix 3: Blend ourP toward market after news update
        target = deal['target']
        acquirer = deal['acquirer']
        tp = self.prices.get(target, 0)
        ap = self.prices.get(acquirer, 0)
        if tp <= 0 or ap <= 0:
            return

        mp = self.prob.implied_prob(did, tp, ap)
        if mp is not None:
            blended = 0.5 * self.prob.analyst_prob[did] + 0.5 * mp
            self.prob.analyst_prob[did] = blended
            new_p = blended

        K = self.prob.deal_value(did, ap)
        V = self.prob.standalone_values[did]
        expected_move = dp * (K - V)

        # Position size based on severity
        desired_size = TRADE_SIZE.get(severity, 5000)

        # Category boost for REG and ALT (highest impact)
        if category in ('REG', 'ALT'):
            desired_size = min(MAX_DEAL_POSITION, int(desired_size * 1.5))

        # Fix 6: Per-deal position limits
        current_pos = int(self.positions.get(target, 0))

        # Market sanity check: don't trade against strong market consensus
        if mp is not None:
            if direction == 'negative' and mp > 0.75:
                log.info(f"  MARKET OVERRIDE: {did} skip SELL - mktP={mp:.1%} too high")
                return
            if direction == 'positive' and mp < 0.25:
                log.info(f"  MARKET OVERRIDE: {did} skip BUY - mktP={mp:.1%} too low")
                return

        if direction == 'positive':
            # Cap so we don't exceed MAX_DEAL_POSITION long
            room_in_deal = max(0, MAX_DEAL_POSITION - current_pos)
            trade_qty = min(desired_size, room_in_deal)
            action = 'BUY'
        else:
            # Cap so we don't exceed MAX_DEAL_POSITION short
            room_in_deal = max(0, MAX_DEAL_POSITION + current_pos)
            trade_qty = min(desired_size, room_in_deal)
            action = 'SELL'

        if trade_qty < 100:
            log.info(f"  DEAL LIMIT: {did} {action} pos={current_pos} limit={MAX_DEAL_POSITION}")
            return

        # Cap by available gross/net room
        avail = self._available_room(target, action)
        actual = min(trade_qty, avail)
        if actual < 100:
            log.info(f"  NO ROOM: {did} {action} wanted={trade_qty} avail={avail}")
            return

        log.info(f"  TRADE: {did} {direction} dp={dp:+.3f} "
                 f"p={old_p:.1%}->{new_p:.1%} exp=${expected_move:+.2f} "
                 f"size={actual} (wanted {desired_size})")

        self.executor.market(target, action, actual)

        # Hedge for non-cash deals
        if deal['structure'] != 'ALL_CASH' and deal['ratio'] > 0:
            hedge_action = 'SELL' if action == 'BUY' else 'BUY'
            hedge_qty = int(actual * deal['ratio'])
            if hedge_qty >= 100:
                hedge_avail = self._available_room(acquirer, hedge_action)
                hedge_actual = min(hedge_qty, hedge_avail)
                if hedge_actual >= 100:
                    self.executor.market(acquirer, hedge_action, hedge_actual)

        self.news_traded += 1
        self.deal_targets[did] = current_pos + (actual if action == 'BUY' else -actual)
        self.deal_last_trade_tick[did] = self.tick  # Fix 8: record cooldown

    def _available_room(self, ticker: str, action: str) -> int:
        """How many shares we can trade. NO cap at MAX_ORDER_SIZE - executor handles chunking."""
        gross = sum(abs(v) for v in self.positions.values())
        net = sum(v for v in self.positions.values())
        gross_room = GROSS_LIMIT - gross
        if action == 'BUY':
            net_room = NET_LIMIT - net
        else:
            net_room = NET_LIMIT + net
        return max(0, int(min(gross_room, net_room)))

    def _check_tenders(self):
        try:
            r = self.session.get(f'{API_BASE}/tenders', timeout=5)
            if not r.ok:
                return
            for tender in r.json():
                tid = tender.get('tender_id')
                ticker = tender.get('ticker', '')
                price = tender.get('price', 0)
                action = tender.get('action', '')
                qty = tender.get('quantity', 0)
                mp = self.prices.get(ticker, 0)
                if mp <= 0:
                    continue

                # Accept tenders that are favorable
                accept = False
                if action == 'BUY' and price > mp * 1.001:
                    accept = True  # Someone wants to buy at above market
                elif action == 'SELL' and price < mp * 0.999:
                    accept = True  # Someone wants to sell below market

                if accept:
                    log.info(f"TENDER: Accept {action} {qty} {ticker} @ ${price:.2f} "
                             f"(mkt ${mp:.2f})")
                    try:
                        self.session.post(f'{API_BASE}/tenders/{tid}', timeout=5)
                    except:
                        pass
        except:
            pass

    def _unwind(self):
        """V5: Disabled. Let auto close-out at tick 600 handle positions.
        Unwinding early pays spread + commissions for no benefit."""
        pass

    def _status(self):
        try:
            r = self.session.get(f'{API_BASE}/trader', timeout=5)
            nlv = r.json().get('nlv', 0) if r.ok else 0
        except:
            nlv = 0

        log.info("=" * 70)
        log.info(f"TICK {self.tick:3d} | NLV: ${nlv:,.2f} | "
                 f"Orders: {self.executor.total_orders} | "
                 f"News traded: {self.news_traded}")

        for did, deal in DEALS.items():
            tp = self.prices.get(deal['target'], 0)
            ap = self.prices.get(deal['acquirer'], 0)
            our_p = self.prob.analyst_prob[did]
            mp = self.prob.implied_prob(did, tp, ap)
            K = self.prob.deal_value(did, ap) if ap > 0 else 0
            t_pos = int(self.positions.get(deal['target'], 0))
            a_pos = int(self.positions.get(deal['acquirer'], 0))
            mp_str = f"{mp:.1%}" if mp is not None else "N/A"

            log.info(f"  {did}: T=${tp:.2f} A=${ap:.2f} K=${K:.2f} "
                     f"ourP={our_p:.1%} mktP={mp_str} "
                     f"tPos={t_pos:+d} aPos={a_pos:+d}")

        gross = sum(abs(v) for v in self.positions.values())
        net = sum(v for v in self.positions.values())
        log.info(f"  Gross={gross:.0f}/{GROSS_LIMIT} Net={net:.0f}/{NET_LIMIT}")
        log.info("=" * 70)

    def stop(self):
        self.running = False


def main():
    trader = MergerArbTrader()
    signal.signal(signal.SIGINT, lambda s, f: trader.stop())
    signal.signal(signal.SIGTERM, lambda s, f: trader.stop())
    trader.start()


if __name__ == '__main__':
    main()
