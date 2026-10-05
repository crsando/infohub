"""Render normalized summaries into concise Memos Markdown."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from typing import Any


def render_memo(item: dict[str, Any], summary: str, tags: list[str]) -> str:
    title = str(item.get("title") or "未命名资讯").strip()
    source = "微信公众号" if item.get("source") == "wxmp" else "X/Twitter"
    published = format_time(item.get("published_at"))
    author = str(item.get("author") or "未知作者").strip()
    title_link = markdown_title(title, str(item.get("source_url") or "").strip())
    lines = [
        title_link,
        "",
        f"来源：{source} · {author} · {published or '时间未知'}",
        "",
        summary.strip(),
    ]
    rendered_tags = [normalize_tag(tag) for tag in tags if normalize_tag(tag)]
    source_tag = "wxmp" if item.get("source") == "wxmp" else "xnews"
    rendered_tags.append(source_tag)
    lines.extend(["", " ".join(f"#{tag}" for tag in dict.fromkeys(rendered_tags)), ""])
    return "\n".join(lines)


def markdown_title(title: str, url: str) -> str:
    """Render the compact linked title, falling back to plain text without a URL."""
    escaped_title = title.replace("\\", "\\\\").replace("]", "\\]")
    if not url:
        return escaped_title
    escaped_url = url.replace("\\", "\\\\").replace(")", "\\)")
    return f"[{escaped_title}]({escaped_url})"


def format_time(timestamp: Any) -> str:
    try:
        return dt.datetime.fromtimestamp(int(timestamp)).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def normalize_tag(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "", str(value).lstrip("#"))[:64]


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
