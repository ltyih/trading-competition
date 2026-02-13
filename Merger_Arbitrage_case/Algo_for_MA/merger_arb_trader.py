# -*- coding: utf-8 -*-
"""
MERGER ARBITRAGE TRADING SYSTEM V6 - RITC 2026
================================================
V6 FIXES FROM V5 ($30.9k NLV - need $200k+):

FIX  1: NEWS_IMPACT values corrected to match PDF (were 30-80% too low)
FIX  2: Market blend 70/30 (was 50/50, killing our news edge)
FIX  3: Deal ID threshold raised to 3 (was 1, false positives)
FIX  4: Strong/weak sector scoring (+3/+1) instead of flat +1
FIX  5: STRONG_POSITIVE/STRONG_NEGATIVE layer (instant classification)
FIX  6: Negation prefix handling ("No obstacles" = positive)
FIX  7: Body weight 1.0x with headline 2.0x (was 0.3x body)
FIX  8: Body search uses body-only text (was double-counting headline)
FIX  9: NEWS_COOLDOWN 15 ticks (was 20)
FIX 10: Category boost 1.3x (was 1.5x)
FIX 11: 4-level market sanity (was 2-level)
FIX 12: Minimum trade size 500 (was 100)
FIX 13: Missing severity words added
FIX 14: Intrinsic price / mispricing check added (MIN_MISPRICING=$0.20)
FIX 15: Missing category keywords added
FIX 16: No-new-trades after tick 580 (was only 600)
FIX 17: News template lookup table (Tier 1 instant classification)
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
API_KEY = 'AJDSYHVC'
API_BASE = 'http://localhost:10000/v1'

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

# FIX 1: Corrected to match PDF Table 1 (were 30-80% too low)
NEWS_IMPACT = {
    'positive': {'small': 0.03, 'medium': 0.07, 'large': 0.14},
    'negative': {'small': -0.04, 'medium': -0.09, 'large': -0.18},
}

GROSS_LIMIT = 100000
NET_LIMIT = 50000
MAX_ORDER_SIZE = 5000
POLL_INTERVAL = 0.15
NO_NEW_TRADES_TICK = 580      # FIX 16: Stop opening new positions at tick 580
UNWIND_CLOSE_TICK = 600       # Let auto close-out handle it

MAX_DEAL_POSITION = 15000
DEAL_SENSITIVITY = {'D1': 1.00, 'D2': 1.05, 'D3': 1.10, 'D4': 1.30, 'D5': 1.15}

# FIX 9: Cooldown 15 ticks (was 20)
NEWS_COOLDOWN_TICKS = 15

# FIX 14: Minimum mispricing to trade
MIN_MISPRICING = 0.20

# FIX 2: Market blend weight (30% market, was 50%)
MARKET_BLEND_WEIGHT = 0.30

TRADE_SIZE = {
    'large': 15000,
    'medium': 10000,
    'small': 5000,
}

# FIX 12: Minimum trade size (was 100)
MIN_TRADE_SIZE = 500

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
# FIX 17: NEWS TEMPLATE LOOKUP TABLE (Tier 1 - instant, 100% accuracy)
# =============================================================================
# Built from practice session observations. Normalized headlines map to
# known classifications. This is the #1 competitive advantage.

ALL_TICKERS = ['TGX', 'PHR', 'BYL', 'CLD', 'GGD', 'PNR', 'FSR', 'ATB', 'SPK', 'EEC']
ALL_COMPANY_NAMES = [
    'TARGENIX', 'PHARMACO', 'BYTELAYER', 'CLOUDSYS', 'GREENGRID',
    'PETRONORTH', 'FINSURE', 'ATLAS BANK', 'ATLAS', 'SOLARPEAK',
    'EASTENERGY', 'EAST ENERGY',
]


def normalize_headline(headline: str) -> str:
    """Strip tickers, company names, and normalize for fuzzy matching."""
    text = headline.upper().strip()
    for ticker in ALL_TICKERS:
        text = text.replace(ticker, 'TICKER')
    for name in ALL_COMPANY_NAMES:
        text = text.replace(name, 'COMPANY')
    text = ' '.join(text.split())
    return text


# Lookup table: normalized_headline -> classification
# Populated from practice session observations (logs show actual news items)
NEWS_TEMPLATES = {
    # === POSITIVE templates ===
    'PROJECT FINANCE COMMITMENT SECURED': {
        'direction': 'positive', 'severity': 'medium', 'category': 'FIN', 'confidence': 1.0,
    },
    'INTERCONNECTION RIGHTS CONFIRMED': {
        'direction': 'positive', 'severity': 'small', 'category': 'REG', 'confidence': 1.0,
    },
    'CANADIAN REGULATORS PROVIDE CLEARANCE': {
        'direction': 'positive', 'severity': 'medium', 'category': 'REG', 'confidence': 1.0,
    },
    'REGULATORS INDICATE REMEDIES FRAMEWORK IS ACCEPTABLE': {
        'direction': 'positive', 'severity': 'medium', 'category': 'REG', 'confidence': 1.0,
    },
    'UNCONDITIONAL REGULATORY APPROVAL GRANTED': {
        'direction': 'positive', 'severity': 'large', 'category': 'REG', 'confidence': 1.0,
    },
    'REGULATORY CLEARANCE OBTAINED': {
        'direction': 'positive', 'severity': 'large', 'category': 'REG', 'confidence': 1.0,
    },
    'EARLY TERMINATION OF HSR WAITING PERIOD': {
        'direction': 'positive', 'severity': 'large', 'category': 'REG', 'confidence': 1.0,
    },
    'SHAREHOLDER VOTE SCHEDULED': {
        'direction': 'positive', 'severity': 'small', 'category': 'SHR', 'confidence': 1.0,
    },
    'SHAREHOLDERS APPROVE MERGER': {
        'direction': 'positive', 'severity': 'large', 'category': 'SHR', 'confidence': 1.0,
    },
    'DEFINITIVE AGREEMENT SIGNED': {
        'direction': 'positive', 'severity': 'large', 'category': 'REG', 'confidence': 1.0,
    },
    'FINANCING COMMITMENT SECURED': {
        'direction': 'positive', 'severity': 'medium', 'category': 'FIN', 'confidence': 1.0,
    },
    'DEBT FINANCING COMMITTED': {
        'direction': 'positive', 'severity': 'medium', 'category': 'FIN', 'confidence': 1.0,
    },
    'COMPETING BID EMERGES': {
        'direction': 'positive', 'severity': 'large', 'category': 'ALT', 'confidence': 1.0,
    },
    'RIVAL BID ANNOUNCED': {
        'direction': 'positive', 'severity': 'large', 'category': 'ALT', 'confidence': 1.0,
    },
    'SUPERIOR PROPOSAL RECEIVED': {
        'direction': 'positive', 'severity': 'large', 'category': 'ALT', 'confidence': 1.0,
    },
    'SWEETENED OFFER ANNOUNCED': {
        'direction': 'positive', 'severity': 'large', 'category': 'ALT', 'confidence': 1.0,
    },
    'SETTLEMENT REACHED': {
        'direction': 'positive', 'severity': 'medium', 'category': 'REG', 'confidence': 1.0,
    },
    'LAWSUIT DISMISSED': {
        'direction': 'positive', 'severity': 'medium', 'category': 'REG', 'confidence': 1.0,
    },
    'CONVERTIBLE DEBT REFINANCED': {
        'direction': 'positive', 'severity': 'small', 'category': 'FIN', 'confidence': 1.0,
    },
    'OUTSIDE DATE EXTENDED': {
        'direction': 'positive', 'severity': 'small', 'category': 'REG', 'confidence': 1.0,
    },
    'STANDSTILL AGREEMENT REACHED': {
        'direction': 'positive', 'severity': 'small', 'category': 'SHR', 'confidence': 1.0,
    },
    'EMPLOYEE RETENTION AGREEMENTS SIGNED': {
        'direction': 'positive', 'severity': 'small', 'category': 'FIN', 'confidence': 1.0,
    },
    'INTEGRATION PLANNING ANNOUNCED': {
        'direction': 'positive', 'severity': 'small', 'category': 'FIN', 'confidence': 1.0,
    },
    'STRONG QUARTERLY RESULTS REPORTED': {
        'direction': 'positive', 'severity': 'medium', 'category': 'FIN', 'confidence': 1.0,
    },
    'SYNERGY ESTIMATES RAISED': {
        'direction': 'positive', 'severity': 'medium', 'category': 'FIN', 'confidence': 1.0,
    },
    'ISS RECOMMENDS APPROVAL': {
        'direction': 'positive', 'severity': 'medium', 'category': 'SHR', 'confidence': 1.0,
    },
    'GLASS LEWIS RECOMMENDS APPROVAL': {
        'direction': 'positive', 'severity': 'medium', 'category': 'SHR', 'confidence': 1.0,
    },
    'INTEREST RATE HEDGING STRATEGY IMPLEMENTED': {
        'direction': 'positive', 'severity': 'small', 'category': 'FIN', 'confidence': 1.0,
    },
    'ENHANCED LIQUIDITY FACILITY ARRANGED': {
        'direction': 'positive', 'severity': 'small', 'category': 'FIN', 'confidence': 1.0,
    },

    # === NEGATIVE templates ===
    'EUROPEAN COMMISSION OPENS PHASE II INVESTIGATION': {
        'direction': 'negative', 'severity': 'large', 'category': 'REG', 'confidence': 1.0,
    },
    'SELL-SIDE ANALYSTS ISSUE DIVERGENT VALUATION ASSESSMENTS': {
        'direction': 'negative', 'severity': 'medium', 'category': 'FIN', 'confidence': 1.0,
    },
    'CREDIT CONDITIONS DETERIORATE': {
        'direction': 'negative', 'severity': 'large', 'category': 'FIN', 'confidence': 1.0,
    },
    'DEPOSIT OUTFLOWS ACCELERATE': {
        'direction': 'negative', 'severity': 'large', 'category': 'FIN', 'confidence': 1.0,
    },
    'MATERIAL ADVERSE CHANGE ALLEGED': {
        'direction': 'negative', 'severity': 'large', 'category': 'REG', 'confidence': 1.0,
    },
    'ANTITRUST CONCERNS RAISED': {
        'direction': 'negative', 'severity': 'medium', 'category': 'REG', 'confidence': 1.0,
    },
    'SHAREHOLDER LAWSUIT FILED': {
        'direction': 'negative', 'severity': 'medium', 'category': 'REG', 'confidence': 1.0,
    },
    'CLASS ACTION COMPLAINT FILED': {
        'direction': 'negative', 'severity': 'medium', 'category': 'REG', 'confidence': 1.0,
    },
    'REGULATORY HURDLES IDENTIFIED': {
        'direction': 'negative', 'severity': 'medium', 'category': 'REG', 'confidence': 1.0,
    },
    'SECOND REQUEST ISSUED': {
        'direction': 'negative', 'severity': 'large', 'category': 'REG', 'confidence': 1.0,
    },
    'DEAL TERMINATION RISK INCREASES': {
        'direction': 'negative', 'severity': 'large', 'category': 'REG', 'confidence': 1.0,
    },
    'POISON PILL ADOPTED': {
        'direction': 'negative', 'severity': 'medium', 'category': 'SHR', 'confidence': 1.0,
    },
    'CREDIT DOWNGRADE ANNOUNCED': {
        'direction': 'negative', 'severity': 'medium', 'category': 'FIN', 'confidence': 1.0,
    },
    'REVENUE SHORTFALL REPORTED': {
        'direction': 'negative', 'severity': 'medium', 'category': 'FIN', 'confidence': 1.0,
    },
    'ENVIRONMENTAL IMPACT CONCERNS RAISED': {
        'direction': 'negative', 'severity': 'medium', 'category': 'REG', 'confidence': 1.0,
    },
    'INJUNCTION SOUGHT': {
        'direction': 'negative', 'severity': 'large', 'category': 'REG', 'confidence': 1.0,
    },
    'STRESS TEST CONCERNS EMERGE': {
        'direction': 'negative', 'severity': 'medium', 'category': 'FIN', 'confidence': 1.0,
    },
    'ACTIVIST INVESTOR OPPOSES DEAL': {
        'direction': 'negative', 'severity': 'medium', 'category': 'SHR', 'confidence': 1.0,
    },
    'DEAL SPREADS WIDEN': {
        'direction': 'negative', 'severity': 'medium', 'category': 'PRC', 'confidence': 1.0,
    },

    # === NEUTRAL/AMBIGUOUS templates ===
    'FEDERAL RESERVE STAFF HOLDS TECHNICAL WORKING SESSION': {
        'direction': 'ambiguous', 'severity': 'small', 'category': 'REG', 'confidence': 1.0,
    },
    'REGULATORY FILINGS SUBMITTED': {
        'direction': 'ambiguous', 'severity': 'small', 'category': 'REG', 'confidence': 1.0,
    },
    'COUNSEL PROVIDES UPDATED RISK FACTOR DISCLOSURE': {
        'direction': 'ambiguous', 'severity': 'small', 'category': 'SHR', 'confidence': 1.0,
    },
    'INDUSTRY CONFERENCE GENERATES TRANSACTION SPECULATION': {
        'direction': 'ambiguous', 'severity': 'small', 'category': 'SHR', 'confidence': 1.0,
    },
    'UNUSUAL INSTITUTIONAL TRADING ACTIVITY DETECTED': {
        'direction': 'ambiguous', 'severity': 'small', 'category': 'SHR', 'confidence': 1.0,
    },
    'MANAGEMENT PROVIDES UPDATED MARKET OUTLOOK': {
        'direction': 'ambiguous', 'severity': 'small', 'category': 'FIN', 'confidence': 1.0,
    },
    'BOARD DEFENDS TRANSACTION TERMS': {
        'direction': 'ambiguous', 'severity': 'small', 'category': 'SHR', 'confidence': 1.0,
    },
    'ANALYSIS PUBLISHED': {
        'direction': 'ambiguous', 'severity': 'small', 'category': 'FIN', 'confidence': 1.0,
    },
    'STRATEGIC REVIEW ANNOUNCED': {
        'direction': 'ambiguous', 'severity': 'small', 'category': 'SHR', 'confidence': 1.0,
    },
    'PREFERRED STOCK ISSUANCE ANNOUNCED': {
        'direction': 'ambiguous', 'severity': 'small', 'category': 'FIN', 'confidence': 1.0,
    },
    'COMMENT PERIOD EXTENDED': {
        'direction': 'ambiguous', 'severity': 'small', 'category': 'REG', 'confidence': 1.0,
    },
}


def lookup_news(headline: str) -> Optional[Dict]:
    """Tier 1: Look up headline in template table. Returns classification or None."""
    normalized = normalize_headline(headline)

    # Exact match
    if normalized in NEWS_TEMPLATES:
        return NEWS_TEMPLATES[normalized]

    # Substring match (template contained in headline, or headline in template)
    for template, classification in NEWS_TEMPLATES.items():
        if template in normalized or normalized in template:
            return classification

    return None


# =============================================================================
# NEWS CLASSIFIER V6
# =============================================================================
class NewsClassifier:
    """Multi-layer context-aware news classifier with Tier 1 lookup."""

    # FIX 4: Strong/weak sector differentiation for deal identification
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

    # FIX 5: Separate STRONG signal lists for instant high-confidence classification
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

    # Regular positive/negative words (Layer 3)
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
        'CLEARANCE', 'WELCOMES', 'BACKS',
        'SYNERG', 'ACCRETIVE', 'UPGRADE', 'OUTPERFORM',
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

    # FIX 6: Negation prefixes that flip word meaning
    NEGATION_PREFIXES = ['NO ', 'NOT ', 'WITHOUT ', "DON'T ", 'FAILS TO ', 'UNABLE TO ']

    NEGATION_CONTEXT = [
        ('OUTFLOW', 'ACCELERAT'),
        ('SHORTFALL', 'REVENUE'),
        ('CONCERN', 'RAISE'),
        ('RISK', 'INCREASE'),
        ('DOUBT', 'GROW'),
        ('OBSTACLE', 'EMERGE'),
        ('DECLINE', 'ACCELERAT'),
        ('LOSS', 'WIDEN'),
        ('MAC', 'QUESTION'),
    ]

    NEUTRAL_PHRASES = [
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

    # FIX 15: Complete category keywords from strategy spec
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

    # FIX 13: Complete severity indicators from strategy spec
    LARGE_WORDS = ['MAJOR', 'SIGNIFICANT', 'CRITICAL', 'DECISIVE', 'FUNDAMENTAL',
                   'BLOCK', 'TERMIN', 'CANCEL', 'FINAL', 'DEFINITIVE',
                   'UNCONDITIONAL', 'COMPLET', 'PHASE II', 'UNANIM', 'LANDMARK',
                   'COLLAPSE', 'TRANSFORM']
    MEDIUM_WORDS = ['IMPORTANT', 'NOTABLE', 'SUBSTANT', 'CONSIDER', 'MATERIAL',
                    'MEANINGFUL', 'PRELIMINARY', 'GROWING', 'REVISED', 'UPDATED',
                    'EMERGES', 'EXTENDS', 'MODERATE']

    def classify(self, headline: str, body: str = '') -> Dict[str, Any]:
        full_text = f"{headline} {body}".upper()
        headline_upper = headline.upper()
        body_upper = body.upper()  # FIX 8: separate body text

        # FIX 17: Tier 1 - lookup table (instant, high confidence)
        lookup_result = lookup_news(headline)
        if lookup_result is not None:
            deal_id = self._identify_deal(full_text)
            result = {
                'deal_id': deal_id,
                'direction': lookup_result['direction'],
                'severity': lookup_result['severity'],
                'category': lookup_result['category'],
                'confidence': lookup_result['confidence'],
                'tier': 1,
            }
            result['delta_p'] = self._compute_delta_p(
                deal_id, result['category'], result['direction'], result['severity'])
            return result

        # Tier 2: Keyword classifier fallback
        deal_id = self._identify_deal(full_text)
        category = self._identify_category(full_text)
        direction = self._identify_direction(headline_upper, body_upper, full_text)
        severity = self._identify_severity(full_text)
        delta_p = self._compute_delta_p(deal_id, category, direction, severity)

        return {
            'deal_id': deal_id, 'category': category,
            'direction': direction, 'severity': severity, 'delta_p': delta_p,
            'confidence': 0.7 if direction != 'ambiguous' else 0.0,
            'tier': 2,
        }

    def _identify_deal(self, text: str) -> Optional[str]:
        """FIX 3 & 4: Strong/weak sector scoring with threshold 3."""
        scores = {}
        for deal_id, kw in self.DEAL_KEYWORDS.items():
            score = 0
            for t in kw['tickers']:
                if t in text:
                    score += 10
            for n in kw['names']:
                if n in text:
                    score += 8
            # FIX 4: Strong sector keywords +3, weak sector +1
            for w in kw['strong_sector']:
                if w in text:
                    score += 3
            for w in kw['weak_sector']:
                if w in text:
                    score += 1
            if score > 0:
                scores[deal_id] = score
        if scores:
            best = max(scores, key=scores.get)
            # FIX 3: Threshold 3 (was 1) to avoid false positives
            if scores[best] >= 3:
                return best
        return None

    def _identify_category(self, text: str) -> str:
        scores = {}
        for cat, keywords in self.CATEGORY_KEYWORDS.items():
            s = sum(1 for kw in keywords if kw in text)
            if s > 0:
                scores[cat] = s
        return max(scores, key=scores.get) if scores else 'FIN'

    def _identify_direction(self, headline: str, body: str, full_text: str) -> str:
        """Multi-layer direction classification per strategy spec."""

        # Layer 1: Neutral/procedural filter
        for phrase in self.NEUTRAL_PHRASES:
            if phrase in headline:
                return 'ambiguous'

        # Layer 2: Strong signal phrases (FIX 5)
        strong_pos = sum(1 for p in self.STRONG_POSITIVE if p in headline)
        strong_neg = sum(1 for p in self.STRONG_NEGATIVE if p in headline)
        if strong_pos > 0 and strong_neg == 0:
            return 'positive'
        if strong_neg > 0 and strong_pos == 0:
            return 'negative'
        # If both > 0, fall through to word-level scoring

        # Layer 3: Word-level scoring with negation handling
        # FIX 6: Negation prefix handling
        # FIX 7: Headline words count at 2x weight
        pos_h = 0
        neg_h = 0
        for word in self.POSITIVE_WORDS:
            if word in headline:
                # Check if preceded by negation -> flip to negative
                if self._is_negated(headline, word):
                    neg_h += 2
                else:
                    pos_h += 2
        for word in self.NEGATIVE_WORDS:
            if word in headline:
                # Check if preceded by negation -> flip to positive
                if self._is_negated(headline, word):
                    pos_h += 2
                else:
                    neg_h += 2

        # Layer 4: Body fallback (only if headline is tied)
        # FIX 7: Body weight 1.0x (was 0.3x)
        # FIX 8: Search body-only text (was searching full_text = double-counting)
        if pos_h == neg_h:
            for word in self.POSITIVE_WORDS:
                if word in body:
                    if self._is_negated(body, word):
                        neg_h += 1
                    else:
                        pos_h += 1
            for word in self.NEGATIVE_WORDS:
                if word in body:
                    if self._is_negated(body, word):
                        pos_h += 1
                    else:
                        neg_h += 1

        # Layer 5: Negation context pairs (always negative)
        for trigger, modifier in self.NEGATION_CONTEXT:
            if trigger in full_text and modifier in full_text:
                neg_h += 2

        if pos_h > neg_h:
            return 'positive'
        elif neg_h > pos_h:
            return 'negative'
        return 'ambiguous'

    def _is_negated(self, text: str, word: str) -> bool:
        """FIX 6: Check if a word is preceded by a negation prefix."""
        idx = text.find(word)
        if idx < 0:
            return False
        prefix = text[max(0, idx - 12):idx]
        for neg in self.NEGATION_PREFIXES:
            if prefix.endswith(neg):
                return True
        return False

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
        # FIX 1: Uses corrected NEWS_IMPACT values
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

    def intrinsic_target_price(self, did: str, ap: float) -> float:
        """FIX 14: Compute intrinsic target price from analyst probability."""
        p = self.analyst_prob[did]
        K = self.deal_value(did, ap)
        V = self.standalone_values[did]
        return p * K + (1 - p) * V

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
# MAIN TRADING ENGINE V6
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
        self.tier1_hits = 0
        self.tier2_hits = 0
        self.start_time = None

        self.deal_targets = {did: 0 for did in DEALS}
        self.deal_last_trade_tick = {did: -100 for did in DEALS}

    def start(self):
        self.running = True
        self.start_time = datetime.now()

        log.info("=" * 70)
        log.info("  MERGER ARB TRADER V6 - LOOKUP TABLE + MULTI-LAYER CLASSIFIER")
        log.info("=" * 70)
        log.info(f"  NEWS_IMPACT: pos large={NEWS_IMPACT['positive']['large']}, "
                 f"neg large={NEWS_IMPACT['negative']['large']}")
        log.info(f"  BLEND: {1-MARKET_BLEND_WEIGHT:.0%} news / {MARKET_BLEND_WEIGHT:.0%} market")
        log.info(f"  COOLDOWN: {NEWS_COOLDOWN_TICKS} ticks | MIN_TRADE: {MIN_TRADE_SIZE}")
        log.info(f"  TEMPLATES: {len(NEWS_TEMPLATES)} known headlines")
        for did, d in DEALS.items():
            log.info(f"  {d['name']}: {d['structure']} p0={d['p0']:.0%} "
                     f"sens={DEAL_SENSITIVITY[did]:.2f}")
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

                # FIX 16: No new trades after tick 580 (was 600)
                if self.tick >= NO_NEW_TRADES_TICK:
                    # Still track news IDs but don't trade
                    if news_resp.ok:
                        for n in news_resp.json():
                            nid = n.get('news_id', 0)
                            if nid > self.last_news_id:
                                headline = n.get('headline', '')
                                log.info(f"  SKIP (t>={NO_NEW_TRADES_TICK}): {headline[:60]}")
                                self.last_news_id = max(self.last_news_id, nid)
                else:
                    # ACTIVE TRADING
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
        self.tier1_hits = 0
        self.tier2_hits = 0
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
            tier = c.get('tier', 2)

            tier_tag = f"T{tier}"
            tag = f"[{did}]" if did else "[???]"
            log.info(f"NEWS {tag} {tier_tag} {c['category']} {direction}/{c['severity']} "
                     f"dp={dp:+.4f}: {headline[:90]}")

            if tier == 1:
                self.tier1_hits += 1
            else:
                self.tier2_hits += 1

            if did and dp != 0:
                self._trade_on_news(did, c)

            self.last_news_id = max(self.last_news_id, nid)

    def _trade_on_news(self, did: str, classification: Dict):
        """Trade on news with all V6 fixes applied."""
        deal = DEALS[did]
        direction = classification['direction']
        dp = classification['delta_p']
        severity = classification['severity']
        category = classification['category']
        tier = classification.get('tier', 2)
        confidence = classification.get('confidence', 0.7)

        # FIX 9: Cooldown check (15 ticks, was 20)
        ticks_since = self.tick - self.deal_last_trade_tick[did]
        if ticks_since < NEWS_COOLDOWN_TICKS:
            log.info(f"  COOLDOWN: {did} traded {ticks_since} ticks ago, need {NEWS_COOLDOWN_TICKS}")
            self.prob.apply_news(did, dp)
            return

        old_p, new_p = self.prob.apply_news(did, dp)

        # FIX 2: Blend 70% news / 30% market (was 50/50)
        target = deal['target']
        acquirer = deal['acquirer']
        tp = self.prices.get(target, 0)
        ap = self.prices.get(acquirer, 0)
        if tp <= 0 or ap <= 0:
            return

        mp = self.prob.implied_prob(did, tp, ap)
        if mp is not None:
            blended = (1 - MARKET_BLEND_WEIGHT) * self.prob.analyst_prob[did] + \
                      MARKET_BLEND_WEIGHT * mp
            self.prob.analyst_prob[did] = blended
            new_p = blended

        K = self.prob.deal_value(did, ap)
        V = self.prob.standalone_values[did]

        # FIX 14: Check intrinsic price mispricing before trading
        intrinsic = self.prob.intrinsic_target_price(did, ap)
        mispricing = intrinsic - tp
        if abs(mispricing) < MIN_MISPRICING:
            log.info(f"  SKIP MISPRICING: {did} intrinsic=${intrinsic:.2f} "
                     f"market=${tp:.2f} gap=${mispricing:+.2f} < ${MIN_MISPRICING}")
            return

        # Position sizing: Tier 1 gets full size, Tier 2 gets base size
        if tier == 1:
            desired_size = TRADE_SIZE.get(severity, 5000)
            # Tier 1 gets a confidence boost
            desired_size = min(MAX_DEAL_POSITION, int(desired_size * 1.2))
        else:
            desired_size = TRADE_SIZE.get(severity, 5000)

        # FIX 10: Category boost 1.3x (was 1.5x)
        if category in ('REG', 'ALT'):
            desired_size = min(MAX_DEAL_POSITION, int(desired_size * 1.3))

        current_pos = int(self.positions.get(target, 0))

        # FIX 11: 4-level market sanity check (was 2-level)
        if mp is not None:
            if direction == 'positive' and mp > 0.90:
                log.info(f"  SANITY: {did} skip BUY - mktP={mp:.1%} > 90% (limited upside)")
                return
            if direction == 'negative' and mp < 0.10:
                log.info(f"  SANITY: {did} skip SELL - mktP={mp:.1%} < 10% (limited downside)")
                return
            if direction == 'negative' and mp > 0.80:
                log.warning(f"  SANITY: {did} skip SELL - mktP={mp:.1%} > 80% (market disagrees)")
                return
            if direction == 'positive' and mp < 0.20:
                log.warning(f"  SANITY: {did} skip BUY - mktP={mp:.1%} < 20% (market disagrees)")
                return

        if direction == 'positive':
            room_in_deal = max(0, MAX_DEAL_POSITION - current_pos)
            trade_qty = min(desired_size, room_in_deal)
            action = 'BUY'
        else:
            room_in_deal = max(0, MAX_DEAL_POSITION + current_pos)
            trade_qty = min(desired_size, room_in_deal)
            action = 'SELL'

        # FIX 12: Minimum trade size 500 (was 100)
        if trade_qty < MIN_TRADE_SIZE:
            log.info(f"  DEAL LIMIT: {did} {action} pos={current_pos} "
                     f"room={room_in_deal} < {MIN_TRADE_SIZE}")
            return

        avail = self._available_room(target, action)
        actual = min(trade_qty, avail)
        if actual < MIN_TRADE_SIZE:
            log.info(f"  NO ROOM: {did} {action} wanted={trade_qty} avail={avail}")
            return

        expected_move = dp * (K - V)
        log.info(f"  TRADE: {did} T{tier} {direction} dp={dp:+.3f} "
                 f"p={old_p:.1%}->{new_p:.1%} intrinsic=${intrinsic:.2f} "
                 f"mkt=${tp:.2f} gap=${mispricing:+.2f} "
                 f"exp=${expected_move:+.2f} size={actual} (wanted {desired_size})")

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
        self.deal_last_trade_tick[did] = self.tick

    def _available_room(self, ticker: str, action: str) -> int:
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

                accept = False
                if action == 'BUY' and price > mp * 1.001:
                    accept = True
                elif action == 'SELL' and price < mp * 0.999:
                    accept = True

                if accept:
                    log.info(f"TENDER: Accept {action} {qty} {ticker} @ ${price:.2f} "
                             f"(mkt ${mp:.2f})")
                    try:
                        self.session.post(f'{API_BASE}/tenders/{tid}', timeout=5)
                    except:
                        pass
        except:
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
                 f"News: {self.news_traded} (T1:{self.tier1_hits} T2:{self.tier2_hits})")

        for did, deal in DEALS.items():
            tp = self.prices.get(deal['target'], 0)
            ap = self.prices.get(deal['acquirer'], 0)
            our_p = self.prob.analyst_prob[did]
            mp = self.prob.implied_prob(did, tp, ap)
            K = self.prob.deal_value(did, ap) if ap > 0 else 0
            intrinsic = self.prob.intrinsic_target_price(did, ap) if ap > 0 else 0
            t_pos = int(self.positions.get(deal['target'], 0))
            a_pos = int(self.positions.get(deal['acquirer'], 0))
            mp_str = f"{mp:.1%}" if mp is not None else "N/A"
            gap = intrinsic - tp if tp > 0 else 0

            log.info(f"  {did}: T=${tp:.2f} A=${ap:.2f} K=${K:.2f} "
                     f"ourP={our_p:.1%} mktP={mp_str} "
                     f"intrin=${intrinsic:.2f} gap=${gap:+.2f} "
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
