"""Strategy orchestrator for dry-run regime/FV/quoting outputs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ritc_mm.data.state import GlobalState
from ritc_mm.strategy.fair_value import FairValueEngine, FairValueSnapshot
from ritc_mm.strategy.quoting import QuoteBuilder, QuoteTarget
from ritc_mm.strategy.regimes import RegimeDecision, RegimeEngine


class StrategyEngine:
    """Compose regime selection, fair-value estimation, and quote building."""

    def __init__(self, tunables: Mapping[str, Any]) -> None:
        self._regime_engine = RegimeEngine(
            news_lockout_seconds=float(tunables["news_lockout_seconds"]),
            minute_closeout_start_s=int(tunables["minute_closeout_start_s"]),
            heat_closeout_start_s=int(tunables["heat_closeout_start_s"]),
        )
        self._fair_value_engine = FairValueEngine(
            ema_alpha=float(tunables["fv_ema_alpha"]),
            news_impulse_bps=float(tunables["news_impulse_bps"]),
            positive_keywords=list(tunables["news_positive_keywords"]),
            negative_keywords=list(tunables["news_negative_keywords"]),
        )
        self._quote_builder = QuoteBuilder(
            tick_size=float(tunables["tick_size"]),
            rounding_decimals=int(tunables["rounding_decimals"]),
            base_hs_ticks=float(tunables["base_hs_ticks"]),
            min_hs_ticks=float(tunables["min_hs_ticks"]),
            base_size=float(tunables["base_size"]),
            max_quote_size=float(tunables["max_quote_size"]),
            soft_cap_tkr=float(tunables["soft_cap_tkr"]),
            hard_cap_tkr=float(tunables["hard_cap_tkr"]),
            inv_k=float(tunables["inv_k"]),
        )

    def step(self, state: GlobalState, now_ts: float | None = None) -> dict[str, QuoteTarget]:
        """Generate per-ticker quote targets from current state."""
        regime_decisions: dict[str, RegimeDecision] = self._regime_engine.select(state, now_ts=now_ts)

        fair_values: dict[str, FairValueSnapshot] = {}
        for ticker in state.universe:
            fair_values[ticker] = self._fair_value_engine.compute(ticker=ticker, state=state, now_ts=now_ts)

        return self._quote_builder.build_all(
            regime_decisions=regime_decisions,
            fair_values=fair_values,
            state=state,
        )
