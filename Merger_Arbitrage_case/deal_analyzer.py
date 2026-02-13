"""
Deal Analyzer for Merger Arbitrage Case.

Computes deal values, implied probabilities, standalone values, and classifies news.
This is the analytical engine that enriches raw market data with merger-arb-specific metrics.
"""
import re
import logging
from typing import Dict, Any, Optional, Tuple

from config import DEALS, TICKER_TO_DEAL, CATEGORY_MULTIPLIERS, NEWS_IMPACT

logger = logging.getLogger(__name__)

# Build company name lookup for news matching
# Maps partial name fragments to deal IDs for fuzzy matching
_COMPANY_NAMES = {}
for _did, _deal in DEALS.items():
    _COMPANY_NAMES[_did] = {
        'target': _deal['target'],
        'acquirer': _deal['acquirer'],
    }


class DealAnalyzer:
    """Analyzes M&A deals: computes spreads, implied probabilities, and classifies news."""

    def __init__(self):
        # Current analyst probability per deal (updated by news)
        self.analyst_probs: Dict[str, float] = {}
        # Standalone values inferred from initial prices
        self.standalone_values: Dict[str, float] = {}

        # Initialize from config
        for deal_id, deal in DEALS.items():
            self.analyst_probs[deal_id] = deal['initial_prob']
            k = self.compute_deal_value(deal_id, deal['acquirer_start'])
            p0 = deal['initial_prob']
            if p0 < 1.0:
                self.standalone_values[deal_id] = (deal['target_start'] - p0 * k) / (1 - p0)
            else:
                self.standalone_values[deal_id] = deal['target_start']

    def compute_deal_value(self, deal_id: str, acquirer_price: float) -> float:
        """
        Compute deal value K for the target given current acquirer price.
        All-cash: K = cash amount
        Stock-for-stock: K = ratio * acquirer_price
        Mixed: K = cash + ratio * acquirer_price
        """
        deal = DEALS[deal_id]
        cash = deal['deal_terms']['cash']
        ratio = deal['deal_terms']['ratio']
        return cash + ratio * acquirer_price

    def compute_implied_probability(self, deal_id: str, target_price: float,
                                     acquirer_price: float) -> Optional[float]:
        """
        Compute implied deal completion probability from market prices.
        P_T = p * K + (1 - p) * V  =>  p = (P_T - V) / (K - V)
        """
        deal_value = self.compute_deal_value(deal_id, acquirer_price)
        standalone = self.standalone_values.get(deal_id)
        if standalone is None or abs(deal_value - standalone) < 0.001:
            return None
        implied_p = (target_price - standalone) / (deal_value - standalone)
        return max(0.0, min(1.0, implied_p))

    def compute_deal_spread(self, deal_id: str, target_price: float,
                            acquirer_price: float) -> Dict[str, Any]:
        """Compute full deal spread metrics for a single deal."""
        deal = DEALS[deal_id]
        deal_value = self.compute_deal_value(deal_id, acquirer_price)
        standalone = self.standalone_values.get(deal_id, 0)
        spread = deal_value - target_price
        spread_pct = (spread / target_price * 100) if target_price > 0 else 0
        implied_prob = self.compute_implied_probability(deal_id, target_price, acquirer_price)
        analyst_prob = self.analyst_probs.get(deal_id, deal['initial_prob'])

        return {
            'deal_id': deal_id,
            'target_ticker': deal['target'],
            'acquirer_ticker': deal['acquirer'],
            'target_price': target_price,
            'acquirer_price': acquirer_price,
            'deal_value': round(deal_value, 4),
            'standalone_value': round(standalone, 4),
            'deal_spread': round(spread, 4),
            'deal_spread_pct': round(spread_pct, 4),
            'implied_prob': round(implied_prob, 4) if implied_prob is not None else None,
            'analyst_prob': round(analyst_prob, 4),
            'prob_diff': round((implied_prob or 0) - analyst_prob, 4),
            'structure': deal['structure'],
        }

    def classify_news(self, headline: str, body: str = "") -> Dict[str, Any]:
        """
        Classify a news item: identify deal, category, direction, severity.
        Uses headline for direction (cleaner signal) and full text for deal/category matching.
        """
        full_text = f"{headline} {body}".upper()
        headline_upper = headline.upper()

        # 1. Identify which deal this news is about (use full text)
        deal_id = self._identify_deal(full_text)

        # 2. Identify news category (use full text)
        category = self._identify_category(full_text)

        # 3. Identify direction - USE HEADLINE ONLY for cleaner signal
        direction = self._identify_direction(headline_upper)

        # 4. Identify severity (use full text)
        severity = self._identify_severity(full_text)

        # 5. Compute estimated probability change
        delta_p = self._compute_delta_p(deal_id, category, direction, severity)

        return {
            'deal_id': deal_id,
            'category': category,
            'direction': direction,
            'severity': severity,
            'delta_p': delta_p,
        }

    def apply_news_impact(self, deal_id: str, delta_p: float, news_id: int = None) -> Tuple[float, float]:
        """Apply a probability change to a deal. Returns (prob_before, prob_after)."""
        if deal_id is None or deal_id not in self.analyst_probs:
            return (0, 0)
        prob_before = self.analyst_probs[deal_id]
        prob_after = max(0.0, min(1.0, prob_before + delta_p))
        self.analyst_probs[deal_id] = prob_after
        return (prob_before, prob_after)

    # --- Private classification helpers ---

    def _identify_deal(self, text: str) -> Optional[str]:
        """Identify which deal a news item refers to using tickers, company names, and sector keywords."""
        deal_scores: Dict[str, int] = {}

        # Expanded keyword maps for each deal
        deal_keywords = {
            'D1': [
                'TGX', 'PHR', 'TARGENIX', 'PHARMACO',
                'PHARMACEUTICAL', 'DRUG', 'FDA', 'BIOTECH', 'CLINICAL',
                'TRIAL', 'PATENT', 'THERAPY', 'MEDICINE', 'PHARMA',
                'DIVESTITURE', 'HEALTH',
            ],
            'D2': [
                'BYL', 'CLD', 'BYTELAYER', 'CLOUDSYS',
                'CLOUD', 'SOFTWARE', 'TECH', 'DATA CENTER', 'SAAS',
                'ENGINEER', 'DEVELOPER', 'MARKET SHARE', 'DIGITAL',
                'FTC', 'ANTITRUST',
            ],
            'D3': [
                'GGD', 'PNR', 'GREENGRID', 'PETRONORTH',
                'INFRASTRUCTURE', 'OIL', 'GAS', 'PIPELINE', 'GRID',
                'UTILITY', 'FOSSIL', 'CARBON', 'EMISSIONS',
                'ENVIRONMENTAL', 'POLITICAL',
            ],
            'D4': [
                'FSR', 'ATB', 'FINSURE', 'ATLAS BANK', 'ATLAS',
                'BANKING', 'FINANCIAL', 'INSURANCE', 'BANK', 'FDIC',
                'OCC', 'DEPOSIT', 'BRANCH', 'LENDING', 'CREDIT UNION',
            ],
            'D5': [
                'SPK', 'EEC', 'SOLARPEAK', 'EASTENERGY',
                'SOLAR', 'RENEWABLE', 'WIND', 'CLEAN ENERGY',
                'TAX CREDIT', 'TURBINE', 'PANEL', 'FERC',
                'INTERCONNECT', 'GENERATION',
            ],
        }

        for deal_id, keywords in deal_keywords.items():
            score = 0
            for kw in keywords:
                if kw in text:
                    # Ticker matches are strongest
                    if kw in (DEALS[deal_id]['target'], DEALS[deal_id]['acquirer']):
                        score += 5
                    # Company names
                    elif len(kw) > 5:
                        score += 3
                    else:
                        score += 1
            if score > 0:
                deal_scores[deal_id] = score

        if deal_scores:
            best = max(deal_scores, key=deal_scores.get)
            # Only return if we have a meaningful match (avoid false positives on generic words)
            if deal_scores[best] >= 2:
                return best
        return None

    def _identify_category(self, text: str) -> str:
        """Identify news category: REG, FIN, SHR, ALT, PRC."""
        category_keywords = {
            'REG': ['REGULAT', 'ANTITRUST', 'APPROVAL', 'COMMISSION', 'FTC', 'DOJ',
                     'COMPLIANCE', 'GOVERNMENT', 'AUTHORITY', 'RULING', 'LEGAL',
                     'INVESTIGATION', 'REVIEW', 'FILING', 'CLOSING DATE',
                     'REMEDY', 'DIVESTITURE', 'FDIC', 'OCC', 'FERC',
                     'PHASE II', 'PHASE I', 'ISS', 'GLASS LEWIS', 'EC '],
            'FIN': ['FINANC', 'EARNINGS', 'REVENUE', 'PROFIT', 'LOSS', 'BALANCE',
                     'QUARTER', 'ANNUAL', 'DEBT', 'CREDIT', 'VALUATION', 'EPS',
                     'MARKET CAP', 'CASH FLOW', 'TERMINATION FEE', 'REFINANC',
                     'RETENTION', 'INCENTIVE', 'CO-INVESTMENT'],
            'SHR': ['SHAREHOLD', 'VOTE', 'PROXY', 'ACTIVIST', 'BOARD', 'DIRECTOR',
                     'DISSENT', 'OPPOSITION', 'STAKE', 'INVESTOR',
                     'ADVOCACY', 'CONSUMER GROUP', 'FAIRNESS', 'MANAGEMENT',
                     'SPECULATION', 'VIABILITY', 'OUTLOOK', 'SENTIMENT'],
            'ALT': ['ALTERNATIVE', 'COMPETING', 'RIVAL', 'COUNTER', 'BIDDER',
                     'HOSTILE', 'UNSOLICITED', 'REVISED', 'SWEETENED', 'RAISED',
                     'TOPPING', 'OVERBID', 'WHITE KNIGHT'],
            'PRC': ['PRICE', 'PREMIUM', 'DISCOUNT', 'OVERVALUED', 'UNDERVALUED',
                     'FAIR VALUE', 'SYNERGY', 'COST', 'SAVING', 'SPREAD'],
        }

        scores = {}
        for cat, keywords in category_keywords.items():
            score = sum(1 for kw in keywords if kw in text)
            if score > 0:
                scores[cat] = score

        if scores:
            return max(scores, key=scores.get)
        return 'FIN'

    def _identify_direction(self, text: str) -> str:
        """
        Identify direction: positive, negative, or ambiguous.
        Uses HEADLINE ONLY for cleaner signal (body can have mixed language).
        """
        positive_words = [
            'APPROV', 'SUCCESS', 'SUPPORT', 'FAVOR', 'AGREE', 'ACCEPT',
            'PROGRESS', 'ADVANCE', 'CLEAR', 'GREEN LIGHT', 'ENDORSE',
            'RECOMMEND', 'BOOST', 'STRONG', 'INCREASE', 'RAISE', 'ABOVE',
            'EXCEED', 'COMPLETE', 'FINALIZ', 'CONFIRM', 'ON TRACK',
            'SWEETENED', 'HIGHER', 'IMPROVED', 'POSITIVE', 'GROWTH',
            'SECURED', 'REFINANC', 'REMEDY ACCEPTED', 'REMEDY PACKAGE',
            'STRONG REVENUE', 'RETENTION',
        ]
        negative_words = [
            'REJECT', 'BLOCK', 'OPPOS', 'FAIL', 'DECLINE', 'CONCERN',
            'DELAY', 'OBSTACLE', 'CHALLENGE', 'RISK', 'THREAT', 'WITHDRAW',
            'CANCEL', 'TERMIN', 'DOUBT', 'UNCERTAIN', 'LAWSUIT', 'SUE',
            'DISAPPROV', 'NEGATIVE', 'BELOW', 'WEAK', 'LOSS', 'DROP',
            'LOWER', 'PROBLEM', 'ISSUE', 'COMPLICATE', 'PUSHED BACK',
            'INVESTIGATION', 'TRIGGERED', 'SIGNALS CONCERN',
        ]

        pos_score = sum(1 for w in positive_words if w in text)
        neg_score = sum(1 for w in negative_words if w in text)

        if pos_score > neg_score:
            return 'positive'
        elif neg_score > pos_score:
            return 'negative'
        return 'ambiguous'

    def _identify_severity(self, text: str) -> str:
        """Identify severity: small, medium, or large."""
        large_words = [
            'MAJOR', 'SIGNIFICANT', 'CRITICAL', 'DECISIVE', 'FUNDAMENTAL',
            'TRANSFORM', 'BLOCK', 'TERMIN', 'CANCEL', 'FINAL',
            'DEFINITIVE', 'UNCONDITIONAL', 'COMPLET', 'PHASE II',
        ]
        medium_words = [
            'IMPORTANT', 'NOTABLE', 'SUBSTANT', 'CONSIDER', 'MATERIAL',
            'MEANINGFUL', 'MODERATE', 'PRELIMINARY', 'GROWING',
            'EMERGES', 'EXTENDS', 'UPDATED', 'REVISED',
        ]

        large_score = sum(1 for w in large_words if w in text)
        medium_score = sum(1 for w in medium_words if w in text)

        if large_score >= 2:
            return 'large'
        elif large_score >= 1 or medium_score >= 2:
            return 'medium'
        return 'small'

    def _compute_delta_p(self, deal_id: Optional[str], category: str,
                         direction: str, severity: str) -> float:
        """Compute estimated probability change from classified news."""
        if deal_id is None or direction == 'ambiguous':
            return 0.0

        deal = DEALS.get(deal_id)
        if not deal:
            return 0.0

        base = NEWS_IMPACT.get(direction, {}).get(severity, 0.0)
        cat_mult = CATEGORY_MULTIPLIERS.get(category, 1.0)
        deal_mult = deal.get('sensitivity_multiplier', 1.0)

        return round(base * cat_mult * deal_mult, 4)
