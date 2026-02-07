"""Structured logging utilities for the RITC market making bot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4


REQUIRED_STRUCTURED_FIELDS: tuple[str, ...] = (
    "timestamp",
    "level",
    "message",
    "logger",
    "module",
    "ticker",
    "regime",
    "order_id",
    "request_id",
    "correlation_id",
)


@dataclass(frozen=True)
class LoggerConfig:
    """Settings for configuring the structured application logger."""

    level: str = "INFO"
    console_enabled: bool = True
    file_enabled: bool = False
    file_path: str = "logs/ritc_mm.log"


class JsonFormatter(logging.Formatter):
    """Format log records as JSON lines with a stable field schema."""

    def format(self, record: logging.LogRecord) -> str:
        """Render a log record into a JSON string.

        Parameters
        ----------
        record:
            Standard Python log record.

        Returns
        -------
        str
            JSON serialized log line.
        """
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "module": record.module,
            "ticker": getattr(record, "ticker", None),
            "regime": getattr(record, "regime", None),
            "order_id": getattr(record, "order_id", None),
            "request_id": getattr(record, "request_id", None),
            "correlation_id": getattr(record, "correlation_id", None),
        }

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(payload, separators=(",", ":"), ensure_ascii=True)


class StructuredLoggerAdapter(logging.LoggerAdapter[logging.Logger]):
    """Logger adapter that preserves bound context and per-call context."""

    def process(self, msg: str, kwargs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Merge adapter context with ad-hoc `extra` fields for this log call."""
        merged_extra = dict(self.extra)
        call_extra = kwargs.get("extra", {})
        if isinstance(call_extra, dict):
            merged_extra.update(call_extra)
        kwargs["extra"] = merged_extra
        return msg, kwargs


def _configure_handler(handler: logging.Handler) -> logging.Handler:
    """Apply the shared formatter to a logging handler."""
    handler.setFormatter(JsonFormatter())
    return handler


def build_logger(name: str, config: LoggerConfig) -> logging.Logger:
    """Build and configure a base logger.

    Parameters
    ----------
    name:
        Logger name.
    config:
        Logger configuration.

    Returns
    -------
    logging.Logger
        Configured logger instance.
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, config.level.upper(), logging.INFO))
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    if config.console_enabled:
        logger.addHandler(_configure_handler(logging.StreamHandler()))

    if config.file_enabled:
        log_path = Path(config.file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.addHandler(_configure_handler(logging.FileHandler(log_path, encoding="utf-8")))

    return logger


def bind_context(
    logger: logging.Logger | logging.LoggerAdapter[logging.Logger],
    **context: Any,
) -> StructuredLoggerAdapter:
    """Attach persistent contextual fields to a logger adapter.

    Parameters
    ----------
    logger:
        Base logger or existing logger adapter.
    context:
        Context fields, for example ``ticker`` and ``correlation_id``.

    Returns
    -------
    logging.LoggerAdapter
        Logger adapter carrying merged contextual metadata.
    """
    if isinstance(logger, logging.LoggerAdapter):
        merged_context = dict(logger.extra)
        merged_context.update(context)
        return StructuredLoggerAdapter(logger.logger, merged_context)

    return StructuredLoggerAdapter(logger, context)


def get_logger(
    name: str,
    config: LoggerConfig | None = None,
    **context: Any,
) -> StructuredLoggerAdapter:
    """Create a configured logger adapter with optional default context.

    Parameters
    ----------
    name:
        Logger name.
    config:
        Optional logger configuration. If omitted, defaults are used.
    context:
        Initial context fields.

    Returns
    -------
    logging.LoggerAdapter
        Structured logger adapter.
    """
    resolved_config = config or LoggerConfig()
    base_logger = build_logger(name=name, config=resolved_config)
    return bind_context(base_logger, **context)


def new_request_id() -> str:
    """Generate a new correlation/request identifier for tracing operations."""
    return uuid4().hex
