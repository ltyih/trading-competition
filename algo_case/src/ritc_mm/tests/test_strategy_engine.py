"""Tests for strategy engine orchestration."""

from __future__ import annotations

from unittest.mock import patch

from ritc_mm.api.models import CaseResponse, NewsItem, SecurityResponse
from ritc_mm.data.book import L1
from ritc_mm.data.state import GlobalState
from ritc_mm.strategy.engine import StrategyEngine
from ritc_mm.strategy.regimes import Regime


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


def _tunables() -> dict:
    return {
        "tick_size": 0.01,
        "rounding_decimals": 2,
        "base_hs_ticks": 2,
        "min_hs_ticks": 1,
        "base_size": 300,
        "max_quote_size": 1500,
        "soft_cap_tkr": 2500,
        "hard_cap_tkr": 5000,
        "inv_k": 1.2,
        "news_lockout_seconds": 1.0,
        "minute_closeout_start_s": 50,
        "heat_closeout_start_s": 270,
        "fv_ema_alpha": 0.2,
        "news_impulse_bps": 12,
        "news_positive_keywords": ["strong", "beat", "growth"],
        "news_negative_keywords": ["weak", "miss", "decline"],
    }


def _state() -> GlobalState:
    state = GlobalState(
        universe=["SPNG", "SMMR"],
        book_depth=20,
        tape_maxlen=100,
        news_max_items=100,
    )
    state.case = _case(10)
    state.positions_by_ticker["SPNG"] = SecurityResponse.model_validate({"ticker": "SPNG", "position": 0})
    state.positions_by_ticker["SMMR"] = SecurityResponse.model_validate({"ticker": "SMMR", "position": 0})
    state.l1["SPNG"] = L1(24.99, 100, 25.01, 100, 25.0, 0.02, 0.0)
    state.l1["SMMR"] = L1(24.98, 100, 25.02, 100, 25.0, 0.04, 0.0)
    return state


def test_step_returns_targets_for_all_tickers() -> None:
    state = _state()
    engine = StrategyEngine(_tunables())

    targets = engine.step(state, now_ts=100.0)

    assert set(targets.keys()) == {"SPNG", "SMMR"}
    assert targets["SPNG"].regime == Regime.NORMAL_MM


def test_news_event_produces_lockout_target() -> None:
    state = _state()
    state.news.apply([_news(1, "SPNG", "Strong growth", "beat")])
    engine = StrategyEngine(_tunables())

    targets = engine.step(state, now_ts=100.0)

    assert targets["SPNG"].regime == Regime.NEWS_LOCKOUT
    assert targets["SPNG"].cancel_all is True


def test_closeout_window_produces_cancel_targets() -> None:
    state = _state()
    state.case = _case(55)
    engine = StrategyEngine(_tunables())

    targets = engine.step(state, now_ts=100.0)

    assert targets["SPNG"].regime == Regime.CLOSEOUT
    assert targets["SMMR"].regime == Regime.CLOSEOUT
    assert targets["SPNG"].cancel_all is True


def test_strategy_step_does_not_call_execution_api_methods() -> None:
    state = _state()
    engine = StrategyEngine(_tunables())

    with patch("ritc_mm.api.client.ApiClient.submit_order", side_effect=AssertionError("unexpected")):
        with patch("ritc_mm.api.client.ApiClient.cancel_order", side_effect=AssertionError("unexpected")):
            with patch("ritc_mm.api.client.ApiClient.bulk_cancel", side_effect=AssertionError("unexpected")):
                engine.step(state, now_ts=100.0)
