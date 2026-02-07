"""Tests for structured logger initialization and output fields."""

from __future__ import annotations

import json
from pathlib import Path

from ritc_mm.telemetry.logger import LoggerConfig, REQUIRED_STRUCTURED_FIELDS, build_logger, get_logger


def test_logger_initializes_console_and_file_handlers(tmp_path: Path) -> None:
    """Logger factory should configure both console and file handlers when enabled."""
    log_path = tmp_path / "ritc_mm.log"
    logger = build_logger(
        name="ritc_mm.test.handlers",
        config=LoggerConfig(
            level="INFO",
            console_enabled=True,
            file_enabled=True,
            file_path=str(log_path),
        ),
    )

    assert len(logger.handlers) == 2


def test_logger_emits_expected_structured_fields(tmp_path: Path) -> None:
    """Structured log records should include required keys and bound context values."""
    log_path = tmp_path / "ritc_mm_structured.log"
    logger = get_logger(
        name="ritc_mm.test.records",
        config=LoggerConfig(
            level="INFO",
            console_enabled=False,
            file_enabled=True,
            file_path=str(log_path),
        ),
        correlation_id="corr-123",
    )

    logger.info(
        "test-message",
        extra={
            "ticker": "SPNG",
            "regime": "NORMAL_MM",
            "order_id": "order-1",
            "request_id": "req-1",
        },
    )

    for handler in logger.logger.handlers:
        handler.flush()

    log_line = log_path.read_text(encoding="utf-8").strip()
    payload = json.loads(log_line)

    for required_field in REQUIRED_STRUCTURED_FIELDS:
        assert required_field in payload

    assert payload["message"] == "test-message"
    assert payload["ticker"] == "SPNG"
    assert payload["regime"] == "NORMAL_MM"
    assert payload["order_id"] == "order-1"
    assert payload["request_id"] == "req-1"
    assert payload["correlation_id"] == "corr-123"
