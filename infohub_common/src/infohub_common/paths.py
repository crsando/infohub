"""XDG-compliant path resolution utilities.

Provides a consistent way to resolve config, data, and cache directories
following XDG Base Directory Specification for all infohub projects.

Deliberately does not handle Windows paths; designed for Linux/macOS/WSL.
On Windows, access via WSL at /mnt/c/... paths.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


def config_dir(app_name: str, env_override: str | None = None) -> Path:
    """Resolve XDG_CONFIG_HOME/app_name directory."""
    if env_override:
        override = os.environ.get(env_override, "").strip()
        if override:
            return Path(override).expanduser()

    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / app_name


def data_dir(app_name: str, env_override: str | None = None) -> Path:
    """Resolve XDG_DATA_HOME/app_name directory."""
    if env_override:
        override = os.environ.get(env_override, "").strip()
        if override:
            return Path(override).expanduser()

    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / app_name


def cache_dir(app_name: str, env_override: str | None = None) -> Path:
    """Resolve XDG_CACHE_HOME/app_name directory."""
    if env_override:
        override = os.environ.get(env_override, "").strip()
        if override:
            return Path(override).expanduser()

    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / app_name


def resolve_config_path(app_name: str, env_config: str) -> Path:
    """Resolve config.json path from environment or default location.

    Priority:
    1. ${env_config} environment variable (exact path)
    2. XDG_CONFIG_HOME/app_name/config.json
    3. ~/.config/app_name/config.json
    """
    override = os.environ.get(env_config, "").strip()
    if override:
        return Path(override).expanduser()
    return config_dir(app_name) / "config.json"


def ensure_dirs(*paths: Path, mode: int = 0o700) -> None:
    """Create directories if they don't exist and set permissions."""
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)
        try:
            path.chmod(mode)
        except OSError:
            pass


def safe_component(value: str, fallback: str = "item", max_len: int = 160) -> str:
    """Sanitize a string for use as a filesystem component."""
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value))
    return clean.strip("._")[:max_len] or fallback


def write_json_atomic(path: Path, value: Any) -> None:
    """Write JSON atomically with a temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(path)


def fix_permissions(root: Path, dir_mode: int = 0o700, file_mode: int = 0o600) -> None:
    """Recursively fix directory and file permissions."""
    if not root.exists():
        return

    for path in root.rglob("*"):
        try:
            if path.is_dir():
                path.chmod(dir_mode)
            elif path.is_file():
                path.chmod(file_mode)
        except OSError:
            pass

    try:
        root.chmod(dir_mode)
    except OSError:
        pass
