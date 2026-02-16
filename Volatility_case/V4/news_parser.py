"""Parse volatility news announcements from the RIT news feed."""

import re
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Regex patterns for parsing news
RE_CURRENT_VOL = re.compile(
    r"realized volatility.*?(?:this week|for this week).*?(\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)
RE_FORECAST_VOL = re.compile(
    r"next week.*?between\s*(\d+(?:\.\d+)?)\s*%?\s*and\s*(\d+(?:\.\d+)?)\s*%",
    re.IGNORECASE,
)
RE_DELTA_LIMIT = re.compile(
    r"delta limit.*?(\d+(?:,\d+)*)", re.IGNORECASE
)
RE_PENALTY_RATE = re.compile(
    r"penalty.*?(\d+(?:\.\d+)?)\s*%", re.IGNORECASE
)


class VolatilityState:
    """Tracks the current volatility state from news announcements."""

    def __init__(self):
        self.current_vol: Optional[float] = None       # Current week's realized vol (decimal)
        self.forecast_vol_low: Optional[float] = None   # Next week forecast low (decimal)
        self.forecast_vol_high: Optional[float] = None  # Next week forecast high (decimal)
        self.delta_limit: Optional[float] = None         # Delta limit for this sub-heat
        self.penalty_rate: Optional[float] = None        # Penalty rate (decimal)
        self.last_news_id: int = 0

    @property
    def forecast_vol_mid(self) -> Optional[float]:
        """Midpoint of the forecast range."""
        if self.forecast_vol_low is not None and self.forecast_vol_high is not None:
            return (self.forecast_vol_low + self.forecast_vol_high) / 2.0
        return None

    @property
    def best_vol_estimate(self) -> Optional[float]:
        """Best estimate of current realized volatility.
        Falls back to forecast midpoint if current vol not yet announced."""
        if self.current_vol is not None:
            return self.current_vol
        return self.forecast_vol_mid

    def process_news(self, news_items: list) -> bool:
        """
        Process a list of news items and update volatility state.
        Returns True if any new volatility info was found.
        """
        updated = False

        for news in news_items:
            news_id = news.get("news_id", 0)
            if news_id <= self.last_news_id:
                continue

            headline = news.get("headline", "")
            body = news.get("body", "")
            text = f"{headline} {body}"

            # Parse current volatility
            match = RE_CURRENT_VOL.search(text)
            if match:
                vol_pct = float(match.group(1))
                self.current_vol = vol_pct / 100.0
                logger.info("NEWS: Current realized vol = %.1f%%", vol_pct)
                updated = True

            # Parse forecast volatility range
            match = RE_FORECAST_VOL.search(text)
            if match:
                low_pct = float(match.group(1))
                high_pct = float(match.group(2))
                self.forecast_vol_low = low_pct / 100.0
                self.forecast_vol_high = high_pct / 100.0
                logger.info("NEWS: Forecast vol next week = %.1f%% - %.1f%%",
                            low_pct, high_pct)
                updated = True

            # Parse delta limit
            match = RE_DELTA_LIMIT.search(text)
            if match:
                self.delta_limit = float(match.group(1).replace(",", ""))
                logger.info("NEWS: Delta limit = %.0f", self.delta_limit)
                updated = True

            # Parse penalty rate
            match = RE_PENALTY_RATE.search(text)
            if match:
                self.penalty_rate = float(match.group(1)) / 100.0
                logger.info("NEWS: Penalty rate = %.2f%%",
                            float(match.group(1)))
                updated = True

            self.last_news_id = max(self.last_news_id, news_id)

        return updated

    def __repr__(self) -> str:
        parts = []
        if self.current_vol is not None:
            parts.append(f"vol={self.current_vol*100:.1f}%")
        if self.forecast_vol_low is not None:
            parts.append(
                f"forecast={self.forecast_vol_low*100:.1f}-"
                f"{self.forecast_vol_high*100:.1f}%"
            )
        if self.delta_limit is not None:
            parts.append(f"dlimit={self.delta_limit:.0f}")
        if self.penalty_rate is not None:
            parts.append(f"penalty={self.penalty_rate*100:.2f}%")
        return f"VolState({', '.join(parts)})"
