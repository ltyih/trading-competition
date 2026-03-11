# -*- coding: utf-8 -*-
"""
MERGER ARBITRAGE TRADING SYSTEM V8 - *REMOVED* 2026
================================================
V8 FIXES from V7 (which LOST money: $-17K final NLV):

ROOT CAUSE of V7 failure (empirically proven from 2 heats):
  - Same headline targets DIFFERENT deals across heats (67% change rate)
  - Direction depends on WHICH DEAL is affected (50% flip rate)
  - Category is the only stable field (100% stable across heats)
  - V7's lookup table averaged opposite signals → "ambiguous" → skipped trades
  - V7 removed all guardrails → amplified wrong-direction trades to max size

V8 ARCHITECTURE:
  1. Lookup table stores CATEGORY + SEVERITY only (stable fields)
  2. Direction determined from BODY TEXT keywords (per-deal, per-heat)
  3. Deal identification from body text tickers/names + keyword classifier
  4. Restored sanity checks (market probability agreement)
  5. Restored moderate cooldown (5 ticks per deal)
  6. Restored position reversal on mispricing flip
  7. Sensible position sizing (500-15000, severity-based)

PRICE TIMING (empirically measured):
  - News arrives at tick T
  - Price is UNCHANGED at tick T (we can trade at pre-move price!)
  - Price starts moving at T+3 to T+5
  - Full move completes by T+6 to T+10
  - We have a 3-5 tick window to trade BEFORE market adjusts
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

NEWS_IMPACT = {
    'positive': {'small': 0.03, 'medium': 0.07, 'large': 0.14},
    'negative': {'small': -0.04, 'medium': -0.09, 'large': -0.18},
}

GROSS_LIMIT = 100000
NET_LIMIT = 50000
MAX_ORDER_SIZE = 5000
POLL_INTERVAL = 0.15

MAX_DEAL_POSITION = 15000
DEAL_SENSITIVITY = {'D1': 1.00, 'D2': 1.05, 'D3': 1.10, 'D4': 1.30, 'D5': 1.15}

# V8: Restored moderate cooldown (per-deal, 5 ticks)
NEWS_COOLDOWN_TICKS = 5

# V8: Moderate mispricing threshold (not too tight, not too loose)
MIN_MISPRICING = 0.10

# V8: 80% news / 20% market (respect market signal somewhat)
MARKET_BLEND_WEIGHT = 0.20

TRADE_SIZE = {
    'large': 15000,
    'medium': 10000,
    'small': 5000,
}

# V8: Min trade size back to 500 (stop spraying tiny orders)
MIN_TRADE_SIZE = 500

# V8: Stop trading at 590 (leave margin for close-out)
NO_NEW_TRADES_TICK = 590

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
# V8.1: MASTER LOOKUP INTEGRATION (15 heats, 1934 observations)
# =============================================================================
# Strategy:
#   Tier 0: MASTER_LOOKUP entry with n>=6 AND (|dp|>0.015 OR n_clean>=4)
#           → Trust direction from 15-heat average (reliable)
#   Tier 1: MASTER_LOOKUP entry with n>=3 but direction unreliable
#           → Use category+severity from ML, body text for direction
#   Tier 2: Hardcoded HEADLINE_META (for headlines not in ML)
#           → Use body_hint for direction
#   Tier 3: Unknown headline → keyword classifier + body text

import os as _os
import importlib.util as _importlib_util

ALL_TICKERS = ['TGX', 'PHR', 'BYL', 'CLD', 'GGD', 'PNR', 'FSR', 'ATB', 'SPK', 'EEC']
ALL_COMPANY_NAMES = [
    'TARGENIX', 'PHARMACO', 'BYTELAYER', 'CLOUDSYS', 'GREENGRID',
    'PETRONORTH', 'FINSURE', 'ATLAS BANK', 'ATLAS', 'SOLARPEAK',
    'EASTENERGY', 'EAST ENERGY',
]


def normalize_headline(headline: str) -> str:
    """Strip tickers, company names, and normalize for matching."""
    text = headline.upper().strip()
    for ticker in ALL_TICKERS:
        text = text.replace(ticker, 'TICKER')
    for name in ALL_COMPANY_NAMES:
        text = text.replace(name, 'COMPANY')
    text = ' '.join(text.split())
    return text


def _load_master_lookup() -> Dict:
    """Load MASTER_LOOKUP.py from same directory."""
    script_dir = _os.path.dirname(_os.path.abspath(__file__))
    lookup_path = _os.path.join(script_dir, 'MASTER_LOOKUP.py')
    if not _os.path.exists(lookup_path):
        return {}
    try:
        spec = _importlib_util.spec_from_file_location('master_lookup', lookup_path)
        mod = _importlib_util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, 'MASTER_LOOKUP', {})
    except Exception as e:
        print(f"Warning: Failed to load MASTER_LOOKUP.py: {e}")
        return {}


_MASTER_LOOKUP_RAW = _load_master_lookup()

# Pre-classify each ML entry into trust tiers
_ML_TIER0 = {}  # Trust direction (n>=6, reliable)
_ML_TIER1 = {}  # Trust meta only (n>=3, direction unreliable)

for _k, _v in _MASTER_LOOKUP_RAW.items():
    _n = _v.get('n_obs', 0)
    _nc = _v.get('n_clean', 0)
    _dp = abs(_v.get('avg_dp', 0))
    # Tier 0: Trust direction ONLY for strong, well-observed signals
    # n>=6 observations AND |avg_dp|>0.03 (consistently one direction)
    if _n >= 6 and _dp > 0.03:
        _ML_TIER0[_k] = _v
    elif _n >= 3:
        _ML_TIER1[_k] = _v


def lookup_master(headline: str) -> Optional[Tuple[Dict, int]]:
    """Look up headline in MASTER_LOOKUP. Returns (entry, tier) or None.
    tier=0 means trust direction, tier=1 means trust meta only."""
    normalized = normalize_headline(headline)

    # Exact match - Tier 0
    if normalized in _ML_TIER0:
        return _ML_TIER0[normalized], 0
    # Exact match - Tier 1
    if normalized in _ML_TIER1:
        return _ML_TIER1[normalized], 1

    # Substring match - Tier 0
    for template, entry in _ML_TIER0.items():
        if template in normalized or normalized in template:
            return entry, 0
    # Substring match - Tier 1
    for template, entry in _ML_TIER1.items():
        if template in normalized or normalized in template:
            return entry, 1

    return None


# Hardcoded fallback for headlines not in MASTER_LOOKUP
# body_hint: 'inherent_positive', 'inherent_negative', 'body_dependent', 'ambiguous'
HEADLINE_META = {
    # ============================================================
    # INHERENTLY POSITIVE (always positive for the affected deal)
    # ============================================================
    # Regulatory clearances
    'FTC CLEARS TRANSACTION - EARLY TERMINATION': {
        'category': 'REG', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'UNCONDITIONAL REGULATORY APPROVAL GRANTED': {
        'category': 'REG', 'severity': 'large', 'body_hint': 'inherent_positive',
    },
    'REGULATORY CLEARANCE OBTAINED': {
        'category': 'REG', 'severity': 'large', 'body_hint': 'inherent_positive',
    },
    'EARLY TERMINATION OF HSR WAITING PERIOD': {
        'category': 'REG', 'severity': 'large', 'body_hint': 'inherent_positive',
    },
    'CANADIAN REGULATORS PROVIDE CLEARANCE': {
        'category': 'REG', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'REGULATORS INDICATE REMEDIES FRAMEWORK IS ACCEPTABLE': {
        'category': 'REG', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'DOJ PROVIDES CONDITIONAL CLEARANCE': {
        'category': 'REG', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'DOJ ANTITRUST CLEARANCE GRANTED': {
        'category': 'REG', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'FERC GRANTS EXPEDITED APPROVAL': {
        'category': 'REG', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'FEDERAL JUDGE DENIES STATE AG INJUNCTION': {
        'category': 'REG', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'UK CMA PROVISIONALLY CLEARS DEAL': {
        'category': 'REG', 'severity': 'large', 'body_hint': 'inherent_positive',
    },
    'UK CMA PHASE I REVIEW COMPLETED': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'inherent_positive',
    },
    'STATE BANKING REGULATORS PROVIDE CLEARANCE': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'inherent_positive',
    },
    'ISS STRONGLY RECOMMENDS APPROVAL': {
        'category': 'SHR', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'GLASS LEWIS RECOMMENDS APPROVAL': {
        'category': 'SHR', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'ISS RECOMMENDS APPROVAL': {
        'category': 'SHR', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    # Shareholder approval
    'SHAREHOLDERS APPROVE MERGER': {
        'category': 'SHR', 'severity': 'large', 'body_hint': 'inherent_positive',
    },
    'EARLY VOTE RESULTS SHOW STRONG SUPPORT': {
        'category': 'SHR', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'SHAREHOLDER VOTE SCHEDULED': {
        'category': 'SHR', 'severity': 'small', 'body_hint': 'inherent_positive',
    },
    # Financing secured
    'PROJECT FINANCE COMMITMENT SECURED': {
        'category': 'FIN', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'FINANCING COMMITMENT SECURED': {
        'category': 'FIN', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'DEBT FINANCING COMMITTED': {
        'category': 'FIN', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'INVESTMENT GRADE BOND OFFERING COMPLETED': {
        'category': 'FIN', 'severity': 'large', 'body_hint': 'inherent_positive',
    },
    'SENIOR NOTES OFFERING SUCCESSFUL': {
        'category': 'FIN', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'PERMANENT FINANCING SYNDICATION SUCCESSFUL': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'inherent_positive',
    },
    'EQUITY CO-INVESTMENT SECURED': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'inherent_positive',
    },
    # Competing bids (always positive for target)
    'COMPETING BID EMERGES': {
        'category': 'ALT', 'severity': 'large', 'body_hint': 'inherent_positive',
    },
    'RIVAL BID ANNOUNCED': {
        'category': 'ALT', 'severity': 'large', 'body_hint': 'inherent_positive',
    },
    'SUPERIOR PROPOSAL RECEIVED': {
        'category': 'ALT', 'severity': 'large', 'body_hint': 'inherent_positive',
    },
    'SWEETENED OFFER ANNOUNCED': {
        'category': 'ALT', 'severity': 'large', 'body_hint': 'inherent_positive',
    },
    # Legal resolution
    'SETTLEMENT REACHED': {
        'category': 'REG', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'LAWSUIT DISMISSED': {
        'category': 'REG', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    # Opposition = positive in M&A (potential higher bid)
    'ACTIVIST HEDGE FUND OPPOSES DEAL': {
        'category': 'SHR', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'INFRASTRUCTURE FUND OPPOSES TERMS': {
        'category': 'SHR', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    # Other positive
    'REVERSE TERMINATION FEE INCREASED': {
        'category': 'FIN', 'severity': 'large', 'body_hint': 'inherent_positive',
    },
    'COMPANY ENGAGES ADVISORS': {
        'category': 'SHR', 'severity': 'large', 'body_hint': 'inherent_positive',
    },
    'CONVERTIBLE NOTE HOLDERS SEEK CLARITY': {
        'category': 'FIN', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'SHARE BUYBACK PROGRAM ANNOUNCED': {
        'category': 'FIN', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'SYNERGY ESTIMATES RAISED': {
        'category': 'FIN', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'COMPANY BOARD REAFFIRMS SUPPORT': {
        'category': 'SHR', 'severity': 'small', 'body_hint': 'inherent_positive',
    },
    'TERMINATION FEE STRUCTURE': {
        'category': 'FIN', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'RATE CASE PROCEEDINGS INITIATED': {
        'category': 'FIN', 'severity': 'large', 'body_hint': 'inherent_positive',
    },
    'CLASS ACTION FILED IN DELAWARE': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'inherent_positive',
    },
    'SHAREHOLDER DERIVATIVE SUIT FILED': {
        'category': 'SHR', 'severity': 'small', 'body_hint': 'inherent_positive',
    },
    'STATE AG COALITION FILES LAWSUIT': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'inherent_positive',
    },
    'CONGRESSIONAL HEARING ANNOUNCED': {
        'category': 'REG', 'severity': 'large', 'body_hint': 'inherent_positive',
    },
    'MATERIAL BREACH OF MERGER COVENANT ALLEGED': {
        'category': 'FIN', 'severity': 'large', 'body_hint': 'inherent_positive',
    },
    'POLITICAL OPPOSITION VOICED': {
        'category': 'FIN', 'severity': 'large', 'body_hint': 'inherent_positive',
    },
    'POLITICAL OPPOSITION EMERGES FROM FOSSIL FUEL-DEPENDENT STATES': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'inherent_positive',
    },
    'RENEWABLE ENERGY CREDIT TRANSFER REVIEW': {
        'category': 'REG', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'PROJECT DELAYS RAISE MAC CONCERNS': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'inherent_positive',
    },
    'PROXY ADVISORY FIRMS ISSUE DIVIDED RECOMMENDATIONS': {
        'category': 'SHR', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },
    'TERMINATION FEE SET AT': {
        'category': 'FIN', 'severity': 'medium', 'body_hint': 'inherent_positive',
    },

    # ============================================================
    # INHERENTLY NEGATIVE (always negative for the affected deal)
    # ============================================================
    'FTC COMMISSIONER DISSENT PUBLISHED': {
        'category': 'REG', 'severity': 'large', 'body_hint': 'inherent_negative',
    },
    'EUROPEAN COMMISSION OPENS PHASE II INVESTIGATION': {
        'category': 'REG', 'severity': 'medium', 'body_hint': 'inherent_negative',
    },
    'SECOND REQUEST ISSUED': {
        'category': 'REG', 'severity': 'large', 'body_hint': 'inherent_negative',
    },
    'DEAL TERMINATION RISK INCREASES': {
        'category': 'REG', 'severity': 'large', 'body_hint': 'inherent_negative',
    },
    'CREDIT CONDITIONS DETERIORATE': {
        'category': 'FIN', 'severity': 'large', 'body_hint': 'inherent_negative',
    },
    'DEPOSIT OUTFLOWS ACCELERATE': {
        'category': 'FIN', 'severity': 'large', 'body_hint': 'inherent_negative',
    },
    'MATERIAL ADVERSE CHANGE ALLEGED': {
        'category': 'REG', 'severity': 'large', 'body_hint': 'inherent_negative',
    },
    'INJUNCTION SOUGHT': {
        'category': 'REG', 'severity': 'large', 'body_hint': 'inherent_negative',
    },
    'MIXED ECONOMIC DATA CREATES CROSS-CURRENTS FOR DEAL MARKETS': {
        'category': 'FIN', 'severity': 'medium', 'body_hint': 'inherent_negative',
    },
    'FORCE MAJEURE EVENT RAISES CONCERNS': {
        'category': 'REG', 'severity': 'medium', 'body_hint': 'inherent_negative',
    },
    'FED ANNOUNCES ENHANCED REVIEW PROCESS': {
        'category': 'REG', 'severity': 'medium', 'body_hint': 'inherent_negative',
    },
    'CFIUS NATIONAL SECURITY REVIEW INITIATED': {
        'category': 'REG', 'severity': 'medium', 'body_hint': 'inherent_negative',
    },

    # ============================================================
    # BODY-DEPENDENT (need body text to determine direction)
    # ============================================================
    'CONGRESSIONAL BANKING COMMITTEE INQUIRY': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'FERC STAFF HOLDS TECHNICAL WORKING SESSION': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'IMPROVED FINANCIAL DISCLOSURES': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'INDUSTRY CONFERENCE GENERATES TRANSACTION SPECULATION': {
        'category': 'SHR', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'OUTSIDE DATE EXTENDED': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'OUTSIDE DATE EXTENDED TO YEAR-END': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'DOJ REQUESTS EXTENDED REVIEW': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'BRIDGE LOAN REPLACED WITH TERM FACILITY': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'CREDIT FACILITY AMENDMENT NEGOTIATED': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'CREDIT RATING DOWNGRADE': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'BANK SYNDICATE EXPANDS': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'EXPECTED TIMELINE REVISED': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'CLOSING CONDITIONS STATUS': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'CLOSING CONDITIONS STATUS UPDATE': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'EXCHANGE RATIO COLLAR DISCUSSED': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'REGULATORY APPLICATIONS FILED': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'REGULATORY TIMELINE SLIPPAGE': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'INTEGRATION TEAMS FORMED': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'COMPANY REPORTS STRONG REVENUE GROWTH': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'COMPANY SETS SHAREHOLDER MEETING DATE': {
        'category': 'SHR', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'INDUSTRY LOBBYISTS SUPPORT TRANSACTION': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'INSTITUTIONAL INVESTORS SIGNAL SUPPORT': {
        'category': 'SHR', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'FERC NOTIFICATION FILED': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'SUPPLEMENTAL ENVIRONMENTAL REVIEW': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'ANTITRUST EXPERTS RAISE COMPETITION CONCERNS': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'MAJOR RATING AGENCY REVISES SECTOR OUTLOOKS DOWNWARD': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'RENEWABLE ENERGY SECTOR SELL-OFF': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'body_dependent',
    },
    'SECTOR MULTIPLE COMPRESSION': {
        'category': 'PRC', 'severity': 'small', 'body_hint': 'body_dependent',
    },

    # ============================================================
    # AMBIGUOUS (no reliable directional signal)
    # ============================================================
    'SELL-SIDE ANALYSTS ISSUE DIVERGENT VALUATION ASSESSMENTS': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'ambiguous',
    },
    'CLOSED-DOOR MEETING BETWEEN TRANSACTION PRINCIPALS REPORTED': {
        'category': 'SHR', 'severity': 'small', 'body_hint': 'ambiguous',
    },
    'UNUSUAL INSTITUTIONAL TRADING ACTIVITY OBSERVED IN TICKER': {
        'category': 'SHR', 'severity': 'small', 'body_hint': 'ambiguous',
    },
    'COUNSEL PROVIDES UPDATED RISK FACTOR DISCLOSURE': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'ambiguous',
    },
    'THIRD-PARTY REGULATORY ANALYSIS PUBLISHED': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'ambiguous',
    },
    'COMPANY BOARD DEFENDS TRANSACTION': {
        'category': 'SHR', 'severity': 'small', 'body_hint': 'ambiguous',
    },
    'VOTE TRACKING SHOWS MAJORITY SUPPORT': {
        'category': 'SHR', 'severity': 'small', 'body_hint': 'ambiguous',
    },
    'STRESS TEST REQUIREMENTS IMPOSED': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'ambiguous',
    },
    'DEFINITIVE AGREEMENT EXECUTED': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'ambiguous',
    },
    'DEFINITIVE AGREEMENT SIGNED': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'ambiguous',
    },
    'MERGER AGREEMENT SIGNED AND ANNOUNCED': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'ambiguous',
    },
    'INTERCONNECTION RIGHTS CONFIRMED': {
        'category': 'REG', 'severity': 'small', 'body_hint': 'ambiguous',
    },
    'CONVERTIBLE DEBT REFINANCED': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'ambiguous',
    },
    'WELCOME TO THE *REMOVED* 2026 MERGER ARBITRAGE CASE - PRACTICE SERVER': {
        'category': 'FIN', 'severity': 'small', 'body_hint': 'ambiguous',
    },
}


def lookup_headline_meta(headline: str) -> Optional[Dict]:
    """Look up category/severity metadata for a headline. Returns None if unknown."""
    normalized = normalize_headline(headline)

    # Exact match
    if normalized in HEADLINE_META:
        return HEADLINE_META[normalized]

    # Substring match
    for template, meta in HEADLINE_META.items():
        if template in normalized or normalized in template:
            return meta

    return None


# =============================================================================
# V8: BODY TEXT DIRECTION CLASSIFIER
# Determines direction from body text keywords (not headline)
# =============================================================================

# Words in body text that reliably indicate POSITIVE direction for the affected deal
BODY_POSITIVE_WORDS = [
    'CLEARS', 'CLEARED', 'CLEARANCE', 'APPROVAL', 'APPROVED', 'FAVORABLE',
    'SUPPORT', 'RECOMMEND', 'SECURED', 'SUCCESSFUL', 'COMPLETED',
    'EXPEDITED', 'DISMISSED', 'RESOLVED', 'SETTLED', 'WAIVED',
    'NO OBJECTION', 'CONSTRUCTIVE', 'PROGRESS', 'ON TRACK',
    'STRONG SUPPORT', 'OVERWHELMING', 'UNANIM',
    'NO SIGNIFICANT COMPETITIVE CONCERNS',
    'COMMITMENT', 'COMMITTED', 'ROBUST',
    'INCREASED BID', 'HIGHER BID', 'SWEETENED',
    'OVER-SUBSCRIPTION', 'OVERSUBSCRI',
    'GREEN LIGHT', 'SMOOTH',
    'ACCELERAT',  # accelerated timeline = positive
    'SYNERG',
    'ACCRETIVE',
]

# Words in body text that reliably indicate NEGATIVE direction
BODY_NEGATIVE_WORDS = [
    'CONCERN', 'CONCERNS RAISED', 'CITING CONCERN',
    'DELAY', 'DELAYED', 'SLIPPAGE',
    'BLOCK', 'BLOCKED',
    'OPPOSE', 'OPPOSED', 'OPPOSITION',
    'REJECT', 'REJECTED',
    'DETERIORAT',
    'DECLINE', 'DECLINING',
    'SHORTFALL',
    'ADVERSE', 'MATERIAL ADVERSE',
    'SCRUTIN',
    'EXTENDED REVIEW', 'ADDITIONAL REVIEW',
    'PHASE II', 'IN-DEPTH INVESTIGATION',
    'SECOND REQUEST',
    'DISSENT', 'DISSENTING',
    'OBSTACLE',
    'CHALLENGED', 'CHALLENGING',
    'DOWNGRADE', 'REVIEW FOR POSSIBLE DOWNGRADE',
    'LEVERAGE CONCERN',
    'OUTFLOW',
    'FAILS TO', 'UNABLE TO',
    'AT RISK', 'RISK INCREASE', 'RISK FACTOR',
    'COMPLEXITY',
    'WIDEN', 'WIDENING',
    'SELL-OFF', 'SELLOFF',
    'COMPRESSION',
    'NEGATIVE OUTLOOK',
]


def classify_body_direction(body: str) -> str:
    """Determine direction from body text keywords."""
    body_upper = body.upper()

    pos_score = 0
    neg_score = 0

    for word in BODY_POSITIVE_WORDS:
        if word in body_upper:
            pos_score += 1

    for word in BODY_NEGATIVE_WORDS:
        if word in body_upper:
            neg_score += 1

    if pos_score > neg_score:
        return 'positive'
    elif neg_score > pos_score:
        return 'negative'
    return 'ambiguous'


# =============================================================================
# NEWS CLASSIFIER V8
# =============================================================================
class NewsClassifier:
    """V8 classifier: metadata from headline, direction from body text."""

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
                              'RENEWABLE ENERGY', 'PUC'],
            'weak_sector': ['SOLAR', 'RENEWABLE', 'WIND', 'CLEAN ENERGY',
                            'TURBINE', 'PANEL', 'GENERATION'],
        },
    }

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
        body_upper = body.upper()

        # Step 1: Identify the deal from body + headline
        deal_id = self._identify_deal(full_text)

        # Step 2: Try MASTER_LOOKUP first (15 heats of data)
        ml_result = lookup_master(headline)
        if ml_result is not None:
            entry, ml_tier = ml_result

            category = entry.get('category', 'FIN')
            severity = entry.get('severity', 'small')

            if ml_tier == 0:
                # Tier 0: Trust direction from 15-heat average
                direction = entry.get('direction', 'ambiguous')
                # Use the actual avg_dp magnitude for better sizing
                raw_dp = entry.get('avg_dp', 0)
                if direction == 'ambiguous' or abs(raw_dp) < 0.003:
                    direction = 'ambiguous'
                    delta_p = 0.0
                else:
                    # Scale dp by deal sensitivity
                    deal_sens = DEAL_SENSITIVITY.get(deal_id, 1.0) if deal_id else 1.0
                    delta_p = round(raw_dp * deal_sens, 4)

                return {
                    'deal_id': deal_id, 'direction': direction,
                    'severity': severity, 'category': category,
                    'confidence': min(1.0, entry.get('confidence', 0.8)),
                    'tier': 0, 'delta_p': delta_p,
                }

            else:
                # Tier 1: Trust category+severity, use body text for direction
                # But use ML direction as tiebreaker / confirmation
                body_dir = classify_body_direction(body)
                ml_dir = entry.get('direction', 'ambiguous')
                ml_dp = entry.get('avg_dp', 0)

                if body_dir != 'ambiguous':
                    # Body text has an opinion
                    if ml_dir != 'ambiguous' and body_dir != ml_dir and abs(ml_dp) > 0.01:
                        # Body and ML disagree, ML has mild signal → ambiguous
                        direction = 'ambiguous'
                    else:
                        direction = body_dir
                elif ml_dir != 'ambiguous' and abs(ml_dp) > 0.01:
                    # Body is ambiguous, but ML has a mild signal → use ML
                    direction = ml_dir
                else:
                    direction = 'ambiguous'

                delta_p = self._compute_delta_p(deal_id, category, direction, severity)
                return {
                    'deal_id': deal_id, 'direction': direction,
                    'severity': severity, 'category': category,
                    'confidence': 0.6 if direction != 'ambiguous' else 0.0,
                    'tier': 1, 'delta_p': delta_p,
                }

        # Step 3: Try hardcoded HEADLINE_META fallback
        meta = lookup_headline_meta(headline)
        if meta is not None:
            category = meta['category']
            severity = meta['severity']
            hint = meta['body_hint']

            if hint == 'inherent_positive':
                direction = 'positive'
            elif hint == 'inherent_negative':
                direction = 'negative'
            elif hint == 'ambiguous':
                direction = 'ambiguous'
            elif hint == 'body_dependent':
                direction = classify_body_direction(body)
            else:
                direction = 'ambiguous'

            delta_p = self._compute_delta_p(deal_id, category, direction, severity)
            return {
                'deal_id': deal_id, 'direction': direction,
                'severity': severity, 'category': category,
                'confidence': 0.7 if direction != 'ambiguous' else 0.0,
                'tier': 2, 'delta_p': delta_p,
            }

        # Tier 3: Unknown headline — keyword classifier + body text
        category = self._identify_category(full_text)
        severity = self._identify_severity(full_text)
        direction = classify_body_direction(body)
        if direction == 'ambiguous':
            direction = self._headline_keyword_direction(headline_upper, body_upper)

        delta_p = self._compute_delta_p(deal_id, category, direction, severity)
        return {
            'deal_id': deal_id, 'category': category,
            'direction': direction, 'severity': severity, 'delta_p': delta_p,
            'confidence': 0.4 if direction != 'ambiguous' else 0.0,
            'tier': 3,
        }

    def _identify_deal(self, text: str) -> Optional[str]:
        """Strong/weak sector scoring with threshold 3."""
        scores = {}
        for deal_id, kw in self.DEAL_KEYWORDS.items():
            score = 0
            for t in kw['tickers']:
                if t in text:
                    score += 10
            for n in kw['names']:
                if n in text:
                    score += 8
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

    def _headline_keyword_direction(self, headline: str, body: str) -> str:
        """Fallback: simple keyword direction from headline for unknown headlines."""
        # Strong positive headline patterns
        STRONG_POS = [
            'CLEARS', 'CLEARED', 'CLEARANCE', 'APPROVAL', 'APPROVED',
            'COMPETING BID', 'RIVAL BID', 'SWEETENED',
            'FINANCING SECURED', 'COMMITMENT SECURED',
            'SETTLEMENT', 'DISMISSED',
            'OPPOSES DEAL', 'OPPOSES TERMS', 'ACTIVIST',
            'REVERSE TERMINATION', 'INVESTMENT GRADE',
            'EXPEDIT',
        ]
        STRONG_NEG = [
            'BLOCK', 'BLOCKED', 'TERMIN', 'CANCEL',
            'PHASE II', 'SECOND REQUEST', 'EXTENDED REVIEW',
            'INJUNCTION', 'POISON PILL',
            'MATERIAL ADVERSE', 'DETERIORAT',
            'COLLAPSE', 'WALK AWAY',
        ]

        pos = sum(1 for p in STRONG_POS if p in headline)
        neg = sum(1 for p in STRONG_NEG if p in headline)

        if pos > neg:
            return 'positive'
        elif neg > pos:
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

    def intrinsic_target_price(self, did: str, ap: float) -> float:
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
# MAIN TRADING ENGINE V8
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
        log.info("  MERGER ARB TRADER V8.1 - MASTER LOOKUP (15 HEATS) + BODY TEXT")
        log.info("=" * 70)
        log.info(f"  MASTER_LOOKUP: {len(_MASTER_LOOKUP_RAW)} entries "
                 f"(T0={len(_ML_TIER0)} trusted, T1={len(_ML_TIER1)} meta-only)")
        log.info(f"  HEADLINE_META: {len(HEADLINE_META)} fallback entries")
        log.info(f"  BLEND: {1-MARKET_BLEND_WEIGHT:.0%} news / {MARKET_BLEND_WEIGHT:.0%} market")
        log.info(f"  COOLDOWN: {NEWS_COOLDOWN_TICKS} | MIN_TRADE: {MIN_TRADE_SIZE} "
                 f"| MIN_MISPRICE: ${MIN_MISPRICING} | NO_TRADE_AFTER: {NO_NEW_TRADES_TICK}")
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

                if self.tick >= NO_NEW_TRADES_TICK:
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

                    # V8: Check for position reversals (every 10 ticks)
                    if self.tick % 10 == 0:
                        self._check_reversals()

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
        """V8: Trade with restored sanity checks and cooldown."""
        deal = DEALS[did]
        direction = classification['direction']
        dp = classification['delta_p']
        severity = classification['severity']
        category = classification['category']
        tier = classification.get('tier', 2)

        # V8: Per-deal cooldown (5 ticks)
        ticks_since = self.tick - self.deal_last_trade_tick[did]
        if ticks_since < NEWS_COOLDOWN_TICKS:
            log.info(f"  COOLDOWN: {did} traded {ticks_since} ticks ago "
                     f"(need {NEWS_COOLDOWN_TICKS})")
            # Still apply the probability update even if we don't trade
            self.prob.apply_news(did, dp)
            return

        old_p, new_p = self.prob.apply_news(did, dp)

        # Blend with market
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

        # Check mispricing
        intrinsic = self.prob.intrinsic_target_price(did, ap)
        mispricing = intrinsic - tp
        if abs(mispricing) < MIN_MISPRICING:
            log.info(f"  SKIP MISPRICING: {did} gap=${mispricing:+.2f} < ${MIN_MISPRICING}")
            return

        # V8: Restored sanity check — if market strongly disagrees, reduce size
        # (but don't skip entirely — market lags our news by 3-5 ticks)
        confidence_mult = 1.0
        if mp is not None:
            if direction == 'positive' and mp > 0.90:
                log.info(f"  CAUTION: {did} mktP={mp:.1%} already high, halving size")
                confidence_mult = 0.5
            elif direction == 'negative' and mp < 0.10:
                log.info(f"  CAUTION: {did} mktP={mp:.1%} already low, halving size")
                confidence_mult = 0.5

        # Position sizing based on severity and tier
        desired_size = TRADE_SIZE.get(severity, 5000)

        # V8.1: Tier-based confidence scaling
        # Tier 0 (MASTER_LOOKUP trusted): full size
        # Tier 1 (ML meta + body text): 70% size
        # Tier 2 (hardcoded meta): 60% size
        # Tier 3 (keyword fallback): 40% size
        tier_mult = {0: 1.0, 1: 0.7, 2: 0.6, 3: 0.4}.get(tier, 0.4)
        desired_size = max(MIN_TRADE_SIZE, int(desired_size * tier_mult))

        # Category boost for regulatory/alternative
        if category in ('REG', 'ALT'):
            desired_size = min(MAX_DEAL_POSITION, int(desired_size * 1.2))

        # Apply confidence multiplier
        desired_size = max(MIN_TRADE_SIZE, int(desired_size * confidence_mult))

        current_pos = int(self.positions.get(target, 0))

        if direction == 'positive':
            room_in_deal = max(0, MAX_DEAL_POSITION - current_pos)
            trade_qty = min(desired_size, room_in_deal)
            action = 'BUY'
        else:
            room_in_deal = max(0, MAX_DEAL_POSITION + current_pos)
            trade_qty = min(desired_size, room_in_deal)
            action = 'SELL'

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
                 f"exp=${expected_move:+.2f} size={actual}")

        self.executor.market(target, action, actual)

        # Hedge for non-cash deals
        if deal['structure'] != 'ALL_CASH' and deal['ratio'] > 0:
            hedge_action = 'SELL' if action == 'BUY' else 'BUY'
            hedge_qty = int(actual * deal['ratio'])
            if hedge_qty >= MIN_TRADE_SIZE:
                hedge_avail = self._available_room(acquirer, hedge_action)
                hedge_actual = min(hedge_qty, hedge_avail)
                if hedge_actual >= 100:
                    self.executor.market(acquirer, hedge_action, hedge_actual)

        self.news_traded += 1
        self.deal_targets[did] = current_pos + (actual if action == 'BUY' else -actual)
        self.deal_last_trade_tick[did] = self.tick

    def _check_reversals(self):
        """V8: If intrinsic price has flipped against our position, unwind."""
        for did, deal in DEALS.items():
            target = deal['target']
            acquirer = deal['acquirer']
            tp = self.prices.get(target, 0)
            ap = self.prices.get(acquirer, 0)
            current_pos = int(self.positions.get(target, 0))

            if tp <= 0 or ap <= 0 or current_pos == 0:
                continue

            intrinsic = self.prob.intrinsic_target_price(did, ap)
            mispricing = intrinsic - tp

            # We're long but intrinsic says we should be short (or vice versa)
            if current_pos > 0 and mispricing < -0.30:
                # Our long position is underwater — intrinsic < market price
                unwind_qty = min(abs(current_pos), 5000)
                avail = self._available_room(target, 'SELL')
                actual = min(unwind_qty, avail)
                if actual >= MIN_TRADE_SIZE:
                    log.info(f"  REVERSAL: {did} SELL {actual} (long pos, "
                             f"intrinsic=${intrinsic:.2f} < mkt=${tp:.2f})")
                    self.executor.market(target, 'SELL', actual)
                    self.deal_last_trade_tick[did] = self.tick

            elif current_pos < 0 and mispricing > 0.30:
                # Our short position is underwater
                unwind_qty = min(abs(current_pos), 5000)
                avail = self._available_room(target, 'BUY')
                actual = min(unwind_qty, avail)
                if actual >= MIN_TRADE_SIZE:
                    log.info(f"  REVERSAL: {did} BUY {actual} (short pos, "
                             f"intrinsic=${intrinsic:.2f} > mkt=${tp:.2f})")
                    self.executor.market(target, 'BUY', actual)
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