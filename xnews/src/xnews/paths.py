"""XDG paths and managed directory permissions."""

from __future__ import annotations

import os
from pathlib import Path

from infohub_common.paths import (
    config_dir as _config_dir,
    data_dir as _data_dir,
    ensure_dirs as _ensure_dirs,
    fix_permissions as _fix_permissions,
    resolve_config_path as _resolve_config_path,
    safe_component as _safe_component,
)

APP_NAME = "xnews"
ENV_CONFIG = "XNEWS_CONFIG"
ENV_CONFIG_DIR = "XNEWS_CONFIG_DIR"
ENV_DATA_DIR = "XNEWS_DATA_DIR"


def config_dir() -> Path:
    return _config_dir(APP_NAME, ENV_CONFIG_DIR)


def default_config_path() -> Path:
    return config_dir() / "config.json"


def resolve_config_path() -> Path:
    return _resolve_config_path(APP_NAME, ENV_CONFIG)


def data_dir(configured: str | None = None) -> Path:
    override = os.environ.get(ENV_DATA_DIR)
    if override:
        return Path(override).expanduser()
    if configured:
        return Path(configured).expanduser()
    return _data_dir(APP_NAME, None)


def db_path(configured: str | None = None) -> Path:
    return data_dir(configured) / "xnews.db"


def raw_dir(configured: str | None = None) -> Path:
    return data_dir(configured) / "raw"


def response_raw_dir(configured: str | None = None) -> Path:
    return raw_dir(configured) / "responses"


def post_raw_dir(configured: str | None = None) -> Path:
    return raw_dir(configured) / "posts"


def log_dir(configured: str | None = None) -> Path:
    return data_dir(configured) / "logs"


def ensure_dirs(configured: str | None = None) -> None:
    root = data_dir(configured)
    _ensure_dirs(root, response_raw_dir(configured), post_raw_dir(configured), log_dir(configured), mode=0o700)


def safe_component(value: str, fallback: str = "unknown") -> str:
    return _safe_component(value, fallback, max_len=120)


def fix_permissions(configured: str | None = None) -> None:
    root = data_dir(configured)
    _fix_permissions(root, dir_mode=0o700, file_mode=0o600)

