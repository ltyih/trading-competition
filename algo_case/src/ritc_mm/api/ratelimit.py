"""Per-ticker rate-limit tracker for the RIT Client REST API.

The RIT API returns ``Retry-After`` (header) and ``wait`` (body) on HTTP 429.
For order insertion, the ``X-Wait-Until`` response header indicates the
earliest monotonic time the next order for that ticker may be submitted.

This module provides a lightweight in-memory tracker so the client can
proactively skip requests that would be rejected.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TickerCooldown:
    """Tracks the next-allowed-order timestamp for a single ticker."""

    next_allowed_ts: float = 0.0

    def is_ready(self) -> bool:
        """Return ``True`` if we may send an order for this ticker now."""
        return time.monotonic() >= self.next_allowed_ts

    def seconds_until_ready(self) -> float:
        """Seconds remaining before the ticker's cooldown expires (≥ 0)."""
        return max(0.0, self.next_allowed_ts - time.monotonic())

    def set_wait_until(self, wait_seconds: float) -> None:
        """Set the next-allowed timestamp relative to *now*.

        Parameters
        ----------
        wait_seconds:
            Seconds from now until the next request is allowed.
        """
        self.next_allowed_ts = time.monotonic() + max(wait_seconds, 0.0)


@dataclass
class RateLimitTracker:
    """Aggregate rate-limit state across all tickers.

    Usage::

        tracker = RateLimitTracker()
        if tracker.is_ready("SPNG"):
            # submit order for SPNG …
            tracker.record_wait("SPNG", wait_seconds=0.215)
    """

    _cooldowns: dict[str, TickerCooldown] = field(default_factory=dict)

    # Track global (non-ticker-specific) cooldown
    _global: TickerCooldown = field(default_factory=TickerCooldown)

    def _get(self, ticker: str) -> TickerCooldown:
        """Return (or lazily create) the cooldown for *ticker*."""
        if ticker not in self._cooldowns:
            self._cooldowns[ticker] = TickerCooldown()
        return self._cooldowns[ticker]

    def is_ready(self, ticker: str) -> bool:
        """Return ``True`` if both global and ticker limits allow a request."""
        return self._global.is_ready() and self._get(ticker).is_ready()

    def seconds_until_ready(self, ticker: str) -> float:
        """Max of global and per-ticker wait remaining."""
        return max(
            self._global.seconds_until_ready(),
            self._get(ticker).seconds_until_ready(),
        )

    def record_wait(self, ticker: str, wait_seconds: float) -> None:
        """Record a rate-limit signal for a specific *ticker*."""
        self._get(ticker).set_wait_until(wait_seconds)

    def record_global_wait(self, wait_seconds: float) -> None:
        """Record a global rate-limit signal (applies to all tickers)."""
        self._global.set_wait_until(wait_seconds)
