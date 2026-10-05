"""Runtime path resolution and permission helpers."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

APP_NAME = "infohub"
ENV_CONFIG = "INFOHUB_CONFIG"
ENV_DATA_DIR = "INFOHUB_DATA_DIR"


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base).expanduser() if base else Path.home() / ".config") / APP_NAME


def resolve_config_path() -> Path:
    value = os.environ.get(ENV_CONFIG, "").strip()
    return Path(value).expanduser() if value else config_dir() / "config.json"


def data_dir(configured: str | None = None) -> Path:
    value = os.environ.get(ENV_DATA_DIR, "").strip() or (configured or "").strip()
    return Path(value).expanduser() if value else Path.home() / ".local" / "share" / APP_NAME


def db_path(configured: str | None = None) -> Path:
    return data_dir(configured) / "timeline.db"


def llm_raw_dir(configured: str | None = None) -> Path:
    return data_dir(configured) / "raw" / "llm"


def log_dir(configured: str | None = None) -> Path:
    return data_dir(configured) / "logs"


def ensure_dirs(configured: str | None = None) -> Path:
    root = data_dir(configured)
    for path in (root, root / "raw", llm_raw_dir(configured), log_dir(configured)):
        path.mkdir(parents=True, exist_ok=True)
        try:
            path.chmod(0o700)
        except OSError:
            pass
    return root


def safe_component(value: str, fallback: str = "item") -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value))
    return clean.strip("._")[:160] or fallback


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    temporary.replace(path)


def fix_permissions(configured: str | None = None) -> None:
    root = data_dir(configured)
    if not root.exists():
        return
    for path in (root, root / "raw", llm_raw_dir(configured), log_dir(configured)):
        if path.exists():
            try:
                path.chmod(0o700)
            except OSError:
                pass
    for path in root.rglob("*"):
        if path.is_file():
            try:
                path.chmod(0o600)
            except OSError:
                pass
