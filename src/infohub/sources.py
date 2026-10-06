"""Source runners and read-only adapters for wxmp and xnews SQLite stores."""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from infohub_common.store import connect_readonly
from .config import SourceConfig
from .errors import SourceError


@dataclass
class SourceRunResult:
    name: str
    status: str
    returncode: int | None = None
    duration_ms: int = 0
    output: str = ""
    error: str = ""


def resolve_database(source: SourceConfig, repo_root: Path) -> Path:
    path = Path(source.database).expanduser()
    return path if path.is_absolute() else repo_root / path


def run_source(name: str, source: SourceConfig, repo_root: Path, timeout: int = 1800) -> SourceRunResult:
    if not source.run_command:
        return SourceRunResult(name, "skipped", error="未配置 run_command")
    started = time.monotonic()
    environment = os.environ.copy()
    config_env = {"wxmp": "WXMP_CONFIG", "xnews": "XNEWS_CONFIG"}.get(name)
    if config_env and source.config:
        config_path = Path(source.config).expanduser()
        if not config_path.is_absolute():
            config_path = repo_root / config_path
        environment[config_env] = str(config_path)
    try:
        completed = subprocess.run(
            source.run_command,
            cwd=str(repo_root),
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return SourceRunResult(
            name,
            "error",
            duration_ms=int((time.monotonic() - started) * 1000),
            error=f"无法执行 {source.run_command[0]}: {exc}",
        )
    except subprocess.TimeoutExpired:
        return SourceRunResult(
            name,
            "error",
            duration_ms=int((time.monotonic() - started) * 1000),
            error=f"采集超过 {timeout} 秒仍未结束",
        )
    output = (completed.stdout or "")[-4000:]
    stderr = (completed.stderr or "")[-4000:]
    return SourceRunResult(
        name,
        "ok" if completed.returncode == 0 else "error",
        returncode=completed.returncode,
        duration_ms=int((time.monotonic() - started) * 1000),
        output=output,
        error=stderr if completed.returncode else "",
    )


def read_source(name: str, source: SourceConfig, repo_root: Path) -> list[dict[str, Any]]:
    path = resolve_database(source, repo_root)
    if not path.exists():
        raise SourceError(f"{name} 数据库不存在: {path}", "先运行对应的采集器，或修改 sources.*.database")
    if name == "wxmp":
        return _read_wxmp(path)
    if name == "xnews":
        return _read_xnews(path)
    raise SourceError(f"不支持的数据源: {name}")


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{urllib.parse.quote(str(path))}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        return connection
    except sqlite3.Error as exc:
        raise SourceError(f"无法读取数据库 {path}: {exc}") from exc


def _read_wxmp(path: Path) -> list[dict[str, Any]]:
    connection = connect_readonly(path)
    try:
        rows = connection.execute(
            """
            SELECT a.url, a.account, a.title, a.digest, a.content,
                   a.published, a.collected, a.raw_path,
                   COALESCE(acc.nick, a.account) AS author
            FROM articles a LEFT JOIN accounts acc ON acc.username=a.account
            ORDER BY a.published ASC, a.url ASC
            """
        ).fetchall()
    except Exception as exc:
        raise SourceError(f"读取 wxmp.articles 失败: {exc}") from exc
    finally:
        connection.close()
    result: list[dict[str, Any]] = []
    for row in rows:
        url = canonical_wechat_url(str(row["url"] or ""))
        body = str(row["content"] or row["digest"] or "").strip()
        result.append(
            _item(
                source="wxmp",
                source_id=url,
                author=str(row["author"] or row["account"] or ""),
                title=str(row["title"] or "").strip(),
                body=body,
                source_url=url,
                published_at=_int_or_none(row["published"]),
                collected_at=_int_or_none(row["collected"]) or int(time.time()),
                raw_path=row["raw_path"],
            )
        )
    return result


def _read_xnews(path: Path) -> list[dict[str, Any]]:
    connection = connect_readonly(path)
    try:
        rows = connection.execute(
            """
            SELECT post_id, author_screen_name, author_name, text, created_at,
                   collected_at, url, raw_path, markdown_path
            FROM posts ORDER BY COALESCE(created_at, collected_at) ASC, post_id ASC
            """
        ).fetchall()
    except Exception as exc:
        raise SourceError(f"读取 xnews.posts 失败: {exc}") from exc
    finally:
        connection.close()
    result: list[dict[str, Any]] = []
    for row in rows:
        body = str(row["text"] or "").strip()
        title = first_line(body)
        screen_name = str(row["author_screen_name"] or "")
        author = str(row["author_name"] or screen_name or "")
        result.append(
            _item(
                source="xnews",
                source_id=str(row["post_id"]),
                author=author,
                title=title,
                body=body,
                source_url=str(row["url"] or ""),
                published_at=_int_or_none(row["created_at"]),
                collected_at=_int_or_none(row["collected_at"]) or int(time.time()),
                raw_path=row["raw_path"],
                markdown_path=row["markdown_path"],
            )
        )
    return result


def _item(
    *,
    source: str,
    source_id: str,
    author: str,
    title: str,
    body: str,
    source_url: str,
    published_at: int | None,
    collected_at: int,
    raw_path: Any = None,
    markdown_path: Any = None,
) -> dict[str, Any]:
    return {
        "item_key": f"{source}:{source_id}",
        "source": source,
        "source_id": source_id,
        "author": author,
        "title": title,
        "body": body,
        "source_url": source_url,
        "published_at": published_at,
        "collected_at": collected_at,
        "content_hash": hashlib.sha256((title + "\n" + body).encode("utf-8")).hexdigest(),
        "raw_path": str(raw_path) if raw_path else None,
        "markdown_path": str(markdown_path) if markdown_path else None,
    }


def canonical_wechat_url(url: str) -> str:
    try:
        parts = urllib.parse.urlsplit(url.strip())
        query = urllib.parse.parse_qs(parts.query, keep_blank_values=False)
        keys = ("__biz", "mid", "idx", "sn")
        selected = [(key, query[key][0]) for key in keys if query.get(key)]
        if selected:
            query_string = urllib.parse.urlencode(selected)
            return urllib.parse.urlunsplit(
                (parts.scheme or "https", parts.netloc, parts.path.rstrip("/") or "/s", query_string, "")
            )
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
    except ValueError:
        return url.strip()


def first_line(text: str, limit: int = 120) -> str:
    line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return line[:limit]


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
