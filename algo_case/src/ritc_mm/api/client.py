"""RIT Client REST API client with robust error handling and retry logic.

This module provides ``ApiClient``, the single point of contact between the
bot and the RIT exchange.  All HTTP details, retry/backoff, rate-limit
tracking, and response parsing are encapsulated here so that callers work
exclusively with typed Pydantic models and never touch raw ``requests``
objects.

Design decisions
----------------
* **Session reuse** — ``requests.Session`` keeps connections alive and
  carries the ``X-API-Key`` header automatically.
* **GET retry with backoff + jitter** — idempotent reads are retried on
  connection/timeout errors and 5xx responses.  Non-idempotent POST/DELETE
  requests are *not* retried automatically.
* **429 handling** — the ``Retry-After`` header (or ``wait`` body field) is
  honoured.  The per-ticker ``RateLimitTracker`` lets the bot gate future
  requests proactively.
* **Structured logging** — every HTTP round-trip is logged with a unique
  ``request_id`` so failures are fully traceable in the JSON log.
"""

from __future__ import annotations

import random
import time
from typing import Any, Mapping

import requests

from *REMOVED*_mm.api.errors import (
    ApiError,
    AuthenticationError,
    ConnectionFailure,
    EndpointNotFoundError,
    RateLimitError,
    ServerError,
    UnexpectedStatusError,
)
from *REMOVED*_mm.api.models import (
    BookResponse,
    CancelResult,
    CaseResponse,
    LimitInfo,
    NewsItem,
    OhlcEntry,
    OrderResponse,
    SecurityResponse,
    TasEntry,
    TraderResponse,
)
from *REMOVED*_mm.api.ratelimit import RateLimitTracker
from *REMOVED*_mm.telemetry.logger import StructuredLoggerAdapter, bind_context, new_request_id


