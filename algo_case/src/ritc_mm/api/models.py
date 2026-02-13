"""Pydantic models for RIT Client REST API responses.

Every model mirrors the response schema defined in ``rit_api_documentation.yaml``
(Swagger 2.0, version 1.0.3).  Fields use ``None`` defaults where the API may
omit a value so that the caller never hits a validation error on optional data.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums (match API definitions section exactly)
# ---------------------------------------------------------------------------

class CaseStatus(str, Enum):
    """Case lifecycle states — ``/case`` → ``status``."""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


class OrderAction(str, Enum):
    """Order side — ``BUY`` or ``SELL``."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Order type — ``MARKET`` or ``LIMIT``."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    """Order lifecycle — ``OPEN`` / ``TRANSACTED`` / ``CANCELLED``."""

    OPEN = "OPEN"
    TRANSACTED = "TRANSACTED"
    CANCELLED = "CANCELLED"


class SecurityType(str, Enum):
    """Security classification — ``/securities`` → ``type``."""

    SPOT = "SPOT"
    FUTURE = "FUTURE"
    INDEX = "INDEX"
    OPTION = "OPTION"
    STOCK = "STOCK"
    CURRENCY = "CURRENCY"
    BOND = "BOND"
    RATE = "RATE"
    FORWARD = "FORWARD"
    SWAP = "SWAP"
    SWAP_BOM = "SWAP_BOM"
    SPRE = "SPRE"


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class CaseResponse(BaseModel):
    """``GET /case`` response."""

    name: str
    period: int
    tick: int
    ticks_per_period: int
    total_periods: int
    status: CaseStatus
    is_enforce_trading_limits: bool = False


class TraderResponse(BaseModel):
    """``GET /trader`` response."""

    trader_id: str
    first_name: str
    last_name: str
    nlv: float


class SecurityLimitInfo(BaseModel):
    """Per-security limit entry nested inside ``SecurityResponse``."""

    name: str
    units: float


class SecurityResponse(BaseModel):
    """Single element from ``GET /securities`` response array."""

    ticker: str
    type: SecurityType | None = None
    size: int | None = None
    position: float = 0.0
    vwap: float = 0.0
    nlv: float = 0.0
    last: float | None = None
    bid: float | None = None
    bid_size: float | None = None
    ask: float | None = None
    ask_size: float | None = None
    volume: float = 0.0
    unrealized: float = 0.0
    realized: float = 0.0
    currency: str | None = None
    total_volume: float = 0.0
    limits: list[SecurityLimitInfo] = Field(default_factory=list)
    interest_rate: float | None = None
    is_tradeable: bool = True
    is_shortable: bool = True
    start_period: int | None = None
    stop_period: int | None = None
    description: str | None = None
    unit_multiplier: int | None = None
    display_unit: str | None = None
    start_price: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    quoted_decimals: int | None = None
    trading_fee: float = 0.0
    limit_order_rebate: float = 0.0
    min_trade_size: float | None = None
    max_trade_size: float | None = None
    api_orders_per_second: int | None = None
    execution_delay_ms: int | None = None


class OrderResponse(BaseModel):
    """Order object returned by ``GET /orders``, ``POST /orders``, etc."""

    order_id: int
    period: int
    tick: int
    trader_id: str | None = None
    ticker: str
    type: OrderType
    quantity: float
    action: OrderAction
    price: float | None = None
    quantity_filled: float = 0.0
    vwap: float | None = None
    status: OrderStatus


class BookResponse(BaseModel):
    """``GET /securities/book`` response — bid and ask sides."""

    bid: list[OrderResponse] = Field(default_factory=list)
    ask: list[OrderResponse] = Field(default_factory=list)


class TasEntry(BaseModel):
    """Single time-and-sales print from ``GET /securities/tas``."""

    id: int
    period: int
    tick: int
    price: float
    quantity: float


class NewsItem(BaseModel):
    """Single news item from ``GET /news``."""

    news_id: int
    period: int
    tick: int
    ticker: str = ""
    headline: str = ""
    body: str = ""


class LimitInfo(BaseModel):
    """Single entry from ``GET /limits``."""

    name: str
    gross: float
    net: float
    gross_limit: float
    net_limit: float
    gross_fine: float = 0.0
    net_fine: float = 0.0


class CancelResult(BaseModel):
    """``POST /commands/cancel`` response."""

    cancelled_order_ids: list[int] = Field(default_factory=list)


class OhlcEntry(BaseModel):
    """Single OHLC bar from ``GET /securities/history``."""

    tick: int
    open: float
    high: float
    low: float
    close: float
