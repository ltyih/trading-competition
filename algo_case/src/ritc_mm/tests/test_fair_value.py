"""Tests for fair value computation."""

from __future__ import annotations

import pytest

from ritc_mm.api.models import NewsItem
from ritc_mm.data.book import L1
from ritc_mm.data.state import GlobalState
from ritc_mm.strategy.fair_value import FairValueEngine


def _state() -> GlobalState:
    return GlobalState(
        universe=["SPNG"],
        book_depth=20,
        tape_maxlen=100,
        news_max_items=100,
    )


def _news(news_id: int, ticker: str, headline: str, body: str) -> NewsItem:
    return NewsItem.model_validate(
        {
            "news_id": news_id,
            "period": 1,
            "tick": 10,
            "ticker": ticker,
            "headline": headline,
            "body": body,
        }
    )


def _engine() -> FairValueEngine:
    return FairValueEngine(
        ema_alpha=0.2,
        news_impulse_bps=12,
        positive_keywords=["strong", "growth", "beat"],
        negative_keywords=["weak", "decline", "miss"],
    )


def test_ema_initializes_from_first_mid() -> None:
    state = _state()
    state.l1["SPNG"] = L1(25.0, 100, 25.02, 100, 25.01, 0.02, 0.0)
    engine = _engine()

    snap = engine.compute("SPNG", state, now_ts=1.0)

    assert snap.ema_mid == pytest.approx(25.01)
    assert snap.fv == pytest.approx(25.01)


def test_ema_updates_over_time() -> None:
    state = _state()
    engine = _engine()

    state.l1["SPNG"] = L1(25.0, 100, 25.0, 100, 25.0, 0.0, 0.0)
    first = engine.compute("SPNG", state, now_ts=1.0)

    state.l1["SPNG"] = L1(26.0, 100, 26.0, 100, 26.0, 0.0, 0.0)
    second = engine.compute("SPNG", state, now_ts=2.0)

    assert first.ema_mid == pytest.approx(25.0)
    assert second.ema_mid == pytest.approx(25.2)


def test_positive_news_increases_fv() -> None:
    state = _state()
    state.l1["SPNG"] = L1(25.0, 100, 25.0, 100, 25.0, 0.0, 0.0)
    state.news.apply([_news(1, "SPNG", "Strong growth", "Company beat estimates")])
    engine = _engine()

    snap = engine.compute("SPNG", state, now_ts=1.0)

    assert snap.news_impulse > 0.0
    assert snap.fv is not None and snap.fv > snap.ema_mid


def test_negative_news_decreases_fv() -> None:
    state = _state()
    state.l1["SPNG"] = L1(25.0, 100, 25.0, 100, 25.0, 0.0, 0.0)
    state.news.apply([_news(1, "SPNG", "Weak guidance", "Demand decline and miss")])
    engine = _engine()

    snap = engine.compute("SPNG", state, now_ts=1.0)

    assert snap.news_impulse < 0.0
    assert snap.fv is not None and snap.fv < snap.ema_mid


def test_same_news_id_is_not_applied_twice() -> None:
    state = _state()
    state.l1["SPNG"] = L1(25.0, 100, 25.0, 100, 25.0, 0.0, 0.0)
    state.news.apply([_news(1, "SPNG", "Strong growth", "beat")])
    engine = _engine()

    first = engine.compute("SPNG", state, now_ts=1.0)
    second = engine.compute("SPNG", state, now_ts=2.0)

    assert first.news_impulse > 0.0
    assert second.news_impulse == 0.0


def test_missing_l1_mid_returns_none_fv() -> None:
    state = _state()
    engine = _engine()

    snap = engine.compute("SPNG", state, now_ts=1.0)

    assert snap.fv is None
