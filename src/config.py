import os

import yaml

_config: dict | None = None


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base. Override values win."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def load_config(path: str = "config.yaml") -> dict:
    global _config
    if _config is None:
        with open(path, "r", encoding="utf-8") as f:
            _config = yaml.safe_load(f)
        # Apply local overrides if present (machine-specific dev paths, never committed)
        local_path = os.path.splitext(path)[0] + ".local.yaml"
        if os.path.exists(local_path):
            with open(local_path, "r", encoding="utf-8") as f:
                local = yaml.safe_load(f) or {}
            _config = _deep_merge(_config, local)
    return _config


def get_config() -> dict:
    if _config is None:
        return load_config()
    return _config
