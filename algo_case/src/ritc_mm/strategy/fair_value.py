"""Fair value estimation for dry-run strategy mode."""

from __future__ import annotations

from dataclasses import dataclass
import time

from ritc_mm.data.news import StoredNews
from ritc_mm.data.state import GlobalState


@dataclass
class FairValueState:
    """Per-ticker running FV model state."""

    ema_mid: float | None
    last_mid: float | None
    last_update_ts: float | None
    last_news_id_applied: int


@dataclass(frozen=True)
class FairValueSnapshot:
    """Computed fair-value view for a ticker at one step."""

    fv: float | None
    ema_mid: float | None
    news_impulse: float


class FairValueEngine:
    """EMA-based fair value with one-shot rule-based news impulse."""

    def __init__(
        self,
        ema_alpha: float,
        news_impulse_bps: float,
        positive_keywords: list[str],
        negative_keywords: list[str],
    ) -> None:
        self._ema_alpha = float(ema_alpha)
        self._news_impulse_bps = float(news_impulse_bps)
        self._positive_keywords = [kw.lower() for kw in positive_keywords]
        self._negative_keywords = [kw.lower() for kw in negative_keywords]
        self._state: dict[str, FairValueState] = {}

    def _get_state(self, ticker: str) -> FairValueState:
        if ticker not in self._state:
            self._state[ticker] = FairValueState(
                ema_mid=None,
                last_mid=None,
                last_update_ts=None,
                last_news_id_applied=0,
            )
        return self._state[ticker]

    @staticmethod
    def _latest_relevant_news(state: GlobalState, ticker: str) -> StoredNews | None:
        candidates: list[StoredNews] = []
        t_news = state.news.get_by_ticker(ticker, limit=1)
        if t_news:
            candidates.append(t_news[0])
        m_news = state.news.get_by_ticker("", limit=1)
        if m_news:
            candidates.append(m_news[0])

        if not candidates:
            return None
        return max(candidates, key=lambda item: item.news_id)

    def _polarity(self, text: str) -> int:
        lower = text.lower()
        positive = sum(1 for kw in self._positive_keywords if kw and kw in lower)
        negative = sum(1 for kw in self._negative_keywords if kw and kw in lower)
        score = positive - negative
        if score > 0:
            return 1
        if score < 0:
            return -1
        return 0

    def compute(self, ticker: str, state: GlobalState, now_ts: float | None = None) -> FairValueSnapshot:
        """Compute fair value for ticker from latest state."""
        now = float(now_ts) if now_ts is not None else time.time()
        model_state = self._get_state(ticker)

        l1 = state.l1.get(ticker)
        mid = l1.mid if l1 is not None else None
        if mid is None:
            model_state.last_update_ts = now
            return FairValueSnapshot(fv=None, ema_mid=model_state.ema_mid, news_impulse=0.0)

        if model_state.ema_mid is None:
            ema_mid = float(mid)
        else:
            ema_mid = self._ema_alpha * float(mid) + (1.0 - self._ema_alpha) * model_state.ema_mid

        model_state.ema_mid = ema_mid
        model_state.last_mid = float(mid)
        model_state.last_update_ts = now

        news_impulse = 0.0
        latest_news = self._latest_relevant_news(state, ticker)
        if latest_news is not None and latest_news.news_id > model_state.last_news_id_applied:
            combined_text = f"{latest_news.headline} {latest_news.body}"
            sign = self._polarity(combined_text)
            news_impulse = sign * self._news_impulse_bps * 1e-4 * ema_mid
            model_state.last_news_id_applied = latest_news.news_id

        return FairValueSnapshot(
            fv=ema_mid + news_impulse,
            ema_mid=ema_mid,
            news_impulse=news_impulse,
        )
