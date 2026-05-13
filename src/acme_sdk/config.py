"""Configuration management for the Acme SDK."""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_ENV_PREFIX = "ACME_"

# Mapping of config keys to environment variable names
_ENV_MAPPING = {
    "api_key": "ACME_API_KEY",
    "endpoint": "ACME_ENDPOINT",
    "timeout": "ACME_TIMEOUT",
    "compression": "ACME_COMPRESSION",
    "batch_size": "ACME_BATCH_SIZE",
    "max_retries": "ACME_MAX_RETRIES",
    "log_level": "ACME_LOG_LEVEL",
}


@dataclass
class AcmeConfig:
    """Configuration for the Acme SDK.

    Configuration can be loaded from environment variables, a config file,
    or set directly via constructor arguments. The precedence order is:
    constructor args > environment variables > config file > defaults.
    """

    api_key: Optional[str] = None
    endpoint: str = "https://ingest.acme-sdk.dev"
    timeout: float = 30.0
    compression: bool = True
    batch_size: int = 512
    max_retries: int = 3
    log_level: str = "WARNING"
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "AcmeConfig":
        """Create a configuration from environment variables.

        Reads all ACME_* environment variables and maps them to
        configuration fields. Boolean values accept 'true', '1', 'yes'
        as truthy and 'false', '0', 'no' as falsy.

        Returns:
            AcmeConfig instance populated from environment.
        """
        kwargs: dict[str, Any] = {}

        for config_key, env_var in _ENV_MAPPING.items():
            value = os.environ.get(env_var)
            if value is None:
                continue

            if config_key in ("timeout",):
                kwargs[config_key] = float(value)
            elif config_key in ("batch_size", "max_retries"):
                kwargs[config_key] = int(value)
            elif config_key in ("compression",):
                kwargs[config_key] = _parse_bool(value)
            else:
                kwargs[config_key] = value

        logger.debug("Loaded config from environment: %s", list(kwargs.keys()))
        return cls(**kwargs)

    @classmethod
    def from_file(cls, path: str | Path) -> "AcmeConfig":
        """Load configuration from a TOML or YAML file.

        The file format is detected from the extension. Supports
        environment variable interpolation using ${VAR_NAME} syntax.

        Args:
            path: Path to the configuration file.

        Returns:
            AcmeConfig instance populated from the file.

        Raises:
            FileNotFoundError: If the config file does not exist.
            ValueError: If the file format is not supported.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        suffix = path.suffix.lower()
        if suffix in (".toml",):
            data = _load_toml(path)
        elif suffix in (".yaml", ".yml"):
            data = _load_yaml(path)
        else:
            raise ValueError(f"Unsupported config file format: {suffix}")

        # Interpolate environment variables
        data = _interpolate_env_vars(data)

        # Extract known keys
        kwargs: dict[str, Any] = {}
        extra: dict[str, Any] = {}

        known_keys = {f.name for f in cls.__dataclass_fields__.values() if f.name != "extra"}
        for key, value in data.items():
            if key in known_keys:
                kwargs[key] = value
            else:
                extra[key] = value

        if extra:
            kwargs["extra"] = extra

        logger.debug("Loaded config from file: %s", path)
        return cls(**kwargs)

    def merge(self, overrides: dict[str, Any]) -> "AcmeConfig":
        """Create a new config with the given overrides applied.

        Args:
            overrides: Dictionary of configuration values to override.

        Returns:
            New AcmeConfig instance with overrides applied.
        """
        current = {
            k: v for k, v in self.__dict__.items() if k != "extra"
        }
        current.update(overrides)
        return AcmeConfig(**current)


def _parse_bool(value: str) -> bool:
    """Parse a string into a boolean value."""
    if value.lower() in ("true", "1", "yes"):
        return True
    elif value.lower() in ("false", "0", "no"):
        return False
    raise ValueError(f"Cannot parse {value!r} as boolean")


def _load_toml(path: Path) -> dict[str, Any]:
    """Load a TOML file and return the acme section."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    with open(path, "rb") as f:
        data = tomllib.load(f)

    # Look for [acme] section or use top-level
    return data.get("acme", data)


def _load_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file and return the acme section."""
    try:
        import yaml
    except ImportError as exc:
        raise ImportError(
            "PyYAML is required for YAML config files. "
            "Install it with: pip install pyyaml"
        ) from exc

    with open(path) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Expected a mapping in {path}, got {type(data).__name__}")

    return data.get("acme", data)


def _interpolate_env_vars(data: dict[str, Any]) -> dict[str, Any]:
    """Replace ${VAR_NAME} patterns with environment variable values.

    Supports default values with ${VAR_NAME:-default} syntax.
    """
    result: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str):
            result[key] = _expand_env_string(value)
        elif isinstance(value, dict):
            result[key] = _interpolate_env_vars(value)
        else:
            result[key] = value
    return result


def _expand_env_string(value: str) -> str:
    """Expand environment variable references in a string."""
    import re

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        if ":-" in var_name:
            name, default = var_name.split(":-", 1)
            return os.environ.get(name, default)
        return os.environ.get(var_name, match.group(0))

    return re.sub(r"\$\{([^}]+)\}", _replace, value)
