"""SQLite state store for normalized items, summaries, and deliveries."""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import paths

SCHEMA_VERSION = 1

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
  item_key TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  author TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  source_url TEXT NOT NULL DEFAULT '',
  published_at INTEGER,
  collected_at INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  raw_path TEXT,
  markdown_path TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE(source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source, published_at DESC);

CREATE TABLE IF NOT EXISTS summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_key TEXT NOT NULL REFERENCES items(item_key) ON DELETE CASCADE,
  content_hash TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  model TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  response_path TEXT,
  input_chars INTEGER NOT NULL DEFAULT 0,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  usage_json TEXT,
  status TEXT NOT NULL,
  error TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  UNIQUE(item_key, content_hash, prompt_version, model)
);

CREATE INDEX IF NOT EXISTS idx_summaries_status ON summaries(status, created_at);

CREATE TABLE IF NOT EXISTS deliveries (
  item_key TEXT NOT NULL REFERENCES items(item_key) ON DELETE CASCADE,
  summary_id INTEGER NOT NULL REFERENCES summaries(id) ON DELETE CASCADE,
  target TEXT NOT NULL,
  remote_name TEXT,
  remote_uid TEXT,
  body_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  error TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  published_at INTEGER,
  PRIMARY KEY(item_key, summary_id, target)
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at INTEGER NOT NULL,
  ended_at INTEGER,
  source_status_json TEXT,
  ingested_count INTEGER NOT NULL DEFAULT 0,
  summarized_count INTEGER NOT NULL DEFAULT 0,
  published_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  error TEXT
);
"""


def now_ts() -> int:
    return int(time.time())


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or paths.db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA_SQL)
        self.conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            self.conn.execute("BEGIN")
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def begin_run(self) -> int:
        cursor = self.conn.execute(
            "INSERT INTO runs(started_at, status) VALUES (?, 'running')", (now_ts(),)
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        source_status: dict[str, Any],
        ingested: int,
        summarized: int,
        published: int,
        error: str = "",
    ) -> None:
        import json

        self.conn.execute(
            """
            UPDATE runs SET ended_at=?, source_status_json=?, ingested_count=?,
              summarized_count=?, published_count=?, status=?, error=? WHERE id=?
            """,
            (
                now_ts(),
                json.dumps(source_status, ensure_ascii=False),
                ingested,
                summarized,
                published,
                status,
                error or None,
                run_id,
            ),
        )
        self.conn.commit()

    def upsert_item(self, item: dict[str, Any]) -> tuple[bool, bool]:
        """Return (is_new, content_changed)."""
        key = str(item["item_key"])
        old = self.conn.execute("SELECT content_hash FROM items WHERE item_key=?", (key,)).fetchone()
        is_new = old is None
        changed = is_new or old["content_hash"] != item["content_hash"]
        now = now_ts()
        self.conn.execute(
            """
            INSERT INTO items(
              item_key, source, source_id, author, title, body, source_url,
              published_at, collected_at, content_hash, raw_path, markdown_path,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_key) DO UPDATE SET
              source=excluded.source,
              source_id=excluded.source_id,
              author=excluded.author,
              title=excluded.title,
              body=excluded.body,
              source_url=excluded.source_url,
              published_at=excluded.published_at,
              collected_at=excluded.collected_at,
              content_hash=excluded.content_hash,
              raw_path=excluded.raw_path,
              markdown_path=excluded.markdown_path,
              updated_at=excluded.updated_at
            """,
            (
                key,
                item.get("source", ""),
                item.get("source_id", ""),
                item.get("author", ""),
                item.get("title", ""),
                item.get("body", ""),
                item.get("source_url", ""),
                item.get("published_at"),
                item.get("collected_at") or now,
                item.get("content_hash", ""),
                item.get("raw_path"),
                item.get("markdown_path"),
                now,
                now,
            ),
        )
        self.conn.commit()
        return is_new, changed

    def list_items(self, limit: int | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM items ORDER BY COALESCE(published_at, collected_at), item_key"
        args: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            args = (limit,)
        return list(self.conn.execute(sql, args))

    def get_item(self, item_key: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM items WHERE item_key=?", (item_key,)).fetchone()

    def ensure_summary(
        self,
        item_key: str,
        content_hash: str,
        prompt_version: str,
        model: str,
        *,
        retry_failed: bool = False,
    ) -> int:
        row = self.conn.execute(
            """
            SELECT id, status FROM summaries
            WHERE item_key=? AND content_hash=? AND prompt_version=? AND model=?
            """,
            (item_key, content_hash, prompt_version, model),
        ).fetchone()
        if row:
            if retry_failed and row["status"] == "failed":
                self.conn.execute(
                    "UPDATE summaries SET status='pending', error=NULL, updated_at=? WHERE id=?",
                    (now_ts(), row["id"]),
                )
                self.conn.commit()
            return int(row["id"])
        cursor = self.conn.execute(
            """
            INSERT INTO summaries(item_key, content_hash, prompt_version, model, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', ?, ?)
            """,
            (item_key, content_hash, prompt_version, model, now_ts(), now_ts()),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def pending_summaries(self, limit: int | None = None) -> list[sqlite3.Row]:
        sql = """
          SELECT s.*, i.source, i.source_id, i.author, i.title, i.body, i.source_url,
                 i.published_at, i.collected_at, i.raw_path, i.markdown_path
          FROM summaries s JOIN items i ON i.item_key=s.item_key
          WHERE s.status='pending' AND s.content_hash=i.content_hash
          ORDER BY COALESCE(i.published_at, i.collected_at), s.id
        """
        args: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            args = (limit,)
        return list(self.conn.execute(sql, args))

    def ready_summaries(self, limit: int | None = None) -> list[sqlite3.Row]:
        sql = """
          SELECT s.*, i.source, i.source_id, i.author, i.title, i.body, i.source_url,
                 i.published_at, i.collected_at, i.raw_path, i.markdown_path
          FROM summaries s JOIN items i ON i.item_key=s.item_key
          LEFT JOIN deliveries d ON d.item_key=s.item_key AND d.summary_id=s.id AND d.target='memos'
          WHERE s.status='ok' AND s.content_hash=i.content_hash
            AND (d.status IS NULL OR d.status <> 'published')
          ORDER BY COALESCE(i.published_at, i.collected_at), s.id
        """
        args: tuple[Any, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            args = (limit,)
        return list(self.conn.execute(sql, args))

    def mark_summary_running(self, summary_id: int) -> None:
        self.conn.execute(
            "UPDATE summaries SET status='running', error=NULL, updated_at=? WHERE id=?",
            (now_ts(), summary_id),
        )
        self.conn.commit()

    def save_summary_result(
        self,
        summary_id: int,
        *,
        summary: str = "",
        response_path: str | None = None,
        input_chars: int = 0,
        duration_ms: int = 0,
        usage_json: str | None = None,
        status: str,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE summaries SET summary=?, response_path=?, input_chars=?, duration_ms=?,
              usage_json=?, status=?, error=?, updated_at=? WHERE id=?
            """,
            (
                summary,
                response_path,
                input_chars,
                duration_ms,
                usage_json,
                status,
                error,
                now_ts(),
                summary_id,
            ),
        )
        self.conn.commit()

    def get_delivery(self, item_key: str, summary_id: int, target: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM deliveries WHERE item_key=? AND summary_id=? AND target=?",
            (item_key, summary_id, target),
        ).fetchone()

    def ensure_delivery(self, item_key: str, summary_id: int, target: str, body_hash: str) -> sqlite3.Row:
        self.conn.execute(
            """
            INSERT INTO deliveries(item_key, summary_id, target, body_hash, status)
            VALUES (?, ?, ?, ?, 'pending')
            ON CONFLICT(item_key, summary_id, target) DO UPDATE SET body_hash=excluded.body_hash
            """,
            (item_key, summary_id, target, body_hash),
        )
        self.conn.commit()
        return self.get_delivery(item_key, summary_id, target)  # type: ignore[return-value]

    def mark_delivery_running(self, item_key: str, summary_id: int, target: str) -> None:
        self.conn.execute(
            "UPDATE deliveries SET status='running', attempts=attempts+1, error=NULL WHERE item_key=? AND summary_id=? AND target=?",
            (item_key, summary_id, target),
        )
        self.conn.commit()

    def save_delivery_result(
        self,
        item_key: str,
        summary_id: int,
        target: str,
        *,
        status: str,
        remote_name: str | None = None,
        remote_uid: str | None = None,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE deliveries SET status=?, remote_name=COALESCE(?, remote_name),
              remote_uid=COALESCE(?, remote_uid), error=?, published_at=?
            WHERE item_key=? AND summary_id=? AND target=?
            """,
            (
                status,
                remote_name,
                remote_uid,
                error,
                now_ts() if status == "published" else None,
                item_key,
                summary_id,
                target,
            ),
        )
        self.conn.commit()

    def stats(self) -> dict[str, int]:
        def count(sql: str) -> int:
            return int(self.conn.execute(sql).fetchone()[0])

        return {
            "items": count("SELECT count(*) FROM items"),
            "summaries_pending": count("SELECT count(*) FROM summaries WHERE status='pending'"),
            "summaries_ok": count("SELECT count(*) FROM summaries WHERE status='ok'"),
            "summaries_failed": count("SELECT count(*) FROM summaries WHERE status='failed'"),
            "memos_published": count("SELECT count(*) FROM deliveries WHERE status='published'"),
            "memos_failed": count("SELECT count(*) FROM deliveries WHERE status='failed'"),
        }
