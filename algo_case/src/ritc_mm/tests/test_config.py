"""Tests for configuration loading and required key validation."""

from __future__ import annotations

from pathlib import Path

from *REMOVED*_mm.config import load_config, validate_required_keys


def test_default_config_loads_and_has_required_keys() -> None:
    """The default configuration should load with all required sections present."""
    config_path = Path(__file__).resolve().parents[3] / "config" / "default.yaml"
    config = load_config(config_path)

    required_keys = (
        "api",
        "api.base_url",
        "api.api_key",
        "api.timeout_seconds",
        "universe",
        "universe.tickers",
        "polling",
        "logging",
        "tunables",
        "tunables.quote_interval_ms",
        "tunables.soft_cap_tkr",
        "tunables.news_lockout_seconds",
        "tunables.minute_closeout_start_s",
    )

    validate_required_keys(config, required_keys)
