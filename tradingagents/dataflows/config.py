from contextvars import ContextVar
from copy import deepcopy

import tradingagents.default_config as default_config

# Each async task/run gets its own immutable-by-convention snapshot. This avoids
# concurrent graphs overwriting a process-global data-vendor configuration.
_config_var: ContextVar[dict | None] = ContextVar("tradingagents_dataflow_config", default=None)


def initialize_config():
    """Initialize the configuration with default values."""
    if _config_var.get() is None:
        _config_var.set(deepcopy(default_config.DEFAULT_CONFIG))


def set_config(config: dict):
    """Update the configuration with custom values.

    Dict-valued keys (e.g. ``data_vendors``) are merged one level deep so a
    partial update like ``{"data_vendors": {"core_stock_apis": "alpha_vantage"}}``
    keeps the other nested keys from the default; scalar keys are replaced.
    """
    initialize_config()
    incoming = deepcopy(config)
    # A complete application/run config is a replacement snapshot, not a
    # partial patch. Starting from defaults prevents stale tool-vendor keys from
    # an earlier run leaking into a later run in the same task/thread.
    is_full_snapshot = all(
        key in incoming for key in ("llm_provider", "data_vendors", "tool_vendors")
    )
    current = deepcopy(
        default_config.DEFAULT_CONFIG
        if is_full_snapshot
        else (_config_var.get() or default_config.DEFAULT_CONFIG)
    )
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(current.get(key), dict):
            current[key].update(value)
        else:
            current[key] = value
    _config_var.set(current)


def get_config() -> dict:
    """Get the current configuration."""
    if _config_var.get() is None:
        initialize_config()
    return deepcopy(_config_var.get())


# Initialize with default config
initialize_config()
