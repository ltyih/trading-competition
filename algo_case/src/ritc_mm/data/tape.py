"""Time-and-sales ingestion helpers."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import time

from *REMOVED*_mm.api.models import TasEntry


@dataclass(frozen=True)
class Print:
    """Normalized tape print."""

    id: int
    period: int
    tick: int
    px: float
    qty: float
    ts: float


class TapeBuffer:
    """Per-ticker ring buffers and incremental pointers for tape prints."""

    def __init__(self, maxlen_per_ticker: int) -> None:
        self._maxlen_per_ticker = maxlen_per_ticker
        self._buffers: dict[str, deque[Print]] = {}
        self._last_ids: dict[str, int] = {}

    def _buffer(self, ticker: str) -> deque[Print]:
        if ticker not in self._buffers:
            self._buffers[ticker] = deque(maxlen=self._maxlen_per_ticker)
        return self._buffers[ticker]

    def last_id(self, ticker: str) -> int:
        """Last accepted print id for ticker; ``0`` if no prints ingested yet."""
        return self._last_ids.get(ticker, 0)

    def apply(self, ticker: str, entries: list[TasEntry]) -> list[Print]:
        """Apply incremental TAS entries and return newly accepted prints."""
        accepted: list[Print] = []
        current_last = self.last_id(ticker)
        buffer = self._buffer(ticker)

        for entry in sorted(entries, key=lambda item: int(item.id)):
            entry_id = int(entry.id)
            if entry_id <= current_last:
                continue

            p = Print(
                id=entry_id,
                period=int(entry.period),
                tick=int(entry.tick),
                px=float(entry.price),
                qty=float(entry.quantity),
                ts=time.time(),
            )
            buffer.append(p)
            accepted.append(p)
            current_last = entry_id

        self._last_ids[ticker] = current_last
        return accepted

    def get_recent(self, ticker: str, limit: int | None = None) -> list[Print]:
        """Return most recent prints for a ticker in chronological order."""
        items = list(self._buffer(ticker))
        if limit is None or limit >= len(items):
            return items
        return items[-limit:]
