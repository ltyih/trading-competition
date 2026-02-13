"""Comprehensive tests for the ``ritc_mm.api`` package.

All HTTP interactions are mocked via ``unittest.mock.patch`` so these tests
run on **any OS** without a live RIT Client connection.  This is essential
because the RIT simulator only runs on Windows.

Test categories:
1. **Models** — validate Pydantic parsing of real-world-shaped API payloads.
2. **Errors** — verify exception hierarchy and attributes.
3. **RateLimitTracker** — test per-ticker and global cooldown logic.
4. **ApiClient** — test retry/backoff, 429 handling, and response mapping
   with mocked ``requests.Session``.
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
import requests

from ritc_mm.api.client import ApiClient
from ritc_mm.api.errors import (
    AuthenticationError,
    ConnectionFailure,
    EndpointNotFoundError,
    RateLimitError,
    ServerError,
    UnexpectedStatusError,
)
from ritc_mm.api.models import (
    BookResponse,
    CancelResult,
    CaseResponse,
    CaseStatus,
    LimitInfo,
    NewsItem,
    OhlcEntry,
    OrderAction,
    OrderResponse,
    OrderStatus,
    OrderType,
    SecurityResponse,
    TasEntry,
    TraderResponse,
)
from ritc_mm.api.ratelimit import RateLimitTracker, TickerCooldown
from ritc_mm.telemetry.logger import LoggerConfig, get_logger


# -----------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------


@pytest.fixture()
def logger():
    """Quiet structured logger for tests (no console or file output)."""
    return get_logger(
        "test.api",
        LoggerConfig(level="DEBUG", console_enabled=False, file_enabled=False),
    )


def _mock_response(
    status_code: int = 200,
    json_data: dict | list | None = None,
    headers: dict[str, str] | None = None,
    text: str = "",
) -> MagicMock:
    """Create a ``MagicMock`` that behaves like a ``requests.Response``."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.headers = headers or {}
    resp.text = text
    return resp


# -----------------------------------------------------------------------
# 1) Model parsing tests
# -----------------------------------------------------------------------


class TestCaseResponseModel:
    """Validates ``CaseResponse`` parsing against the API spec."""

    def test_parse_minimal(self):
        data = {
            "name": "Algo-MM",
            "period": 1,
            "tick": 42,
            "ticks_per_period": 300,
            "total_periods": 1,
            "status": "ACTIVE",
        }
        cr = CaseResponse.model_validate(data)
        assert cr.name == "Algo-MM"
        assert cr.tick == 42
        assert cr.status == CaseStatus.ACTIVE
        assert cr.is_enforce_trading_limits is False

    def test_parse_with_limits_enforced(self):
        data = {
            "name": "Algo-MM",
            "period": 1,
            "tick": 1,
            "ticks_per_period": 300,
            "total_periods": 1,
            "status": "PAUSED",
            "is_enforce_trading_limits": True,
        }
        cr = CaseResponse.model_validate(data)
        assert cr.status == CaseStatus.PAUSED
        assert cr.is_enforce_trading_limits is True


class TestOrderResponseModel:
    """Validates ``OrderResponse`` parsing."""

    def test_parse_limit_order(self):
        data = {
            "order_id": 1221,
            "period": 1,
            "tick": 10,
            "trader_id": "trader49",
            "ticker": "SPNG",
            "type": "LIMIT",
            "quantity": 500,
            "action": "BUY",
            "price": 25.05,
            "quantity_filled": 100,
            "vwap": 25.04,
            "status": "OPEN",
        }
        o = OrderResponse.model_validate(data)
        assert o.order_id == 1221
        assert o.type == OrderType.LIMIT
        assert o.action == OrderAction.BUY
        assert o.status == OrderStatus.OPEN
        assert o.quantity_filled == 100

    def test_parse_market_order_null_price(self):
        data = {
            "order_id": 999,
            "period": 1,
            "tick": 5,
            "ticker": "WNTR",
            "type": "MARKET",
            "quantity": 200,
            "action": "SELL",
            "price": None,
            "quantity_filled": 200,
            "vwap": 24.90,
            "status": "TRANSACTED",
        }
        o = OrderResponse.model_validate(data)
        assert o.price is None
        assert o.status == OrderStatus.TRANSACTED