class ApiClient:
    """Thread-unsafe, synchronous client for the RIT Client REST API.

    Parameters
    ----------
    base_url:
        API root including version prefix, e.g. ``http://localhost:9999/v1``.
    api_key:
        Value for the ``X-API-Key`` header.
    timeout_seconds:
        Per-request timeout.
    max_get_retries:
        Maximum number of retries for idempotent GET requests on
        connection/timeout errors and 5xx responses.
    retry_backoff_seconds:
        Base sleep duration between retries.
    retry_jitter_seconds:
        Maximum random jitter added to retry sleeps to avoid thundering herd.
    logger:
        Structured logger adapter for diagnostics.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        max_get_retries: int,
        retry_backoff_seconds: float,
        retry_jitter_seconds: float,
        logger: StructuredLoggerAdapter,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._max_get_retries = max_get_retries
        self._retry_backoff = retry_backoff_seconds
        self._retry_jitter = retry_jitter_seconds
        self._logger = logger

        self._session = requests.Session()
        self._session.headers.update({"X-API-Key": api_key})

        self._rate_limiter = RateLimitTracker()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        """Build a full URL from a relative path (e.g. ``/case``)."""
        return f"{self._base_url}/{path.lstrip('/')}"

    @staticmethod
    def _extract_wait(response: requests.Response, default: float) -> float:
        """Parse the server's retry-wait hint from headers/body."""
        header = response.headers.get("Retry-After")
        if header:
            try:
                return max(float(header), 0.0)
            except ValueError:
                pass
        try:
            body = response.json()
        except ValueError:
            return default
        if isinstance(body, dict) and "wait" in body:
            try:
                return max(float(body["wait"]), 0.0)
            except (TypeError, ValueError):
                pass
        return default

    def _classify_response(self, response: requests.Response) -> None:
        """Raise the appropriate ``ApiError`` subclass for non-200 codes."""
        code = response.status_code
        if code == 200:
            return

        if code in (401, 403):
            raise AuthenticationError()

        if code == 404:
            raise EndpointNotFoundError()

        if code == 429:
            wait = self._extract_wait(response, self._retry_backoff)
            raise RateLimitError(retry_after=wait)

        if 500 <= code <= 599:
            raise ServerError(status_code=code)

        raise UnexpectedStatusError(status_code=code, body=response.text[:200])

    def _sleep_with_jitter(self, base: float | None = None) -> None:
        """Sleep for ``base + uniform(0, jitter)`` seconds."""
        wait = (base if base is not None else self._retry_backoff) + random.uniform(
            0.0, self._retry_jitter
        )
        time.sleep(wait)

    # ------------------------------------------------------------------
    # Core HTTP verbs (with retry / logging)
    # ------------------------------------------------------------------

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Perform a GET request with retry logic for transient failures.

        Retries on ``ConnectionError``, ``Timeout``, 5xx, and a single 429.

        Parameters
        ----------
        path:
            Relative endpoint path (e.g. ``case``).
        params:
            Optional query parameters.

        Returns
        -------
        Any
            Parsed JSON body on HTTP 200.

        Raises
        ------
        ApiError (or subclass)
            On non-recoverable failures.
        """
        url = self._url(path)
        attempt = 0
        retried_429 = False

        while True:
            rid = new_request_id()
            log = bind_context(self._logger, request_id=rid)

            log.debug(
                "GET %s attempt=%d",
                path,
                attempt,
                extra={"order_id": None},
            )

            try:
                resp = self._session.get(url, params=params, timeout=self._timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt < self._max_get_retries:
                    log.warning(
                        "Connection/timeout on GET %s; retrying (attempt %d/%d)",
                        path,
                        attempt + 1,
                        self._max_get_retries,
                        extra={"order_id": None},
                    )
                    self._sleep_with_jitter()
                    attempt += 1
                    continue
                log.error(
                    "Connection/timeout exhausted retries on GET %s. "
                    "If API runs in a Windows VM from macOS, verify VM networking.",
                    path,
                    extra={"order_id": None},
                    exc_info=True,
                )
                raise ConnectionFailure(f"GET {path} failed after {attempt + 1} attempts") from exc

            # 200 — success
            if resp.status_code == 200:
                log.debug("GET %s → 200", path, extra={"order_id": None})
                return resp.json()

            # 429 — rate limit; honour server wait then retry once
            if resp.status_code == 429:
                wait = self._extract_wait(resp, self._retry_backoff)
                if not retried_429:
                    log.warning(
                        "429 on GET %s; waiting %.3fs",
                        path,
                        wait,
                        extra={"order_id": None},
                    )
                    self._sleep_with_jitter(wait)
                    retried_429 = True
                    continue
                raise RateLimitError(retry_after=wait)

            # 5xx — transient server error; retry
            if 500 <= resp.status_code <= 599:
                if attempt < self._max_get_retries:
                    log.warning(
                        "Server error %d on GET %s; retrying",
                        resp.status_code,
                        path,
                        extra={"order_id": None},
                    )
                    self._sleep_with_jitter()
                    attempt += 1
                    continue
                raise ServerError(status_code=resp.status_code)

            # Non-recoverable
            self._classify_response(resp)

    def _post(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Perform a POST request **without** automatic retry.

        POST requests mutate state, so retrying is the caller's
        responsibility.

        Returns
        -------
        Any
            Parsed JSON body on HTTP 200.

        Raises
        ------
        ApiError (or subclass)
            On any non-200 response.
        """
        url = self._url(path)
        rid = new_request_id()
        log = bind_context(self._logger, request_id=rid)

        log.debug("POST %s", path, extra={"order_id": None})

        try:
            resp = self._session.post(url, params=params, timeout=self._timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            log.error(
                "Connection/timeout on POST %s",
                path,
                extra={"order_id": None},
                exc_info=True,
            )
            raise ConnectionFailure(f"POST {path} connection failure") from exc

        if resp.status_code == 200:
            log.debug("POST %s → 200", path, extra={"order_id": None})
            return resp.json()

        # Record rate-limit in the tracker for proactive gating
        if resp.status_code == 429:
            wait = self._extract_wait(resp, self._retry_backoff)
            ticker = (params or {}).get("ticker")
            if ticker:
                self._rate_limiter.record_wait(str(ticker), wait)
            else:
                self._rate_limiter.record_global_wait(wait)
            raise RateLimitError(retry_after=wait)

        self._classify_response(resp)

    def _delete(
        self,
        path: str,
    ) -> Any:
        """Perform a DELETE request without retry.

        Returns
        -------
        Any
            Parsed JSON body on HTTP 200.
        """
        url = self._url(path)
        rid = new_request_id()
        log = bind_context(self._logger, request_id=rid)

        log.debug("DELETE %s", path, extra={"order_id": None})

        try:
            resp = self._session.delete(url, timeout=self._timeout)
        except (requests.Timeout, requests.ConnectionError) as exc:
            log.error(
                "Connection/timeout on DELETE %s",
                path,
                extra={"order_id": None},
                exc_info=True,
            )
            raise ConnectionFailure(f"DELETE {path} connection failure") from exc

        if resp.status_code == 200:
            log.debug("DELETE %s → 200", path, extra={"order_id": None})
            return resp.json()

        self._classify_response(resp)

    # ------------------------------------------------------------------
    # Public API — Case / Trader
    # ------------------------------------------------------------------

    def get_case(self) -> CaseResponse:
        """``GET /case`` — current case information (tick, status, etc.)."""
        data = self._get("case")
        result = CaseResponse.model_validate(data)
        self._logger.info(
            "Case: period=%d tick=%d status=%s",
            result.period,
            result.tick,
            result.status.value,
            extra={"order_id": None},
        )
        return result

    def get_trader(self) -> TraderResponse:
        """``GET /trader`` — current trader information (NLV, etc.)."""
        data = self._get("trader")
        return TraderResponse.model_validate(data)

    # ------------------------------------------------------------------
    # Public API — Securities / Market Data
    # ------------------------------------------------------------------

    def get_securities(self, ticker: str | None = None) -> list[SecurityResponse]:
        """``GET /securities`` — positions and security metadata.

        Parameters
        ----------
        ticker:
            Optional ticker filter.
        """
        params: dict[str, Any] = {}
        if ticker:
            params["ticker"] = ticker
        data = self._get("securities", params=params or None)
        return [SecurityResponse.model_validate(s) for s in data]

    def get_book(self, ticker: str, limit: int = 20) -> BookResponse:
        """``GET /securities/book`` — order book snapshot.

        Parameters
        ----------
        ticker:
            Case-sensitive ticker symbol.
        limit:
            Maximum levels per side (default 20).
        """
        data = self._get("securities/book", params={"ticker": ticker, "limit": limit})
        return BookResponse.model_validate(data)

    def get_history(
        self,
        ticker: str,
        period: int | None = None,
        limit: int | None = None,
    ) -> list[OhlcEntry]:
        """``GET /securities/history`` — OHLC bars."""
        params: dict[str, Any] = {"ticker": ticker}
        if period is not None:
            params["period"] = period
        if limit is not None:
            params["limit"] = limit
        data = self._get("securities/history", params=params)
        return [OhlcEntry.model_validate(e) for e in data]

    def get_tas(self, ticker: str, after: int = 0) -> list[TasEntry]:
        """``GET /securities/tas`` — incremental time & sales.

        Parameters
        ----------
        ticker:
            Case-sensitive ticker symbol.
        after:
            Return only trades with ``id > after`` (for incremental polling).
        """
        params: dict[str, Any] = {"ticker": ticker}
        if after > 0:
            params["after"] = after
        data = self._get("securities/tas", params=params)
        return [TasEntry.model_validate(e) for e in data]

    # ------------------------------------------------------------------
    # Public API — News
    # ------------------------------------------------------------------

    def get_news(self, since: int = 0, limit: int | None = None) -> list[NewsItem]:
        """``GET /news`` — incremental news items.

        Parameters
        ----------
        since:
            Return only news with ``news_id > since``.
        limit:
            Maximum number of items (defaults to 20 on server).
        """
        params: dict[str, Any] = {}
        if since > 0:
            params["since"] = since
        if limit is not None:
            params["limit"] = limit
        data = self._get("news", params=params or None)
        return [NewsItem.model_validate(n) for n in data]

    # ------------------------------------------------------------------
    # Public API — Limits
    # ------------------------------------------------------------------

    def get_limits(self) -> list[LimitInfo]:
        """``GET /limits`` — current risk limits and fines."""
        data = self._get("limits")
        return [LimitInfo.model_validate(li) for li in data]

    # ------------------------------------------------------------------
    # Public API — Orders
    # ------------------------------------------------------------------

    def get_orders(self, status: str = "OPEN") -> list[OrderResponse]:
        """``GET /orders`` — open (or filtered) orders.

        Parameters
        ----------
        status:
            Order status filter (``OPEN``, ``TRANSACTED``, ``CANCELLED``).
        """
        data = self._get("orders", params={"status": status})
        return [OrderResponse.model_validate(o) for o in data]

    def get_order(self, order_id: int) -> OrderResponse:
        """``GET /orders/{id}`` — details of a single order."""
        data = self._get(f"orders/{order_id}")
        return OrderResponse.model_validate(data)

    def submit_order(
        self,
        ticker: str,
        order_type: str,
        quantity: float,
        action: str,
        price: float | None = None,
    ) -> OrderResponse:
        """``POST /orders`` — submit a new order.

        Parameters
        ----------
        ticker:
            Case-sensitive ticker.
        order_type:
            ``MARKET`` or ``LIMIT``.
        quantity:
            Number of shares.
        action:
            ``BUY`` or ``SELL``.
        price:
            Required for ``LIMIT`` orders.

        Returns
        -------
        OrderResponse
            The submitted (possibly partially filled) order.

        Raises
        ------
        RateLimitError
            If the per-ticker insertion rate limit is exceeded.
        """
        params: dict[str, Any] = {
            "ticker": ticker,
            "type": order_type,
            "quantity": quantity,
            "action": action,
        }
        if price is not None:
            params["price"] = price

        # Proactive rate-limit check
        if not self._rate_limiter.is_ready(ticker):
            wait = self._rate_limiter.seconds_until_ready(ticker)
            self._logger.warning(
                "Proactive rate-limit hold for %s (%.3fs remaining)",
                ticker,
                wait,
                extra={"order_id": None, "ticker": ticker},
            )
            raise RateLimitError(retry_after=wait, message=f"Proactive hold for {ticker}")

        data = self._post("orders", params=params)
        result = OrderResponse.model_validate(data)

        self._logger.info(
            "Order submitted: %s %s %s qty=%.0f px=%s → id=%d status=%s",
            ticker,
            action,
            order_type,
            quantity,
            price,
            result.order_id,
            result.status.value,
            extra={
                "order_id": result.order_id,
                "ticker": ticker,
            },
        )
        return result

    def cancel_order(self, order_id: int) -> dict[str, Any]:
        """``DELETE /orders/{id}`` — cancel a specific open order."""
        data = self._delete(f"orders/{order_id}")
        self._logger.info(
            "Cancelled order %d",
            order_id,
            extra={"order_id": order_id},
        )
        return data  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Public API — Bulk cancel
    # ------------------------------------------------------------------

    def bulk_cancel(
        self,
        *,
        ticker: str | None = None,
        all_orders: bool = False,
        ids: list[int] | None = None,
        query: str | None = None,
    ) -> CancelResult:
        """``POST /commands/cancel`` — bulk cancel open orders.

        Exactly one parameter should be specified (server processes the first
        available in the order: ``all``, ``ticker``, ``ids``, ``query``).
        """
        params: dict[str, Any] = {}
        if all_orders:
            params["all"] = 1
        elif ticker:
            params["ticker"] = ticker
        elif ids:
            params["ids"] = ",".join(str(i) for i in ids)
        elif query:
            params["query"] = query

        data = self._post("commands/cancel", params=params)
        result = CancelResult.model_validate(data)

        self._logger.info(
            "Bulk cancel → %d orders cancelled",
            len(result.cancelled_order_ids),
            extra={"order_id": None, "ticker": ticker},
        )
        return result

    # ------------------------------------------------------------------
    # Rate limiter access (for external inspection)
    # ------------------------------------------------------------------

    @property
    def rate_limiter(self) -> RateLimitTracker:
        """Expose rate-limit tracker for external callers."""
        return self._rate_limiter

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()
        self._logger.info("ApiClient session closed", extra={"order_id": None})
