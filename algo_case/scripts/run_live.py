"""Live entrypoint for ingestion-only runtime checks.

This script initializes configuration/logging, runs an initial `/case` health check,
then enters a polling loop that ingests market data into ``GlobalState``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
import random
from pathlib import Path
import sys
import time
from typing import Any

import requests

# Support direct execution from repo root without requiring editable install first.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from *REMOVED*_mm.api.client import ApiClient
from *REMOVED*_mm.api.models import CaseStatus
from *REMOVED*_mm.config import ConfigError, load_config, validate_required_keys
from *REMOVED*_mm.data.state import GlobalState
from *REMOVED*_mm.strategy.engine import StrategyEngine
from *REMOVED*_mm.telemetry.logger import LoggerConfig, bind_context, get_logger, new_request_id


REQUIRED_CONFIG_KEYS: tuple[str, ...] = (
    "api",
    "api.base_url",
    "api.api_key",
    "api.timeout_seconds",
    "api.max_get_retries",
    "api.retry_backoff_seconds",
    "api.retry_jitter_seconds",
    "universe",
    "universe.tickers",
    "polling",
    "polling.case_interval_ms",
    "polling.book_interval_ms",
    "polling.tas_interval_ms",
    "polling.news_interval_ms",
    "polling.loop_sleep_ms",
    "polling.book_depth",
    "polling.tape_maxlen_per_ticker",
    "polling.news_max_items",
    "runtime",
    "runtime.run_mode",
    "tunables",
    "tunables.tick_size",
    "tunables.rounding_decimals",
    "tunables.base_hs_ticks",
    "tunables.min_hs_ticks",
    "tunables.base_size",
    "tunables.max_quote_size",
    "tunables.soft_cap_tkr",
    "tunables.hard_cap_tkr",
    "tunables.inv_k",
    "tunables.news_lockout_seconds",
    "tunables.minute_closeout_start_s",
    "tunables.heat_closeout_start_s",
    "tunables.fv_ema_alpha",
    "tunables.news_impulse_bps",
    "tunables.news_positive_keywords",
    "tunables.news_negative_keywords",
    "logging",
    "logging.level",
    "logging.console_enabled",
    "logging.file_enabled",
    "logging.file_path",
)


@dataclass(frozen=True)
class HealthCheckFailure(Exception):
    """Signal a health check failure with a deterministic process exit code."""

    exit_code: int
    reason: str


def _extract_wait_seconds(response: requests.Response, default_wait: float) -> float:
    """Extract retry wait duration from HTTP headers/body."""
    header_wait = response.headers.get("Retry-After")
    if header_wait:
        try:
            return max(float(header_wait), 0.0)
        except ValueError:
            pass

    try:
        body = response.json()
    except ValueError:
        return default_wait

    if isinstance(body, dict) and "wait" in body:
        try:
            return max(float(body["wait"]), 0.0)
        except (TypeError, ValueError):
            return default_wait

    return default_wait


def _health_check_case(
    base_url: str,
    api_key: str,
    timeout_seconds: float,
    max_get_retries: int,
    retry_backoff_seconds: float,
    retry_jitter_seconds: float,
    logger: logging.Logger | logging.LoggerAdapter[logging.Logger],
) -> dict[str, Any]:
    """Perform a robust GET `/case` health check."""
    url = f"{base_url.rstrip('/')}/case"
    headers = {"X-API-Key": api_key}

    retry_attempt = 0
    retried_429 = False

    while True:
        request_id = new_request_id()
        req_logger = bind_context(logger, request_id=request_id)

        try:
            response = requests.get(url=url, headers=headers, timeout=timeout_seconds)
        except (requests.Timeout, requests.ConnectionError) as exc:
            if retry_attempt < max_get_retries:
                sleep_seconds = retry_backoff_seconds + random.uniform(0.0, retry_jitter_seconds)
                req_logger.warning(
                    "Connection or timeout error during /case health check; retrying",
                    extra={"order_id": None},
                )
                time.sleep(sleep_seconds)
                retry_attempt += 1
                continue

            req_logger.error(
                "Unable to connect to RIT API. If API runs in a Windows VM from macOS, verify VM networking, host/IP, and port forwarding.",
                extra={"order_id": None},
                exc_info=True,
            )
            raise HealthCheckFailure(exit_code=6, reason="connection_or_timeout") from exc

        status_code = response.status_code

        if status_code == 200:
            try:
                payload = response.json()
            except ValueError as exc:
                req_logger.error("Invalid JSON payload from /case health check", exc_info=True)
                raise HealthCheckFailure(exit_code=5, reason="invalid_case_payload") from exc
            if not isinstance(payload, dict):
                raise HealthCheckFailure(exit_code=5, reason="invalid_case_payload")
            return payload

        if status_code in (401, 403):
            req_logger.error("Authentication failed for /case health check")
            raise HealthCheckFailure(exit_code=2, reason="auth_failed")

        if status_code == 404:
            req_logger.error("/case endpoint not found; verify base_url and API path")
            raise HealthCheckFailure(exit_code=3, reason="endpoint_not_found")

        if status_code == 429:
            if retried_429:
                req_logger.error("Rate limited on /case after retry")
                raise HealthCheckFailure(exit_code=4, reason="rate_limited")

            wait_seconds = _extract_wait_seconds(response=response, default_wait=retry_backoff_seconds)
            sleep_seconds = wait_seconds + random.uniform(0.0, retry_jitter_seconds)
            req_logger.warning(
                "Rate limit received for /case health check; honoring server backoff",
                extra={"order_id": None},
            )
            time.sleep(sleep_seconds)
            retried_429 = True
            continue

        if 500 <= status_code <= 599:
            if retry_attempt < max_get_retries:
                sleep_seconds = retry_backoff_seconds + random.uniform(0.0, retry_jitter_seconds)
                req_logger.warning(
                    "Server error during /case health check; retrying",
                    extra={"order_id": None},
                )
                time.sleep(sleep_seconds)
                retry_attempt += 1
                continue

            req_logger.error("Server error persisted for /case health check")
            raise HealthCheckFailure(exit_code=5, reason="server_error")

        req_logger.error("Unexpected status code from /case", extra={"order_id": None})
        raise HealthCheckFailure(exit_code=5, reason=f"unexpected_status_{status_code}")


def _summarize_top_of_book(state: GlobalState) -> dict[str, dict[str, float | None]]:
    """Build a compact per-ticker top-of-book summary."""
    summary: dict[str, dict[str, float | None]] = {}
    for ticker in state.universe:
        l1 = state.l1.get(ticker)
        if l1 is None:
            summary[ticker] = {
                "bid": None,
                "ask": None,
                "mid": None,
                "spread": None,
            }
            continue
        summary[ticker] = {
            "bid": l1.bid_px,
            "ask": l1.ask_px,
            "mid": l1.mid,
            "spread": l1.spread,
        }
    return summary


def main() -> int:
    """Run startup initialization and ingestion polling loop."""
    parser = argparse.ArgumentParser(description="Run *REMOVED* ingestion loop.")
    parser.add_argument(
        "--config",
        default="config/default.yaml",
        help="Path to YAML configuration file.",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
        validate_required_keys(config, REQUIRED_CONFIG_KEYS)
    except ConfigError as exc:
        print(f"Configuration error: {exc}")
        return 1

    run_mode = str(config["runtime"]["run_mode"]).strip().lower()
    if run_mode not in {"ingest", "strategy_dry_run"}:
        print(f"Configuration error: unsupported runtime.run_mode={run_mode!r}")
        return 1

    logging_cfg = config["logging"]
    logger = get_logger(
        name="*REMOVED*_mm.run_live",
        config=LoggerConfig(
            level=str(logging_cfg["level"]),
            console_enabled=bool(logging_cfg["console_enabled"]),
            file_enabled=bool(logging_cfg["file_enabled"]),
            file_path=str(logging_cfg["file_path"]),
        ),
        correlation_id=new_request_id(),
    )

    logger.info(
        "Starting *REMOVED* ingestion runner",
        extra={
            "request_id": new_request_id(),
            "ticker": None,
            "regime": None,
            "order_id": None,
            "run_mode": run_mode,
        },
    )

    api_cfg = config["api"]
    try:
        case_info = _health_check_case(
            base_url=str(api_cfg["base_url"]),
            api_key=str(api_cfg["api_key"]),
            timeout_seconds=float(api_cfg["timeout_seconds"]),
            max_get_retries=int(api_cfg["max_get_retries"]),
            retry_backoff_seconds=float(api_cfg["retry_backoff_seconds"]),
            retry_jitter_seconds=float(api_cfg["retry_jitter_seconds"]),
            logger=logger,
        )
    except HealthCheckFailure as exc:
        logger.error(f"Health check failed: {exc.reason}")
        return exc.exit_code

    logger.info(
        "Health check succeeded",
        extra={
            "request_id": new_request_id(),
            "ticker": None,
            "regime": None,
            "order_id": None,
            "case_name": case_info.get("name"),
            "case_status": case_info.get("status"),
            "period": case_info.get("period"),
            "tick": case_info.get("tick"),
        },
    )

    polling_cfg = config["polling"]
    universe = list(config["universe"]["tickers"])

    state = GlobalState(
        universe=universe,
        book_depth=int(polling_cfg["book_depth"]),
        tape_maxlen=int(polling_cfg["tape_maxlen_per_ticker"]),
        news_max_items=int(polling_cfg["news_max_items"]),
        logger=logger,
    )
    strategy_engine = StrategyEngine(config["tunables"]) if run_mode == "strategy_dry_run" else None

    client = ApiClient(
        base_url=str(api_cfg["base_url"]),
        api_key=str(api_cfg["api_key"]),
        timeout_seconds=float(api_cfg["timeout_seconds"]),
        max_get_retries=int(api_cfg["max_get_retries"]),
        retry_backoff_seconds=float(api_cfg["retry_backoff_seconds"]),
        retry_jitter_seconds=float(api_cfg["retry_jitter_seconds"]),
        logger=logger,
    )

    intervals_ms = {
        "case": int(polling_cfg["case_interval_ms"]),
        "book": int(polling_cfg["book_interval_ms"]),
        "tas": int(polling_cfg["tas_interval_ms"]),
        "news": int(polling_cfg["news_interval_ms"]),
    }
    loop_sleep_seconds = max(1, int(polling_cfg["loop_sleep_ms"])) / 1000.0
    next_due = {name: time.monotonic() for name in intervals_ms}

    logger.info(
        "Entering ingestion loop",
        extra={
            "request_id": new_request_id(),
            "ticker": None,
            "regime": None,
            "order_id": None,
            "intervals_ms": json.dumps(intervals_ms, separators=(",", ":")),
            "run_mode": run_mode,
        },
    )

    try:
        while True:
            now_mono = time.monotonic()
            due = [name for name, due_ts in next_due.items() if now_mono >= due_ts]

            if due:
                counts = state.update(client)

                for name in due:
                    step_seconds = intervals_ms[name] / 1000.0
                    while next_due[name] <= now_mono:
                        next_due[name] += step_seconds

                top_of_book = _summarize_top_of_book(state)
                case_status = state.case.status.value if state.case else "UNKNOWN"
                period = state.case.period if state.case else -1
                tick = state.case.tick if state.case else -1

                logger.info(
                    "Ingestion update %s",
                    json.dumps(
                        {
                            "due": due,
                            "period": period,
                            "tick": tick,
                            "case_status": case_status,
                            "counts": counts,
                            "tas_after": state.tas_after,
                            "news_since": state.news_since,
                            "top_of_book": top_of_book,
                        },
                        separators=(",", ":"),
                        ensure_ascii=True,
                    ),
                    extra={
                        "request_id": new_request_id(),
                        "ticker": None,
                        "regime": None,
                        "order_id": None,
                    },
                )

                if strategy_engine is not None:
                    targets = strategy_engine.step(state)
                    for ticker, target in targets.items():
                        security = state.positions_by_ticker.get(ticker)
                        position = float(security.position) if security is not None else 0.0
                        l1 = state.l1.get(ticker)
                        mid = l1.mid if l1 is not None else None

                        payload = {
                            "ticker": ticker,
                            "period": period,
                            "tick": tick,
                            "regime": target.regime.name,
                            "reason": target.reason,
                            "fair_value": target.fair_value,
                            "cancel_all": target.cancel_all,
                            "position": position,
                            "mid": mid,
                            "bid": (
                                {"price": target.bid.price, "quantity": target.bid.quantity}
                                if target.bid is not None
                                else None
                            ),
                            "ask": (
                                {"price": target.ask.price, "quantity": target.ask.quantity}
                                if target.ask is not None
                                else None
                            ),
                        }

                        logger.info(
                            "Strategy target %s",
                            json.dumps(payload, separators=(",", ":"), ensure_ascii=True),
                            extra={
                                "request_id": new_request_id(),
                                "ticker": ticker,
                                "regime": target.regime.name,
                                "order_id": None,
                            },
                        )

                if state.case is not None and state.case.status != CaseStatus.ACTIVE:
                    logger.info(
                        "Case status is not ACTIVE; exiting",
                        extra={
                            "request_id": new_request_id(),
                            "ticker": None,
                            "regime": None,
                            "order_id": None,
                            "case_status": state.case.status.value,
                        },
                    )
                    break

            time.sleep(loop_sleep_seconds)

    except KeyboardInterrupt:
        logger.info(
            "Interrupted by user; exiting",
            extra={"request_id": new_request_id(), "ticker": None, "regime": None, "order_id": None},
        )
    finally:
        client.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