class TestBookResponseModel:
    """Validates ``BookResponse`` parsing."""

    def test_parse_book(self):
        bid = {
            "order_id": 1,
            "period": 1,
            "tick": 10,
            "ticker": "SPNG",
            "type": "LIMIT",
            "quantity": 300,
            "action": "BUY",
            "price": 24.99,
            "quantity_filled": 0,
            "vwap": None,
            "status": "OPEN",
        }
        ask = {
            "order_id": 2,
            "period": 1,
            "tick": 10,
            "ticker": "SPNG",
            "type": "LIMIT",
            "quantity": 200,
            "action": "SELL",
            "price": 25.01,
            "quantity_filled": 0,
            "vwap": None,
            "status": "OPEN",
        }
        book = BookResponse.model_validate({"bid": [bid], "ask": [ask]})
        assert len(book.bid) == 1
        assert len(book.ask) == 1
        assert book.bid[0].price == 24.99

    def test_parse_empty_book(self):
        book = BookResponse.model_validate({"bid": [], "ask": []})
        assert book.bid == []
        assert book.ask == []


class TestSecurityResponseModel:
    """Validates ``SecurityResponse`` parsing."""

    def test_parse_full_payload(self):
        data = {
            "ticker": "SMMR",
            "type": "STOCK",
            "size": 1,
            "position": 500,
            "vwap": 25.12,
            "nlv": 12560.0,
            "last": 25.10,
            "bid": 25.09,
            "bid_size": 1000,
            "ask": 25.11,
            "ask_size": 800,
            "volume": 5000,
            "unrealized": 120.0,
            "realized": -5.0,
            "currency": "CAD",
            "total_volume": 20000,
            "limits": [{"name": "Gross", "units": 500}],
            "is_tradeable": True,
            "is_shortable": True,
            "quoted_decimals": 2,
            "trading_fee": 0.02,
            "limit_order_rebate": 0.01,
            "max_trade_size": 10000,
            "api_orders_per_second": 10,
        }
        sec = SecurityResponse.model_validate(data)
        assert sec.ticker == "SMMR"
        assert sec.trading_fee == 0.02
        assert sec.limit_order_rebate == 0.01
        assert sec.limits[0].name == "Gross"


class TestTasEntryModel:
    """Validates ``TasEntry`` parsing."""

    def test_parse(self):
        data = {"id": 10, "period": 1, "tick": 50, "price": 4.10, "quantity": 10}
        t = TasEntry.model_validate(data)
        assert t.id == 10
        assert t.price == 4.10


class TestNewsItemModel:
    """Validates ``NewsItem`` parsing."""

    def test_parse(self):
        data = {
            "news_id": 123,
            "period": 1,
            "tick": 60,
            "ticker": "SPNG",
            "headline": "SPNG up",
            "body": "Strong demand...",
        }
        n = NewsItem.model_validate(data)
        assert n.news_id == 123
        assert n.ticker == "SPNG"


class TestLimitInfoModel:
    """Validates ``LimitInfo`` parsing."""

    def test_parse(self):
        data = {
            "name": "Aggregate",
            "gross": 5000,
            "net": 2000,
            "gross_limit": 15000,
            "net_limit": 10000,
            "gross_fine": 10.0,
            "net_fine": 5.0,
        }
        li = LimitInfo.model_validate(data)
        assert li.gross_limit == 15000
        assert li.gross_fine == 10.0


class TestCancelResultModel:
    """Validates ``CancelResult`` parsing."""

    def test_parse(self):
        data = {"cancelled_order_ids": [12, 13, 91]}
        cr = CancelResult.model_validate(data)
        assert cr.cancelled_order_ids == [12, 13, 91]

    def test_parse_empty(self):
        data = {"cancelled_order_ids": []}
        cr = CancelResult.model_validate(data)
        assert cr.cancelled_order_ids == []


# -----------------------------------------------------------------------
# 2) Error hierarchy tests
# -----------------------------------------------------------------------


