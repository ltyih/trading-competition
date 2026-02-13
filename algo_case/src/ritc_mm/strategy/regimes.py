"""Regime selection state machine for strategy dry-run mode."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import time

from ritc_mm.data.state import GlobalState


class Regime(Enum):
    """Supported Feature 4 regimes."""

    NORMAL_MM = 1
    NEWS_LOCKOUT = 2
    CLOSEOUT = 3


@dataclass
class TickerRegimeState:
    """Per-ticker persistent regime state."""

    regime: Regime
    since_ts: float
    lockout_until_ts: float
    last_news_id_seen: int


@dataclass(frozen=True)
class RegimeDecision:
    """Regime output for a ticker at one strategy step."""

    regime: Regime
    reason: str


class RegimeEngine:
    """Select regimes from state snapshots with simple transition rules."""

    def __init__(
        self,
        news_lockout_seconds: float,
        minute_closeout_start_s: int,
        heat_closeout_start_s: int,
    ) -> None:
        self._news_lockout_seconds = float(news_lockout_seconds)
        self._minute_closeout_start_s = int(minute_closeout_start_s)
        self._heat_closeout_start_s = int(heat_closeout_start_s)
        self._state: dict[str, TickerRegimeState] = {}

    def _get_state(self, ticker: str, now_ts: float) -> TickerRegimeState:
        if ticker not in self._state:
            self._state[ticker] = TickerRegimeState(
                regime=Regime.NORMAL_MM,
                since_ts=now_ts,
                lockout_until_ts=0.0,
                last_news_id_seen=0,
            )
        return self._state[ticker]

    @staticmethod
    def _latest_news_id(state: GlobalState, ticker: str) -> int:
        ticker_news = state.news.get_by_ticker(ticker, limit=1)
        market_news = state.news.get_by_ticker("", limit=1)

        latest = 0
        if ticker_news:
            latest = max(latest, ticker_news[0].news_id)
        if market_news:
            latest = max(latest, market_news[0].news_id)
        return latest

    def _closeout_reason(self, state: GlobalState) -> str | None:
        if state.case is None:
            return None

        tick = int(state.case.tick)
        seconds_into_minute = tick % 60
        ticks_per_period = max(int(state.case.ticks_per_period), 1)
        tick_in_period = tick % ticks_per_period

        if tick_in_period >= self._heat_closeout_start_s:
            return "heat_closeout_window"
        if seconds_into_minute >= self._minute_closeout_start_s:
            return "minute_closeout_window"
        return None

    @staticmethod
    def _transition(ticker_state: TickerRegimeState, regime: Regime, now_ts: float) -> None:
        if ticker_state.regime != regime:
            ticker_state.regime = regime
            ticker_state.since_ts = now_ts

    def select(self, state: GlobalState, now_ts: float | None = None) -> dict[str, RegimeDecision]:
        """Return per-ticker regime decisions for the current state snapshot."""
        now = float(now_ts) if now_ts is not None else time.time()
        closeout_reason = self._closeout_reason(state)

        decisions: dict[str, RegimeDecision] = {}
        for ticker in state.universe:
            ticker_state = self._get_state(ticker, now)
            latest_news_id = self._latest_news_id(state, ticker)
            has_new_news = latest_news_id > ticker_state.last_news_id_seen
            ticker_state.last_news_id_seen = max(ticker_state.last_news_id_seen, latest_news_id)

            if closeout_reason is not None:
                self._transition(ticker_state, Regime.CLOSEOUT, now)
                decisions[ticker] = RegimeDecision(regime=Regime.CLOSEOUT, reason=closeout_reason)
                continue

            if has_new_news:
                ticker_state.lockout_until_ts = now + self._news_lockout_seconds
                self._transition(ticker_state, Regime.NEWS_LOCKOUT, now)
                decisions[ticker] = RegimeDecision(regime=Regime.NEWS_LOCKOUT, reason="news_lockout_new_event")
                continue

            if now < ticker_state.lockout_until_ts:
                self._transition(ticker_state, Regime.NEWS_LOCKOUT, now)
                decisions[ticker] = RegimeDecision(regime=Regime.NEWS_LOCKOUT, reason="news_lockout_active")
                continue

            self._transition(ticker_state, Regime.NORMAL_MM, now)
            decisions[ticker] = RegimeDecision(regime=Regime.NORMAL_MM, reason="normal")

        return decisions
