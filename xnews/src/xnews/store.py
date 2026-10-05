"""SQLite storage, FTS5 search, and atomic raw writes."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from . import paths
from .errors import UpstreamError
from .parser import NormalizedPost

SCHEMA_VERSION = 1

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watch_accounts (
  key TEXT PRIMARY KEY,
  nick TEXT NOT NULL,
  screen_name TEXT NOT NULL,
  rest_id TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  added_at TEXT NOT NULL,
  last_run_at INTEGER,
  last_error TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_watch_rest_id
  ON watch_accounts(rest_id) WHERE rest_id IS NOT NULL AND rest_id <> '';

CREATE TABLE IF NOT EXISTS posts (
  post_id TEXT PRIMARY KEY,
  author_rest_id TEXT,
  author_screen_name TEXT,
  author_name TEXT,
  text TEXT NOT NULL,
  created_at INTEGER,
  collected_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  url TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  is_pinned INTEGER NOT NULL DEFAULT 0,
  is_reply INTEGER NOT NULL DEFAULT 0,
  is_repost INTEGER NOT NULL DEFAULT 0,
  entities_json TEXT NOT NULL DEFAULT '{}',
  raw_path TEXT,
  markdown_path TEXT,
  markdown_hash TEXT,
  parser_version TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_author ON posts(author_screen_name, created_at DESC);

CREATE TABLE IF NOT EXISTS post_accounts (
  post_id TEXT NOT NULL REFERENCES posts(post_id) ON DELETE CASCADE,
  watch_key TEXT NOT NULL REFERENCES watch_accounts(key) ON DELETE CASCADE,
  first_seen_at INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL,
  PRIMARY KEY (post_id, watch_key)
);

CREATE TABLE IF NOT EXISTS raw_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id TEXT REFERENCES posts(post_id) ON DELETE SET NULL,
  watch_key TEXT,
  run_id INTEGER,
  path TEXT NOT NULL UNIQUE,
  collected_at INTEGER NOT NULL,
  content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at INTEGER NOT NULL,
  ended_at INTEGER,
  status TEXT NOT NULL,
  message TEXT
);

CREATE TABLE IF NOT EXISTS run_accounts (
  run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  watch_key TEXT NOT NULL,
  status TEXT NOT NULL,
  pages INTEGER NOT NULL DEFAULT 0,
  fetched INTEGER NOT NULL DEFAULT 0,
  new_count INTEGER NOT NULL DEFAULT 0,
  updated_count INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  PRIMARY KEY (run_id, watch_key)
);

CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
  author_screen_name,
  author_name,
  text,
  content='posts',
  content_rowid='rowid',
  tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS posts_ai AFTER INSERT ON posts BEGIN
  INSERT INTO posts_fts(rowid, author_screen_name, author_name, text)
  VALUES (new.rowid, new.author_screen_name, new.author_name, new.text);
END;

CREATE TRIGGER IF NOT EXISTS posts_ad AFTER DELETE ON posts BEGIN
  INSERT INTO posts_fts(posts_fts, rowid, author_screen_name, author_name, text)
  VALUES ('delete', old.rowid, old.author_screen_name, old.author_name, old.text);
END;

CREATE TRIGGER IF NOT EXISTS posts_au AFTER UPDATE ON posts BEGIN
  INSERT INTO posts_fts(posts_fts, rowid, author_screen_name, author_name, text)
  VALUES ('delete', old.rowid, old.author_screen_name, old.author_name, old.text);
  INSERT INTO posts_fts(rowid, author_screen_name, author_name, text)
  VALUES (new.rowid, new.author_screen_name, new.author_name, new.text);
END;
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
        self._init_schema()
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        self.conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        try:
            self.conn.execute("SELECT count(*) FROM posts_fts LIMIT 1")
        except sqlite3.OperationalError as exc:
            raise UpstreamError("当前 Python 的 SQLite 不支持 FTS5") from exc
        self.conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        try:
            self.conn.execute("BEGIN")
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ---------- accounts ----------

    def upsert_account(
        self,
        key: str,
        nick: str,
        screen_name: str,
        rest_id: str = "",
        enabled: bool = True,
        added_at: str = "",
        last_run_at: int | None = None,
        last_error: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO watch_accounts
              (key, nick, screen_name, rest_id, enabled, added_at, last_run_at, last_error)
            VALUES (?, ?, ?, NULLIF(?, ''), ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              nick=excluded.nick,
              screen_name=excluded.screen_name,
              rest_id=COALESCE(excluded.rest_id, watch_accounts.rest_id),
              enabled=excluded.enabled,
              last_run_at=COALESCE(excluded.last_run_at, watch_accounts.last_run_at),
              last_error=excluded.last_error
            """,
            (key, nick or screen_name, screen_name, rest_id, int(enabled), added_at, last_run_at, last_error),
        )
        self.conn.commit()

    def list_accounts(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM watch_accounts ORDER BY key"))

    def set_account_status(self, key: str, *, last_run_at: int | None = None, last_error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE watch_accounts SET last_run_at=COALESCE(?, last_run_at), last_error=? WHERE key=?",
            (last_run_at, last_error, key),
        )
        self.conn.commit()

    def remove_account(self, key: str, *, purge: bool = False) -> list[str]:
        paths_to_delete: list[str] = []
        with self.transaction():
            post_ids = [
                row["post_id"]
                for row in self.conn.execute(
                    "SELECT post_id FROM post_accounts WHERE watch_key=?", (key,)
                )
            ]
            self.conn.execute("DELETE FROM post_accounts WHERE watch_key=?", (key,))
            if purge and post_ids:
                for row in self.conn.execute(
                    """
                    SELECT p.post_id, p.raw_path, p.markdown_path
                    FROM posts p
                    WHERE p.post_id IN ({}) AND NOT EXISTS (
                      SELECT 1 FROM post_accounts pa WHERE pa.post_id=p.post_id
                    )
                    """.format(",".join("?" for _ in post_ids)),
                    post_ids,
                ):
                    paths_to_delete.extend(x for x in (row["raw_path"], row["markdown_path"]) if x)
                    self.conn.execute("DELETE FROM posts WHERE post_id=?", (row["post_id"],))
            self.conn.execute("DELETE FROM watch_accounts WHERE key=?", (key,))
        return paths_to_delete

    # ---------- runs ----------

    def begin_run(self) -> int:
        cursor = self.conn.execute(
            "INSERT INTO runs(started_at, status) VALUES (?, 'running')", (now_ts(),)
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, message: str = "") -> None:
        self.conn.execute(
            "UPDATE runs SET ended_at=?, status=?, message=? WHERE id=?",
            (now_ts(), status, message, run_id),
        )
        self.conn.commit()

    def begin_run_account(self, run_id: int, watch_key: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO run_accounts(run_id, watch_key, status) VALUES (?, ?, 'running')",
            (run_id, watch_key),
        )
        self.conn.commit()

    def finish_run_account(
        self,
        run_id: int,
        watch_key: str,
        *,
        status: str,
        pages: int = 0,
        fetched: int = 0,
        new_count: int = 0,
        updated_count: int = 0,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE run_accounts
            SET status=?, pages=?, fetched=?, new_count=?, updated_count=?, error=?
            WHERE run_id=? AND watch_key=?
            """,
            (status, pages, fetched, new_count, updated_count, error, run_id, watch_key),
        )
        self.conn.commit()

    # ---------- posts ----------

    def get_post(self, post_id: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM posts WHERE post_id=?", (post_id,)).fetchone()

    def known_post_ids(self, post_ids: list[str]) -> set[str]:
        if not post_ids:
            return set()
        placeholders = ",".join("?" for _ in post_ids)
        return {
            row["post_id"]
            for row in self.conn.execute(
                f"SELECT post_id FROM posts WHERE post_id IN ({placeholders})", post_ids
            )
        }

    def save_post(
        self,
        post: NormalizedPost,
        *,
        watch_key: str,
        collected_at: int,
        raw_path: str | None,
    ) -> str:
        existing = self.get_post(post.post_id)
        status = "new" if existing is None else "known"
        if existing is None:
            self.conn.execute(
                """
                INSERT INTO posts(
                  post_id, author_rest_id, author_screen_name, author_name, text,
                  created_at, collected_at, updated_at, url, content_hash,
                  is_pinned, is_reply, is_repost, entities_json, raw_path, parser_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    post.post_id,
                    post.author_rest_id,
                    post.author_screen_name,
                    post.author_name,
                    post.text,
                    post.created_at,
                    collected_at,
                    collected_at,
                    post.url,
                    post.content_hash,
                    int(post.is_pinned),
                    int(post.is_reply),
                    int(post.is_repost),
                    json.dumps(post.entities, ensure_ascii=False, sort_keys=True),
                    raw_path,
                    "0.1",
                ),
            )
        elif existing["content_hash"] != post.content_hash:
            status = "updated"
            self.conn.execute(
                """
                UPDATE posts SET
                  author_rest_id=?, author_screen_name=?, author_name=?, text=?,
                  created_at=?, collected_at=?, updated_at=?, url=?, content_hash=?,
                  is_pinned=?, is_reply=?, is_repost=?, entities_json=?,
                  raw_path=COALESCE(?, raw_path), parser_version=?
                WHERE post_id=?
                """,
                (
                    post.author_rest_id,
                    post.author_screen_name,
                    post.author_name,
                    post.text,
                    post.created_at,
                    existing["collected_at"],
                    collected_at,
                    post.url,
                    post.content_hash,
                    int(post.is_pinned),
                    int(post.is_reply),
                    int(post.is_repost),
                    json.dumps(post.entities, ensure_ascii=False, sort_keys=True),
                    raw_path,
                    "0.1",
                    post.post_id,
                ),
            )
        self.conn.execute(
            """
            INSERT INTO post_accounts(post_id, watch_key, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(post_id, watch_key) DO UPDATE SET last_seen_at=excluded.last_seen_at
            """,
            (post.post_id, watch_key, collected_at, collected_at),
        )
        return status

    def add_raw_snapshot(
        self,
        *,
        post_id: str | None,
        watch_key: str,
        run_id: int,
        path: str,
        collected_at: int,
        content_hash: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO raw_snapshots
              (post_id, watch_key, run_id, path, collected_at, content_hash)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (post_id, watch_key, run_id, path, collected_at, content_hash),
        )

    def posts_for_export(self, post_ids: list[str] | None = None) -> list[sqlite3.Row]:
        if post_ids:
            placeholders = ",".join("?" for _ in post_ids)
            return list(
                self.conn.execute(
                    f"SELECT * FROM posts WHERE post_id IN ({placeholders}) ORDER BY created_at DESC",
                    post_ids,
                )
            )
        return list(self.conn.execute("SELECT * FROM posts ORDER BY created_at DESC"))

    def source_names(self, post_id: str) -> list[str]:
        return [
            row["nick"] or row["screen_name"] or row["key"]
            for row in self.conn.execute(
                """
                SELECT a.nick, a.screen_name, a.key
                FROM post_accounts pa JOIN watch_accounts a ON a.key=pa.watch_key
                WHERE pa.post_id=? ORDER BY a.key
                """,
                (post_id,),
            )
        ]

    def source_keys(self, post_id: str) -> list[str]:
        return [
            row["watch_key"]
            for row in self.conn.execute(
                "SELECT watch_key FROM post_accounts WHERE post_id=? ORDER BY watch_key",
                (post_id,),
            )
        ]

    def mark_markdown(self, post_id: str, path: str, content_hash: str) -> None:
        self.conn.execute(
            "UPDATE posts SET markdown_path=?, markdown_hash=? WHERE post_id=?",
            (path, content_hash, post_id),
        )
        self.conn.commit()

    # ---------- query ----------

    def recent(self, account: str | None = None, limit: int = 20) -> list[sqlite3.Row]:
        if account:
            return list(
                self.conn.execute(
                    """
                    SELECT DISTINCT p.* FROM posts p JOIN post_accounts pa ON pa.post_id=p.post_id
                    WHERE pa.watch_key=? ORDER BY COALESCE(p.created_at, 0) DESC LIMIT ?
                    """,
                    (account, limit),
                )
            )
        return list(self.conn.execute(
            "SELECT * FROM posts ORDER BY COALESCE(created_at, 0) DESC LIMIT ?", (limit,)
        ))

    def search(self, keyword: str, *, account: str | None = None, limit: int = 20) -> list[sqlite3.Row]:
        keyword = keyword.strip()
        if not keyword:
            return []
        params: list[Any] = []
        relation = ""
        if account:
            relation = "JOIN post_accounts pa ON pa.post_id=p.post_id"
            params.append(account)
        if len(keyword) < 3:
            where = "(p.text LIKE ? OR p.author_name LIKE ? OR p.author_screen_name LIKE ?)"
            params.extend([f"%{keyword}%"] * 3)
            query = f"SELECT DISTINCT p.* FROM posts p {relation} WHERE {('pa.watch_key=? AND ' if account else '')}{where} ORDER BY COALESCE(p.created_at,0) DESC LIMIT ?"
            if account:
                params = [account, *params[1:]]
            params.append(limit)
            return list(self.conn.execute(query, params))
        match = keyword.replace('"', '""')
        params_fts: list[Any] = [f'"{match}"']
        if account:
            params_fts.append(account)
        params_fts.append(limit)
        query = f"""
          SELECT DISTINCT p.* FROM posts_fts f JOIN posts p ON p.rowid=f.rowid
          {relation}
          WHERE f.posts_fts MATCH ? {('AND pa.watch_key=?' if account else '')}
          ORDER BY COALESCE(p.created_at,0) DESC LIMIT ?
        """
        try:
            return list(self.conn.execute(query, params_fts))
        except sqlite3.OperationalError:
            params = []
            if account:
                params.extend([account])
            params.extend([f"%{keyword}%", f"%{keyword}%", f"%{keyword}%", limit])
            query = f"SELECT DISTINCT p.* FROM posts p {relation} WHERE {('pa.watch_key=? AND ' if account else '')}(p.text LIKE ? OR p.author_name LIKE ? OR p.author_screen_name LIKE ?) LIMIT ?"
            return list(self.conn.execute(query, params))

    def stats(self) -> dict[str, Any]:
        accounts = self.conn.execute("SELECT count(*) FROM watch_accounts").fetchone()[0]
        enabled = self.conn.execute("SELECT count(*) FROM watch_accounts WHERE enabled=1").fetchone()[0]
        posts = self.conn.execute("SELECT count(*) FROM posts").fetchone()[0]
        snapshots = self.conn.execute("SELECT count(*) FROM raw_snapshots").fetchone()[0]
        exported = self.conn.execute("SELECT count(*) FROM posts WHERE markdown_path IS NOT NULL").fetchone()[0]
        try:
            self.conn.execute("SELECT 1 FROM posts_fts LIMIT 1")
            fts = True
        except sqlite3.OperationalError:
            fts = False
        return {
            "accounts": accounts,
            "accounts_enabled": enabled,
            "posts": posts,
            "snapshots": snapshots,
            "exported": exported,
            "fts_available": fts,
            "db_bytes": self.path.stat().st_size if self.path.exists() else 0,
        }


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