class TestErrors:
    """Verify error class attributes and hierarchy."""

    def test_authentication_error(self):
        err = AuthenticationError()
        assert err.status_code == 401
        assert "401" in str(err)

    def test_rate_limit_error_retry_after(self):
        err = RateLimitError(retry_after=0.5)
        assert err.retry_after == 0.5
        assert err.status_code == 429

    def test_server_error(self):
        err = ServerError(status_code=502)
        assert err.status_code == 502

    def test_connection_failure_no_status(self):
        err = ConnectionFailure()
        assert err.status_code is None

    def test_unexpected_status_truncates_body(self):
        err = UnexpectedStatusError(status_code=418, body="x" * 300)
        # body truncated to 200 chars in message
        assert len(str(err)) < 300


# -----------------------------------------------------------------------
# 3) RateLimitTracker tests
# -----------------------------------------------------------------------


class TestRateLimitTracker:
    """Validate per-ticker and global cooldown logic."""

    def test_initially_ready(self):
        tracker = RateLimitTracker()
        assert tracker.is_ready("SPNG") is True
        assert tracker.seconds_until_ready("SPNG") == 0.0

    def test_record_wait_blocks_ticker(self):
        tracker = RateLimitTracker()
        tracker.record_wait("SPNG", 10.0)
        assert tracker.is_ready("SPNG") is False
        assert tracker.seconds_until_ready("SPNG") > 0.0
        # Other tickers remain unaffected
        assert tracker.is_ready("SMMR") is True

    def test_global_wait_blocks_all(self):
        tracker = RateLimitTracker()
        tracker.record_global_wait(10.0)
        assert tracker.is_ready("SPNG") is False
        assert tracker.is_ready("WNTR") is False

    def test_cooldown_expires(self):
        cd = TickerCooldown()
        cd.set_wait_until(0.0)  # expires immediately
        assert cd.is_ready() is True


# -----------------------------------------------------------------------
# 4) ApiClient tests (mocked HTTP)
# -----------------------------------------------------------------------


SAMPLE_CASE = {
    "name": "Algo-MM",
    "period": 1,
    "tick": 42,
    "ticks_per_period": 300,
    "total_periods": 1,
    "status": "ACTIVE",
    "is_enforce_trading_limits": True,
}

SAMPLE_ORDER = {
    "order_id": 100,
    "period": 1,
    "tick": 15,
    "trader_id": "bot1",
    "ticker": "SPNG",
    "type": "LIMIT",
    "quantity": 300,
    "action": "BUY",
    "price": 25.00,
    "quantity_filled": 0,
    "vwap": None,
    "status": "OPEN",
}


def _build_client(logger, session_mock: MagicMock) -> ApiClient:
    """Build an ``ApiClient`` with a mocked session."""
    client = ApiClient(
        base_url="http://localhost:9999/v1",
        api_key="TESTKEY",
        timeout_seconds=0.5,
        max_get_retries=2,
        retry_backoff_seconds=0.01,
        retry_jitter_seconds=0.0,  # deterministic in tests
        logger=logger,
    )
    client._session = session_mock
    return client


