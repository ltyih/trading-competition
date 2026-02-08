"""Configuration loading and validation utilities for the RITC MM bot."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml


class ConfigError(ValueError):
    """Raised when configuration files are missing or malformed."""


def load_config(path: str | Path) -> dict[str, Any]:
    """Load a YAML configuration file.

    Parameters
    ----------
    path:
        Relative or absolute path to the YAML file.

    Returns
    -------
    dict[str, Any]
        Parsed configuration dictionary.

    Raises
    ------
    ConfigError
        If the file cannot be read or does not produce a mapping.
    """
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Configuration file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)

    if not isinstance(loaded, dict):
        raise ConfigError(f"Configuration root must be a mapping: {config_path}")

    return loaded


def validate_required_keys(config: Mapping[str, Any], required: Sequence[str]) -> None:
    """Validate dotted required keys within a configuration mapping.

    Parameters
    ----------
    config:
        Parsed configuration mapping.
    required:
        Required key paths in dotted form (for example, ``api.base_url``).

    Raises
    ------
    ConfigError
        If any required key path is missing.
    """
    missing: list[str] = []
    for dotted_key in required:
        current: Any = config
        for key_part in dotted_key.split("."):
            if isinstance(current, Mapping) and key_part in current:
                current = current[key_part]
            else:
                missing.append(dotted_key)
                break

    if missing:
        missing_csv = ", ".join(sorted(set(missing)))
        raise ConfigError(f"Missing required config keys: {missing_csv}")
