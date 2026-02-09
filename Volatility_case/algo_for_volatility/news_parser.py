"""Parse volatility news announcements from the RIT news feed."""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

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
        self.current_vol: Optional[float] = None
        self.forecast_vol_low: Optional[float] = None
        self.forecast_vol_high: Optional[float] = None
        self.delta_limit: Optional[float] = None
        self.penalty_rate: Optional[float] = None
        self.last_news_id: int = 0
        self.vol_history: list = []
        self.news_count: int = 0
        # Track which vol info arrived most recently
        self._last_vol_source: Optional[str] = None  # 'realized' or 'forecast'

    @property
    def forecast_vol_mid(self) -> Optional[float]:
        if self.forecast_vol_low is not None and self.forecast_vol_high is not None:
            return (self.forecast_vol_low + self.forecast_vol_high) / 2.0
        return None

    @property
    def best_vol_estimate(self) -> Optional[float]:
        """Best estimate of vol for option pricing.

        CRITICAL FIX: Use the MOST RECENTLY received information.
        - Forecast = forward-looking (what vol WILL be next week)
        - Realized = backward-looking (what vol WAS this week)

        When a new forecast arrives AFTER realized vol, it means vol
        is changing and we must react. The forecast is more relevant
        for pricing options on the REMAINING life.
        """
        if self._last_vol_source == 'forecast' and self.forecast_vol_mid is not None:
            return self.forecast_vol_mid
        if self._last_vol_source == 'realized' and self.current_vol is not None:
            return self.current_vol
        # Fallback: prefer whichever exists
        if self.current_vol is not None:
            return self.current_vol
        return self.forecast_vol_mid

    def process_news(self, news_items: list) -> bool:
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
                new_vol = vol_pct / 100.0
                self.current_vol = new_vol
                self._last_vol_source = 'realized'
                self.vol_history.append(new_vol)
                self.news_count += 1
                logger.info("NEWS: Current realized vol = %.1f%% (history: %s) [NOW USING REALIZED]",
                            vol_pct,
                            [f"{v*100:.0f}%" for v in self.vol_history])
                updated = True

            # Parse forecast volatility range
            match = RE_FORECAST_VOL.search(text)
            if match:
                low_pct = float(match.group(1))
                high_pct = float(match.group(2))
                self.forecast_vol_low = low_pct / 100.0
                self.forecast_vol_high = high_pct / 100.0
                self._last_vol_source = 'forecast'
                self.news_count += 1
                logger.info("NEWS: Forecast vol next week = %.1f%% - %.1f%% (mid=%.1f%%) [NOW USING FORECAST]",
                            low_pct, high_pct, (low_pct + high_pct) / 2)
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
        parts.append(f"using={self._last_vol_source}")
        if self.delta_limit is not None:
            parts.append(f"dlimit={self.delta_limit:.0f}")
        return f"VolState({', '.join(parts)})"
