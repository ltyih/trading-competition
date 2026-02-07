"""Live entrypoint stub for the RITC market making bot.

This script currently performs startup initialization and a single `/case`
health check against the RIT Client REST API.
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

from ritc_mm.config import ConfigError, load_config, validate_required_keys
from ritc_mm.telemetry.logger import LoggerConfig, bind_context, get_logger, new_request_id


REQUIRED_CONFIG_KEYS: tuple[str, ...] = (
    "api",
    "api.base_url",
    "api.api_key",
    "api.timeout_seconds",
    "api.max_get_retries",
    "api.retry_backoff_seconds",
    "api.retry_jitter_seconds",
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
    """Extract retry wait duration from HTTP headers/body.

    Parameters
    ----------
    response:
        HTTP response object returned by `requests`.
    default_wait:
        Fallback wait duration if response does not include wait hints.

    Returns
    -------
    float
        Number of seconds to wait before the next retry attempt.
    """
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
    """Perform a robust GET `/case` health check.

    Parameters
    ----------
    base_url:
        API base URL including `/v1`.
    api_key:
        API key used in the `X-API-Key` header.
    timeout_seconds:
        Per-request timeout.
    max_get_retries:
        Maximum retries for idempotent GET failures (connection/timeout/5xx).
    retry_backoff_seconds:
        Base retry sleep.
    retry_jitter_seconds:
        Max random jitter added to retry sleeps.
    logger:
        Logger adapter for structured logs.

    Returns
    -------
    dict[str, Any]
        Parsed JSON body for successful `/case` requests.

    Raises
    ------
    HealthCheckFailure
        If health check fails with mapped exit code.
    """
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


def main() -> int:
    """Run startup initialization and one `/case` health check request."""
    parser = argparse.ArgumentParser(description="Run RITC live bot startup stub.")
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

    logging_cfg = config["logging"]
    logger = get_logger(
        name="ritc_mm.run_live",
        config=LoggerConfig(
            level=str(logging_cfg["level"]),
            console_enabled=bool(logging_cfg["console_enabled"]),
            file_enabled=bool(logging_cfg["file_enabled"]),
            file_path=str(logging_cfg["file_path"]),
        ),
        correlation_id=new_request_id(),
    )

    logger.info("Starting RITC market making live runner stub")

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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