class TestApiClientGetCase:
    """Tests for ``get_case``."""

    def test_success(self, logger):
        session = MagicMock()
        session.get.return_value = _mock_response(200, SAMPLE_CASE)
        client = _build_client(logger, session)
        case = client.get_case()
        assert case.tick == 42
        assert case.status == CaseStatus.ACTIVE
        session.get.assert_called_once()

    def test_auth_failure_raises(self, logger):
        session = MagicMock()
        session.get.return_value = _mock_response(401)
        client = _build_client(logger, session)
        with pytest.raises(AuthenticationError):
            client.get_case()

    def test_404_raises(self, logger):
        session = MagicMock()
        session.get.return_value = _mock_response(404)
        client = _build_client(logger, session)
        with pytest.raises(EndpointNotFoundError):
            client.get_case()

    def test_connection_error_retries_then_fails(self, logger):
        session = MagicMock()
        session.get.side_effect = requests.ConnectionError("refused")
        client = _build_client(logger, session)
        with pytest.raises(ConnectionFailure):
            client.get_case()
        # initial + 2 retries = 3 calls
        assert session.get.call_count == 3

    def test_timeout_retries_then_fails(self, logger):
        session = MagicMock()
        session.get.side_effect = requests.Timeout("timed out")
        client = _build_client(logger, session)
        with pytest.raises(ConnectionFailure):
            client.get_case()
        assert session.get.call_count == 3

    def test_5xx_retries_then_raises(self, logger):
        session = MagicMock()
        session.get.return_value = _mock_response(503)
        client = _build_client(logger, session)
        with pytest.raises(ServerError) as exc_info:
            client.get_case()
        assert exc_info.value.status_code == 503
        # initial + 2 retries = 3 calls
        assert session.get.call_count == 3

    def test_429_retries_once_then_raises(self, logger):
        resp_429 = _mock_response(429, {"wait": 0.01}, {"Retry-After": "0.01"})
        session = MagicMock()
        session.get.return_value = resp_429
        client = _build_client(logger, session)
        with pytest.raises(RateLimitError):
            client.get_case()
        # First attempt → 429 → retry → 429 → raise
        assert session.get.call_count == 2

    def test_429_then_success(self, logger):
        resp_429 = _mock_response(429, {"wait": 0.01}, {"Retry-After": "0.01"})
        resp_ok = _mock_response(200, SAMPLE_CASE)
        session = MagicMock()
        session.get.side_effect = [resp_429, resp_ok]
        client = _build_client(logger, session)
        case = client.get_case()
        assert case.tick == 42
        assert session.get.call_count == 2

    def test_5xx_then_success(self, logger):
        resp_500 = _mock_response(500)
        resp_ok = _mock_response(200, SAMPLE_CASE)
        session = MagicMock()
        session.get.side_effect = [resp_500, resp_ok]
        client = _build_client(logger, session)
        case = client.get_case()
        assert case.tick == 42

    def test_unexpected_status_raises(self, logger):
        session = MagicMock()
        session.get.return_value = _mock_response(418, text="I'm a teapot")
        client = _build_client(logger, session)
        with pytest.raises(UnexpectedStatusError):
            client.get_case()


class TestApiClientGetSecurities:
    """Tests for ``get_securities``."""

    def test_list_all(self, logger):
        payload = [
            {"ticker": "SPNG", "position": 100, "vwap": 25.0, "nlv": 2500.0},
            {"ticker": "SMMR", "position": -50, "vwap": 24.8, "nlv": -1240.0},
        ]
        session = MagicMock()
        session.get.return_value = _mock_response(200, payload)
        client = _build_client(logger, session)
        secs = client.get_securities()
        assert len(secs) == 2
        assert secs[0].ticker == "SPNG"
        assert secs[1].position == -50

    def test_filter_by_ticker(self, logger):
        payload = [{"ticker": "ATMN", "position": 0, "vwap": 0.0, "nlv": 0.0}]
        session = MagicMock()
        session.get.return_value = _mock_response(200, payload)
        client = _build_client(logger, session)
        secs = client.get_securities(ticker="ATMN")
        assert len(secs) == 1


class TestApiClientGetBook:
    """Tests for ``get_book``."""

    def test_non_empty_book(self, logger):
        bid = {**SAMPLE_ORDER, "action": "BUY", "price": 24.99}
        ask = {**SAMPLE_ORDER, "order_id": 101, "action": "SELL", "price": 25.01}
        session = MagicMock()
        session.get.return_value = _mock_response(200, {"bid": [bid], "ask": [ask]})
        client = _build_client(logger, session)
        book = client.get_book("SPNG", limit=1)
        assert len(book.bid) == 1
        assert len(book.ask) == 1

    def test_empty_book(self, logger):
        session = MagicMock()
        session.get.return_value = _mock_response(200, {"bid": [], "ask": []})
        client = _build_client(logger, session)
        book = client.get_book("WNTR")
        assert book.bid == []


class TestApiClientGetTas:
    """Tests for ``get_tas``."""

    def test_incremental_tas(self, logger):
        payload = [
            {"id": 11, "period": 1, "tick": 50, "price": 25.0, "quantity": 100},
            {"id": 12, "period": 1, "tick": 51, "price": 25.01, "quantity": 50},
        ]
        session = MagicMock()
        session.get.return_value = _mock_response(200, payload)
        client = _build_client(logger, session)
        tas = client.get_tas("SPNG", after=10)
        assert len(tas) == 2
        assert tas[0].id == 11


