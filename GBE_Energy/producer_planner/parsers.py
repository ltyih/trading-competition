"""News parsers for *REMOVED* 2026 Electricity case.

Parses SUNLIGHT forecast news (exact or range hours, delivery_day) and
SPOT BULLETIN news (spot_price, spot_contract_volume, delivery_day).
Never raises on bad format; returns None or empty result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SunlightResult:
    """Parsed SUNLIGHT forecast."""

    delivery_day: Optional[int]  # DAY N
    hours_exact: Optional[float]  # "There will be 13 hours"
    hours_low: Optional[float]  # "between 13 and 20"
    hours_high: Optional[float]

    def mid_hours(self) -> Optional[float]:
        if self.hours_exact is not None:
            return self.hours_exact
        if self.hours_low is not None and self.hours_high is not None:
            return (self.hours_low + self.hours_high) / 2.0
        return None

    def is_exact(self) -> bool:
        return self.hours_exact is not None


@dataclass(frozen=True)
class SpotBulletinResult:
    """Parsed spot price/volume bulletin."""

    delivery_day: Optional[int]
    spot_price: Optional[float]
    spot_contract_volume: Optional[float]


# DAY N in headline or body
_DAY_PATTERN = re.compile(r"\bDAY\s+(\d+)\b", re.IGNORECASE)


def _parse_delivery_day(headline: str, body: str) -> Optional[int]:
    """Extract delivery day from headline or body."""
    for text in (headline, body):
        m = _DAY_PATTERN.search(text)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, IndexError):
                pass
    return None


# "There will be 13 hours of sunlight tomorrow."
# "The weather forecasts call for between 13 and 20 hours of sunlight tomorrow."
_SUNLIGHT_EXACT = re.compile(
    r"(?:there\s+will\s+be|forecasts?\s+call\s+for)\s+(\d+(?:\.\d+)?)\s+hours?\s+of\s+sunlight",
    re.IGNORECASE,
)
_SUNLIGHT_RANGE = re.compile(
    r"between\s+(\d+(?:\.\d+)?)\s+and\s+(\d+(?:\.\d+)?)\s+hours?\s+of\s+sunlight",
    re.IGNORECASE,
)


def parse_sunlight(headline: str, body: str) -> Optional[SunlightResult]:
    """Parse SUNLIGHT forecast news. Returns None on failure or if not sunlight news."""
    if not headline.upper().strip():
        return None
    if "SUNLIGHT" not in headline.upper():
        return None
    delivery_day = _parse_delivery_day(headline, body)
    combined = f"{headline} {body}"
    hours_exact = None
    hours_low = None
    hours_high = None
    m_exact = _SUNLIGHT_EXACT.search(combined)
    if m_exact:
        try:
            hours_exact = float(m_exact.group(1))
        except (ValueError, IndexError):
            pass
    m_range = _SUNLIGHT_RANGE.search(combined)
    if m_range:
        try:
            hours_low = float(m_range.group(1))
            hours_high = float(m_range.group(2))
        except (ValueError, IndexError):
            pass
    if hours_exact is None and (hours_low is None or hours_high is None):
        return None
    return SunlightResult(
        delivery_day=delivery_day,
        hours_exact=hours_exact,
        hours_low=hours_low,
        hours_high=hours_high,
    )


# $18.31 or $ 18.31
_SPOT_PRICE = re.compile(r"\$\s*(\d+(?:\.\d+)?)")
# "402 contracts are available" or "402 contracts"
_SPOT_VOLUME = re.compile(r"(\d+)\s+contracts?(?:\s+are\s+available)?", re.IGNORECASE)


def parse_spot_bulletin(headline: str, body: str) -> Optional[SpotBulletinResult]:
    """Parse SPOT PRICE AND VOLUMES / PRICE AND VOLUME BULLETIN. Returns None on failure."""
    up = headline.upper()
    if "SPOT" not in up and "PRICE" not in up and "VOLUME" not in up and "BULLETIN" not in up:
        return None
    if "SPOT" not in up and "BULLETIN" not in up:
        # Allow "PRICE AND VOLUME BULLETIN"
        if "BULLETIN" not in up:
            return None
    delivery_day = _parse_delivery_day(headline, body)
    combined = f"{headline} {body}"
    price = None
    vol = None
    m_price = _SPOT_PRICE.search(combined)
    if m_price:
        try:
            price = float(m_price.group(1))
        except (ValueError, IndexError):
            pass
    m_vol = _SPOT_VOLUME.search(combined)
    if m_vol:
        try:
            vol = float(m_vol.group(1))
        except (ValueError, IndexError):
            pass
    if price is None and vol is None:
        return None
    return SpotBulletinResult(
        delivery_day=delivery_day,
        spot_price=price,
        spot_contract_volume=vol,
    )


# ---------------------------------------------------------------------------
# Self-test (exact spec examples)
# ---------------------------------------------------------------------------

def _self_test() -> None:
    # SUNLIGHT exact
    h1 = "SUNLIGHT Forecast for DAY 5"
    b1 = "There will be 13 hours of sunlight tomorrow."
    r1 = parse_sunlight(h1, b1)
    assert r1 is not None
    assert r1.delivery_day == 5
    assert r1.hours_exact == 13.0
    assert r1.mid_hours() == 13.0
    assert r1.is_exact()

    # SUNLIGHT range
    b2 = "The weather forecasts call for between 13 and 20 hours of sunlight tomorrow."
    r2 = parse_sunlight(h1, b2)
    assert r2 is not None
    assert r2.hours_low == 13.0 and r2.hours_high == 20.0
    assert r2.mid_hours() == 16.5
    assert not r2.is_exact()

    # SPOT BULLETIN
    h3 = "SPOT PRICE AND VOLUMES FOR DAY 4"
    b3 = "The spot price is $18.31. 402 contracts are available for delivery."
    r3 = parse_spot_bulletin(h3, b3)
    assert r3 is not None
    assert r3.delivery_day == 4
    assert r3.spot_price == 18.31
    assert r3.spot_contract_volume == 402.0

    # PRICE AND VOLUME BULLETIN variant
    h4 = "PRICE AND VOLUME BULLETIN"
    b4 = "Price $18.31. 402 contracts are available."
    r4 = parse_spot_bulletin(h4, b4)
    assert r4 is not None
    assert r4.spot_price == 18.31
    assert r4.spot_contract_volume == 402.0

    # Robustness: bad input returns None, no crash
    assert parse_sunlight("", "") is None
    assert parse_sunlight("OTHER NEWS", "No sunlight here.") is None
    assert parse_spot_bulletin("OTHER", "No price") is None

    print("parsers.py self-test passed.")


if __name__ == "__main__":
    _self_test()
