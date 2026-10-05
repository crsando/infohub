"""XDG paths and managed directory permissions."""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "xnews"
ENV_CONFIG = "XNEWS_CONFIG"
ENV_CONFIG_DIR = "XNEWS_CONFIG_DIR"
ENV_DATA_DIR = "XNEWS_DATA_DIR"


def config_dir() -> Path:
    override = os.environ.get(ENV_CONFIG_DIR)
    if override:
        return Path(override).expanduser()
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")).expanduser()
    return base / APP_NAME


def default_config_path() -> Path:
    return config_dir() / "config.json"


def resolve_config_path() -> Path:
    override = os.environ.get(ENV_CONFIG)
    return Path(override).expanduser() if override else default_config_path()


def data_dir(configured: str | None = None) -> Path:
    override = os.environ.get(ENV_DATA_DIR)
    if override:
        return Path(override).expanduser()
    if configured:
        return Path(configured).expanduser()
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")).expanduser()
    return base / APP_NAME


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


def _mkdir_mode(path: Path, mode: int) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(mode)
    except OSError:
        pass
    return path


def ensure_dirs(configured: str | None = None) -> None:
    root = _mkdir_mode(data_dir(configured), 0o700)
    for child in (root / "raw", response_raw_dir(configured), post_raw_dir(configured), log_dir(configured)):
        _mkdir_mode(child, 0o700)


def safe_component(value: str, fallback: str = "unknown") -> str:
    value = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value))
    return value.strip("._")[:120] or fallback


def fix_permissions(configured: str | None = None) -> None:
    root = data_dir(configured)
    if root.exists():
        for path in (root, raw_dir(configured), response_raw_dir(configured), post_raw_dir(configured), log_dir(configured)):
            if path.exists():
                try:
                    path.chmod(0o700)
                except OSError:
                    pass
        for path in root.rglob("*"):
            if path.is_file() and path.suffix in {".json", ".db", ".md"}:
                try:
                    path.chmod(0o600)
                except OSError:
                    pass
