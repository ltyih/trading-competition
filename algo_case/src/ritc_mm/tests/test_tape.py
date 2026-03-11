"""Tests for incremental TAS buffering."""

from __future__ import annotations

from *REMOVED*_mm.api.models import TasEntry
from *REMOVED*_mm.data.tape import TapeBuffer


def _tas(entry_id: int, tick: int = 1, px: float = 25.0, qty: float = 100.0) -> TasEntry:
    return TasEntry.model_validate(
        {
            "id": entry_id,
            "period": 1,
            "tick": tick,
            "price": px,
            "quantity": qty,
        }
    )


def test_apply_deduplicates_and_tracks_last_id_per_ticker() -> None:
    tape = TapeBuffer(maxlen_per_ticker=10)

    accepted = tape.apply("SPNG", [_tas(2), _tas(1), _tas(2), _tas(3)])
    assert [p.id for p in accepted] == [1, 2, 3]
    assert tape.last_id("SPNG") == 3

    accepted_again = tape.apply("SPNG", [_tas(2), _tas(3)])
    assert accepted_again == []
    assert tape.last_id("SPNG") == 3

    assert tape.last_id("SMMR") == 0


def test_buffer_truncates_to_maxlen() -> None:
    tape = TapeBuffer(maxlen_per_ticker=2)

    tape.apply("SPNG", [_tas(1), _tas(2), _tas(3)])
    recent = tape.get_recent("SPNG")

    assert [p.id for p in recent] == [2, 3]


def test_recent_limit_returns_last_n_in_chronological_order() -> None:
    tape = TapeBuffer(maxlen_per_ticker=10)
    tape.apply("SPNG", [_tas(1), _tas(2), _tas(3), _tas(4)])

    recent = tape.get_recent("SPNG", limit=2)
    assert [p.id for p in recent] == [3, 4]