class TestApiClientGetNews:
    """Tests for ``get_news``."""

    def test_incremental_news(self, logger):
        payload = [
            {
                "news_id": 5,
                "period": 1,
                "tick": 60,
                "ticker": "SPNG",
                "headline": "Good news",
                "body": "Details...",
            }
        ]
        session = MagicMock()
        session.get.return_value = _mock_response(200, payload)
        client = _build_client(logger, session)
        news = client.get_news(since=4)
        assert len(news) == 1
        assert news[0].ticker == "SPNG"


class TestApiClientOrders:
    """Tests for order submission, retrieval, and cancellation."""

    def test_submit_limit_order(self, logger):
        session = MagicMock()
        session.post.return_value = _mock_response(200, SAMPLE_ORDER)
        client = _build_client(logger, session)
        order = client.submit_order("SPNG", "LIMIT", 300, "BUY", price=25.00)
        assert order.order_id == 100
        assert order.type == OrderType.LIMIT

    def test_submit_order_429_records_rate_limit(self, logger):
        resp_429 = _mock_response(429, {"wait": 1.5}, {"Retry-After": "1.5"})
        session = MagicMock()
        session.post.return_value = resp_429
        client = _build_client(logger, session)
        with pytest.raises(RateLimitError) as exc_info:
            client.submit_order("SPNG", "LIMIT", 300, "BUY", price=25.00)
        assert exc_info.value.retry_after == 1.5
        # Rate limiter should proactively block subsequent requests
        assert client.rate_limiter.is_ready("SPNG") is False

    def test_get_orders(self, logger):
        session = MagicMock()
        session.get.return_value = _mock_response(200, [SAMPLE_ORDER])
        client = _build_client(logger, session)
        orders = client.get_orders(status="OPEN")
        assert len(orders) == 1

    def test_get_single_order(self, logger):
        session = MagicMock()
        session.get.return_value = _mock_response(200, SAMPLE_ORDER)
        client = _build_client(logger, session)
        order = client.get_order(100)
        assert order.order_id == 100

    def test_cancel_order(self, logger):
        session = MagicMock()
        session.delete.return_value = _mock_response(200, {"success": True})
        client = _build_client(logger, session)
        result = client.cancel_order(100)
        assert result["success"] is True


class TestApiClientBulkCancel:
    """Tests for ``bulk_cancel``."""

    def test_cancel_by_ticker(self, logger):
        session = MagicMock()
        session.post.return_value = _mock_response(200, {"cancelled_order_ids": [1, 2]})
        client = _build_client(logger, session)
        result = client.bulk_cancel(ticker="SPNG")
        assert len(result.cancelled_order_ids) == 2

    def test_cancel_all(self, logger):
        session = MagicMock()
        session.post.return_value = _mock_response(200, {"cancelled_order_ids": [1, 2, 3, 4]})
        client = _build_client(logger, session)
        result = client.bulk_cancel(all_orders=True)
        assert len(result.cancelled_order_ids) == 4


class TestApiClientGetLimits:
    """Tests for ``get_limits``."""

    def test_success(self, logger):
        payload = [
            {
                "name": "Aggregate",
                "gross": 3000,
                "net": 1000,
                "gross_limit": 15000,
                "net_limit": 10000,
                "gross_fine": 10.0,
                "net_fine": 5.0,
            }
        ]
        session = MagicMock()
        session.get.return_value = _mock_response(200, payload)
        client = _build_client(logger, session)
        limits = client.get_limits()
        assert len(limits) == 1
        assert limits[0].gross_limit == 15000


class TestApiClientClose:
    """Tests for session lifecycle."""

    def test_close_calls_session_close(self, logger):
        session = MagicMock()
        client = _build_client(logger, session)
        client.close()
        session.close.assert_called_once()


class TestApiClientProactiveRateLimit:
    """Verify proactive rate-limit gating on ``submit_order``."""

    def test_proactive_block_raises_without_http_call(self, logger):
        session = MagicMock()
        client = _build_client(logger, session)
        # Simulate a previous 429 that set the tracker
        client.rate_limiter.record_wait("SPNG", 60.0)
        with pytest.raises(RateLimitError):
            client.submit_order("SPNG", "LIMIT", 100, "BUY", price=25.0)
        # No HTTP call should have been made
        session.post.assert_not_called()
