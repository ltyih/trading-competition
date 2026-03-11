"""Tests for quote target generation."""

from __future__ import annotations

from *REMOVED*_mm.api.models import SecurityResponse
from *REMOVED*_mm.data.book import L1
from *REMOVED*_mm.data.state import GlobalState
from *REMOVED*_mm.strategy.fair_value import FairValueSnapshot
from *REMOVED*_mm.strategy.quoting import QuoteBuilder
from *REMOVED*_mm.strategy.regimes import Regime, RegimeDecision


def _builder() -> QuoteBuilder:
    return QuoteBuilder(
        tick_size=0.01,
        rounding_decimals=2,
        base_hs_ticks=2,
        min_hs_ticks=1,
        base_size=300,
        max_quote_size=1500,
        soft_cap_tkr=2500,
        hard_cap_tkr=5000,
        inv_k=1.2,
    )


def _state(position: float = 0.0, include_l1: bool = True) -> GlobalState:
    state = GlobalState(
        universe=["SPNG"],
        book_depth=20,
        tape_maxlen=100,
        news_max_items=100,
    )
    state.positions_by_ticker["SPNG"] = SecurityResponse.model_validate(
        {
            "ticker": "SPNG",
            "position": position,
        }
    )
    if include_l1:
        state.l1["SPNG"] = L1(24.99, 100, 25.01, 100, 25.0, 0.02, 0.0)
    return state


def test_normal_regime_emits_two_sided_quotes() -> None:
    builder = _builder()
    state = _state(position=0)

    target = builder.build_for_ticker(
        ticker="SPNG",
        regime=RegimeDecision(Regime.NORMAL_MM, "normal"),
        fv=FairValueSnapshot(fv=25.0, ema_mid=25.0, news_impulse=0.0),
        state=state,
    )

    assert target.cancel_all is False
    assert target.bid is not None and target.ask is not None
    assert target.bid.price == 24.98
    assert target.ask.price == 25.02


def test_inventory_skew_pushes_quotes_down_for_long_position() -> None:
    builder = _builder()
    neutral_state = _state(position=0)
    long_state = _state(position=2500)

    neutral = builder.build_for_ticker(
        ticker="SPNG",
        regime=RegimeDecision(Regime.NORMAL_MM, "normal"),
        fv=FairValueSnapshot(fv=25.0, ema_mid=25.0, news_impulse=0.0),
        state=neutral_state,
    )
    long_target = builder.build_for_ticker(
        ticker="SPNG",
        regime=RegimeDecision(Regime.NORMAL_MM, "normal"),
        fv=FairValueSnapshot(fv=25.0, ema_mid=25.0, news_impulse=0.0),
        state=long_state,
    )

    assert long_target.bid is not None and long_target.ask is not None
    assert neutral.bid is not None and neutral.ask is not None
    assert long_target.bid.price < neutral.bid.price
    assert long_target.ask.price < neutral.ask.price


def test_hard_cap_suppresses_inventory_increasing_side() -> None:
    builder = _builder()
    state = _state(position=5000)

    target = builder.build_for_ticker(
        ticker="SPNG",
        regime=RegimeDecision(Regime.NORMAL_MM, "normal"),
        fv=FairValueSnapshot(fv=25.0, ema_mid=25.0, news_impulse=0.0),
        state=state,
    )

    assert target.bid is None
    assert target.ask is not None
    assert target.cancel_all is False


def test_news_lockout_and_closeout_cancel_all() -> None:
    builder = _builder()
    state = _state(position=0)

    lockout = builder.build_for_ticker(
        ticker="SPNG",
        regime=RegimeDecision(Regime.NEWS_LOCKOUT, "news_lockout_active"),
        fv=FairValueSnapshot(fv=25.0, ema_mid=25.0, news_impulse=0.0),
        state=state,
    )
    closeout = builder.build_for_ticker(
        ticker="SPNG",
        regime=RegimeDecision(Regime.CLOSEOUT, "minute_closeout_window"),
        fv=FairValueSnapshot(fv=25.0, ema_mid=25.0, news_impulse=0.0),
        state=state,
    )

    assert lockout.cancel_all is True
    assert closeout.cancel_all is True


def test_missing_market_data_emits_cancel_all() -> None:
    builder = _builder()
    state = _state(position=0, include_l1=False)

    target = builder.build_for_ticker(
        ticker="SPNG",
        regime=RegimeDecision(Regime.NORMAL_MM, "normal"),
        fv=FairValueSnapshot(fv=None, ema_mid=None, news_impulse=0.0),
        state=state,
    )

    assert target.cancel_all is True
    assert target.reason == "no_market_data"
