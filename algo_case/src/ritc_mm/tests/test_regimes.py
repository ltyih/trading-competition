"""Tests for strategy regime selection."""

from __future__ import annotations

from *REMOVED*_mm.api.models import CaseResponse, NewsItem
from *REMOVED*_mm.data.state import GlobalState
from *REMOVED*_mm.strategy.regimes import Regime, RegimeEngine


def _case(tick: int) -> CaseResponse:
    return CaseResponse.model_validate(
        {
            "name": "Algo-MM",
            "period": 1,
            "tick": tick,
            "ticks_per_period": 300,
            "total_periods": 1,
            "status": "ACTIVE",
            "is_enforce_trading_limits": True,
        }
    )


def _news(news_id: int, ticker: str) -> NewsItem:
    return NewsItem.model_validate(
        {
            "news_id": news_id,
            "period": 1,
            "tick": 10,
            "ticker": ticker,
            "headline": "headline",
            "body": "body",
        }
    )


def _state() -> GlobalState:
    state = GlobalState(
        universe=["SPNG", "SMMR"],
        book_depth=20,
        tape_maxlen=100,
        news_max_items=100,
    )
    state.case = _case(10)
    return state


def test_default_regime_is_normal_without_triggers() -> None:
    state = _state()
    engine = RegimeEngine(news_lockout_seconds=1.0, minute_closeout_start_s=50, heat_closeout_start_s=270)

    decisions = engine.select(state, now_ts=100.0)

    assert decisions["SPNG"].regime == Regime.NORMAL_MM
    assert decisions["SMMR"].regime == Regime.NORMAL_MM


def test_ticker_news_enters_lockout_for_affected_ticker() -> None:
    state = _state()
    state.news.apply([_news(1, "SPNG")])
    engine = RegimeEngine(news_lockout_seconds=2.0, minute_closeout_start_s=50, heat_closeout_start_s=270)

    decisions = engine.select(state, now_ts=100.0)

    assert decisions["SPNG"].regime == Regime.NEWS_LOCKOUT
    assert decisions["SMMR"].regime == Regime.NORMAL_MM


def test_market_wide_news_locks_all_tickers() -> None:
    state = _state()
    state.news.apply([_news(1, "")])
    engine = RegimeEngine(news_lockout_seconds=2.0, minute_closeout_start_s=50, heat_closeout_start_s=270)

    decisions = engine.select(state, now_ts=100.0)

    assert decisions["SPNG"].regime == Regime.NEWS_LOCKOUT
    assert decisions["SMMR"].regime == Regime.NEWS_LOCKOUT


def test_lockout_expires_back_to_normal() -> None:
    state = _state()
    state.news.apply([_news(1, "SPNG")])
    engine = RegimeEngine(news_lockout_seconds=1.0, minute_closeout_start_s=50, heat_closeout_start_s=270)

    first = engine.select(state, now_ts=100.0)
    second = engine.select(state, now_ts=100.5)
    third = engine.select(state, now_ts=101.1)

    assert first["SPNG"].regime == Regime.NEWS_LOCKOUT
    assert second["SPNG"].regime == Regime.NEWS_LOCKOUT
    assert third["SPNG"].regime == Regime.NORMAL_MM


def test_closeout_overrides_other_regimes() -> None:
    state = _state()
    state.case = _case(55)
    state.news.apply([_news(1, "SPNG")])
    engine = RegimeEngine(news_lockout_seconds=2.0, minute_closeout_start_s=50, heat_closeout_start_s=270)

    decisions = engine.select(state, now_ts=100.0)

    assert decisions["SPNG"].regime == Regime.CLOSEOUT
    assert decisions["SMMR"].regime == Regime.CLOSEOUT
    assert decisions["SPNG"].reason in {"minute_closeout_window", "heat_closeout_window"}
