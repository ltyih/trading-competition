"""Quote target construction for dry-run strategy mode."""

from __future__ import annotations

from dataclasses import dataclass

from ritc_mm.data.state import GlobalState
from ritc_mm.strategy.fair_value import FairValueSnapshot
from ritc_mm.strategy.regimes import Regime, RegimeDecision


@dataclass(frozen=True)
class SideQuote:
    """One side of a passive quote target."""

    price: float
    quantity: int


@dataclass(frozen=True)
class QuoteTarget:
    """Per-ticker quote intent output for downstream reconciliation."""

    ticker: str
    regime: Regime
    fair_value: float | None
    bid: SideQuote | None
    ask: SideQuote | None
    cancel_all: bool
    reason: str


class QuoteBuilder:
    """Build quote targets from regime and fair-value decisions."""

    def __init__(
        self,
        tick_size: float,
        rounding_decimals: int,
        base_hs_ticks: float,
        min_hs_ticks: float,
        base_size: float,
        max_quote_size: float,
        soft_cap_tkr: float,
        hard_cap_tkr: float,
        inv_k: float,
    ) -> None:
        self._tick_size = float(tick_size)
        self._rounding_decimals = int(rounding_decimals)
        self._base_hs_ticks = float(base_hs_ticks)
        self._min_hs_ticks = float(min_hs_ticks)
        self._base_size = float(base_size)
        self._max_quote_size = float(max_quote_size)
        self._soft_cap_tkr = float(soft_cap_tkr)
        self._hard_cap_tkr = float(hard_cap_tkr)
        self._inv_k = float(inv_k)

    @staticmethod
    def _clamp(value: float, lower: float, upper: float) -> float:
        return max(lower, min(upper, value))

    def _round_to_tick(self, price: float) -> float:
        if self._tick_size <= 0.0:
            return round(price, self._rounding_decimals)
        rounded = round(price / self._tick_size) * self._tick_size
        return round(rounded, self._rounding_decimals)

    def _quote_size(self) -> int:
        bounded = min(self._base_size, self._max_quote_size)
        return max(1, int(round(bounded)))

    def build_for_ticker(
        self,
        ticker: str,
        regime: RegimeDecision,
        fv: FairValueSnapshot,
        state: GlobalState,
    ) -> QuoteTarget:
        """Create quote target for one ticker."""
        if regime.regime in (Regime.NEWS_LOCKOUT, Regime.CLOSEOUT):
            return QuoteTarget(
                ticker=ticker,
                regime=regime.regime,
                fair_value=fv.fv,
                bid=None,
                ask=None,
                cancel_all=True,
                reason=regime.reason,
            )

        l1 = state.l1.get(ticker)
        if l1 is None or l1.mid is None or fv.fv is None:
            return QuoteTarget(
                ticker=ticker,
                regime=regime.regime,
                fair_value=fv.fv,
                bid=None,
                ask=None,
                cancel_all=True,
                reason="no_market_data",
            )

        position = 0.0
        security = state.positions_by_ticker.get(ticker)
        if security is not None:
            position = float(security.position)

        hs_px = max(self._min_hs_ticks, self._base_hs_ticks) * self._tick_size
        if self._soft_cap_tkr <= 0.0:
            inv_norm = 0.0
        else:
            inv_norm = self._clamp(position / self._soft_cap_tkr, -1.0, 1.0)
        skew_px = self._inv_k * inv_norm * hs_px

        bid_px = self._round_to_tick(float(fv.fv) - hs_px - skew_px)
        ask_px = self._round_to_tick(float(fv.fv) + hs_px - skew_px)

        quantity = self._quote_size()

        allow_bid = position < self._hard_cap_tkr
        allow_ask = position > -self._hard_cap_tkr

        bid = SideQuote(price=bid_px, quantity=quantity) if allow_bid else None
        ask = SideQuote(price=ask_px, quantity=quantity) if allow_ask else None

        if bid is not None and ask is not None and bid.price >= ask.price:
            return QuoteTarget(
                ticker=ticker,
                regime=regime.regime,
                fair_value=fv.fv,
                bid=None,
                ask=None,
                cancel_all=True,
                reason="crossed_quote_after_rounding",
            )

        if bid is None and ask is None:
            return QuoteTarget(
                ticker=ticker,
                regime=regime.regime,
                fair_value=fv.fv,
                bid=None,
                ask=None,
                cancel_all=True,
                reason="hard_cap_block",
            )

        return QuoteTarget(
            ticker=ticker,
            regime=regime.regime,
            fair_value=fv.fv,
            bid=bid,
            ask=ask,
            cancel_all=False,
            reason=regime.reason,
        )

    def build_all(
        self,
        regime_decisions: dict[str, RegimeDecision],
        fair_values: dict[str, FairValueSnapshot],
        state: GlobalState,
    ) -> dict[str, QuoteTarget]:
        """Create quote targets for all tickers in the universe."""
        targets: dict[str, QuoteTarget] = {}
        for ticker in state.universe:
            regime = regime_decisions[ticker]
            fv = fair_values[ticker]
            targets[ticker] = self.build_for_ticker(
                ticker=ticker,
                regime=regime,
                fv=fv,
                state=state,
            )
        return targets
