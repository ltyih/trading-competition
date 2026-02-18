"""Parse temperature forecast news from the RIT news feed for the electricity case.

News format examples:
    1) Range forecast:
        Headline: TEMPERATURE Forecast for DAY 5
        The average temperature tomorrow is forecasted to be between 8 and 15 degrees Celsius.

    2) Exact forecast:
        Headline: TEMPERATURE Forecast for DAY 5
        The average temperature tomorrow will be 14 degrees Celsius.

The demand model:
    ELEC_customers = 200 - 15*AT + 0.8*AT^2 - 0.01*AT^3
"""

import re
from typing import Optional

from config import DEMAND_INTERCEPT, DEMAND_LINEAR, DEMAND_QUADRATIC, DEMAND_CUBIC

# Regex patterns for parsing temperature forecast news
# Range: "between 8 and 15 degrees Celsius"
RE_TEMP_RANGE = re.compile(
    r"temperature.*?between\s+(-?\d+(?:\.\d+)?)\s+and\s+(-?\d+(?:\.\d+)?)\s+degrees",
    re.IGNORECASE | re.DOTALL,
)
# Exact: "will be 14 degrees Celsius"
RE_TEMP_EXACT = re.compile(
    r"temperature.*?will be\s+(-?\d+(?:\.\d+)?)\s+degrees",
    re.IGNORECASE | re.DOTALL,
)
RE_FORECAST_DAY = re.compile(
    r"(?:forecast|temperature).*?DAY\s+(\d+)",
    re.IGNORECASE | re.DOTALL,
)


def compute_demand(avg_temp: float) -> float:
    """Compute electricity demand given average temperature.

    ELEC_customers = 200 - 15*AT + 0.8*AT^2 - 0.01*AT^3
    """
    return (
        DEMAND_INTERCEPT
        + DEMAND_LINEAR * avg_temp
        + DEMAND_QUADRATIC * avg_temp ** 2
        + DEMAND_CUBIC * avg_temp ** 3
    )


class TemperatureState:
    """Tracks temperature forecasts and computes electricity demand from news."""

    def __init__(self):
        self.last_news_id: int = 0
        # Per-day forecasts: {day_number: (temp_low, temp_high)}
        self.forecasts: dict[int, tuple[float, float]] = {}

    def get_forecast(self, day: int) -> Optional[tuple[float, float]]:
        """Get (temp_low, temp_high) for a given day, if available."""
        return self.forecasts.get(day)

    def get_mid_temp(self, day: int) -> Optional[float]:
        """Get midpoint temperature forecast for a given day."""
        f = self.forecasts.get(day)
        if f is not None:
            return (f[0] + f[1]) / 2.0
        return None

    def get_demand_range(self, day: int) -> Optional[tuple[float, float]]:
        """Compute (demand_low, demand_high) for a given day from temperature range.

        Since the demand function is non-monotonic, we evaluate at both
        endpoints and also check the interior to find the true min/max.
        """
        f = self.forecasts.get(day)
        if f is None:
            return None
        temp_low, temp_high = f
        # Evaluate demand at several points across the range to find min/max
        num_samples = 50
        step = (temp_high - temp_low) / max(num_samples, 1)
        demands = [
            compute_demand(temp_low + i * step) for i in range(num_samples + 1)
        ]
        return (min(demands), max(demands))

    def get_demand_at_mid(self, day: int) -> Optional[float]:
        """Compute demand at the midpoint temperature for a given day."""
        mid = self.get_mid_temp(day)
        if mid is not None:
            return compute_demand(mid)
        return None

    def process_news(self, news_items: list) -> bool:
        """Process news items and extract temperature forecasts.

        Returns True if any new forecast was found.
        """
        updated = False
        # Fix: snapshot the threshold before the loop so that items returned
        # newest-first don't cause earlier (lower-id) items to be skipped.
        threshold = self.last_news_id
        max_id_seen = self.last_news_id

        for news in news_items:
            news_id = news.get("news_id", 0)
            if news_id <= threshold:
                continue

            max_id_seen = max(max_id_seen, news_id)

            headline = news.get("headline", "")
            body = news.get("body", "")
            text = f"{headline} {body}"

            # Extract forecast day
            day_match = RE_FORECAST_DAY.search(text)
            if not day_match:
                continue

            day = int(day_match.group(1))

            # Try range first: "between X and Y degrees"
            range_match = RE_TEMP_RANGE.search(text)
            # Then try exact: "will be X degrees"
            exact_match = RE_TEMP_EXACT.search(text)

            if range_match:
                temp_low = float(range_match.group(1))
                temp_high = float(range_match.group(2))
                self.forecasts[day] = (temp_low, temp_high)

                mid_temp = (temp_low + temp_high) / 2.0
                demand_mid = compute_demand(mid_temp)
                demand_range = self.get_demand_range(day)

                print(
                    f"  [NEWS] Day {day} temp RANGE = {temp_low:.1f} to {temp_high:.1f} C "
                    f"(mid={mid_temp:.1f} C, demand_mid={demand_mid:.1f}, "
                    f"demand_range={demand_range[0]:.1f}-{demand_range[1]:.1f})"
                )
                updated = True

            elif exact_match:
                temp_exact = float(exact_match.group(1))
                self.forecasts[day] = (temp_exact, temp_exact)

                demand = compute_demand(temp_exact)

                print(
                    f"  [NEWS] Day {day} temp EXACT = {temp_exact:.1f} C "
                    f"-> demand = {demand:.2f}"
                )
                updated = True

            else:
                # Temperature headline detected but no temperature value extracted
                print(f"  [NEWS] WARNING: temp headline for Day {day} but no value parsed!")
                print(f"         headline={headline!r}")
                print(f"         body={body!r}")

        # Update last_news_id once after processing all items
        self.last_news_id = max_id_seen

        return updated

    def __repr__(self) -> str:
        parts = []
        for day in sorted(self.forecasts.keys()):
            low, high = self.forecasts[day]
            mid = (low + high) / 2.0
            demand = compute_demand(mid)
            parts.append(
                f"D{day}:[{low:.0f}-{high:.0f}C, mid={mid:.1f}C, demand={demand:.1f}]"
            )
        return f"TempState({', '.join(parts)})"
