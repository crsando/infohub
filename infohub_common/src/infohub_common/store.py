"""JSON serialization and SQLite helpers."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, data: Any, mode: int = 0o600) -> None:
    """Write JSON atomically with a temporary file and replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.stem}-",
        suffix=".tmp"
    )
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
        try:
            Path(temp_path).chmod(mode)
        except OSError:
            pass
        Path(temp_path).replace(path)
    except BaseException:
        try:
            Path(temp_path).unlink()
        except OSError:
            pass
        raise


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite database in read-only mode with Row factory."""
    uri = f"file:{urllib.parse.quote(str(db_path))}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection, schema_sql: str) -> None:
    """Initialize database schema and enable WAL mode."""
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(schema_sql)
    conn.commit()
