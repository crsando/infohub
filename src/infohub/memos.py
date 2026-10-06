"""Memos API adapter with rendering utilities."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from typing import Any

import requests

from .config import MemosConfig
from .errors import MemosError


class MemosClient:
    def __init__(self, config: MemosConfig, token: str, session: requests.Session | None = None) -> None:
        if not token:
            raise MemosError("未配置 MEMOS_TOKEN", "设置 MEMOS_TOKEN 后再发布到 Memos")
        self.config = config
        self.token = token
        self.session = session or requests.Session()
        self.base_url = config.effective_base_url()
        self.endpoint = config.endpoint if config.endpoint.startswith("/") else f"/{config.endpoint}"

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

    def check(self) -> dict[str, Any]:
        try:
            response = self.session.get(
                f"{self.base_url}{self.endpoint}",
                params={"pageSize": 1},
                headers=self._headers(),
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {"data": payload}
        except requests.RequestException as exc:
            raise MemosError(f"Memos 连通性检查失败: {exc}") from exc
        except ValueError as exc:
            raise MemosError("Memos 返回不是合法 JSON") from exc

    def create(self, content: str) -> dict[str, Any]:
        try:
            response = self.session.post(
                f"{self.base_url}{self.endpoint}",
                json={"content": content, "visibility": self.config.visibility},
                headers=self._headers(),
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise MemosError("Memos 创建响应不是对象")
            return payload
        except MemosError:
            raise
        except requests.RequestException as exc:
            raise MemosError(f"Memos 创建 memo 失败: {exc}") from exc
        except ValueError as exc:
            raise MemosError("Memos 创建响应不是合法 JSON") from exc


def remote_ids(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Accept the name/uid shapes returned by different Memos versions."""
    nested = payload.get("memo") if isinstance(payload.get("memo"), dict) else payload
    return (
        str(nested.get("name")) if nested.get("name") is not None else None,
        str(nested.get("uid")) if nested.get("uid") is not None else None,
    )


def render_memo(item: dict[str, Any], summary: str, tags: list[str]) -> str:
    """Render a memo in Markdown format for Memos."""
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
    """Format Unix timestamp as local time string."""
    try:
        return dt.datetime.fromtimestamp(int(timestamp)).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def normalize_tag(value: str) -> str:
    """Sanitize tag value for Memos."""
    return re.sub(r"[^A-Za-z0-9_-]+", "", str(value).lstrip("#"))[:64]


def body_hash(body: str) -> str:
    """Compute SHA-256 hash of memo body for idempotency checks."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()

