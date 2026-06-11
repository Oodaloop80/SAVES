import os
import yaml

_config: dict | None = None


def load_config(path: str = "config.yaml") -> dict:
    global _config
    if _config is None:
        with open(path, "r", encoding="utf-8") as f:
            _config = yaml.safe_load(f)
    return _config


def get_config() -> dict:
    if _config is None:
        return load_config()
    return _config
