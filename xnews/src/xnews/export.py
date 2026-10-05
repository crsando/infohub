"""Markdown rendering and atomic export."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

from .config import Config
from .errors import ConfigError
from .store import Store, json_hash

_BAD_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(value: str, max_len: int = 140) -> str:
    value = _BAD_FILENAME.sub("_", value or "").strip().strip(".")
    value = re.sub(r"\s+", " ", value)
    if len(value) > max_len:
        value = value[:max_len].rstrip()
    return value or "untitled"


def fmt_time(timestamp: int | None) -> str:
    if not timestamp:
        return ""
    return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat(timespec="seconds")


def render_markdown(row: Any, source_names: list[str], cfg: Config) -> str:
    entities = _load_entities(row["entities_json"])
    lines = [
        "---",
        "平台: X",
        f"作者: {_yaml_string(row['author_name'] or row['author_screen_name'])} ({_yaml_string('@' + (row['author_screen_name'] or 'unknown'))})",
        f"作者ID: {_yaml_string(row['author_rest_id'] or '')}",
        f"帖子ID: {_yaml_string(row['post_id'])}",
        f"发布时间: {_yaml_string(fmt_time(row['created_at']))}",
        f"采集时间: {_yaml_string(fmt_time(row['collected_at']))}",
        f"置顶: {'true' if row['is_pinned'] else 'false'}",
        f"回复: {'true' if row['is_reply'] else 'false'}",
        f"转发: {'true' if row['is_repost'] else 'false'}",
        f"原文: {_yaml_string(row['url'])}",
        f"监听账号: {json.dumps(source_names, ensure_ascii=False)}",
        "---",
        "",
        (row["text"] or "").strip(),
    ]
    links = _entity_links(entities, cfg.export.include_media_links)
    if links:
        lines.extend(["", "## 链接与媒体", ""])
        lines.extend(f"- {link}" for link in links)
    lines.append("")
    return "\n".join(lines)


def export_posts(
    store: Store,
    cfg: Config,
    *,
    post_ids: list[str] | None = None,
    force: bool = False,
) -> list[Path]:
    if not cfg.export.dir:
        raise ConfigError("export.dir 未配置", "在 config.json 指定 Markdown 输出目录")
    base = Path(cfg.export.dir).expanduser()
    rows = store.posts_for_export(post_ids)
    written: list[Path] = []
    for row in rows:
        body = render_markdown(row, store.source_names(row["post_id"]), cfg)
        digest = json_hash(body)
        current = row["markdown_path"]
        if not force and current and row["markdown_hash"] == digest and Path(current).exists():
            continue
        screen_name = row["author_screen_name"] or row["author_rest_id"] or "unknown"
        date = dt.datetime.fromtimestamp(
            row["created_at"] or row["collected_at"], dt.timezone.utc
        ).strftime("%Y-%m-%d")
        try:
            filename = cfg.export.filename.format(
                date=date,
                screen_name=screen_name,
                id=row["post_id"],
                title=(row["text"] or "").splitlines()[0][:80],
            )
        except (KeyError, ValueError) as exc:
            raise ConfigError(f"export.filename 模板无效: {exc}") from exc
        filename = sanitize_filename(filename)
        if not filename.lower().endswith(".md"):
            filename += ".md"
        directory = base / sanitize_filename(screen_name) if cfg.export.layout == "by_account" else base
        directory.mkdir(parents=True, exist_ok=True)
        try:
            directory.chmod(0o700)
        except OSError:
            pass
        target = directory / filename
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(body, encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(target)
        store.mark_markdown(row["post_id"], str(target), digest)
        written.append(target)
    return written


def _yaml_string(value: str) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _load_entities(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _entity_links(entities: dict[str, Any], include_media: bool) -> list[str]:
    links: list[str] = []
    for item in entities.get("urls", []) if isinstance(entities.get("urls"), list) else []:
        if isinstance(item, dict):
            link = item.get("expanded_url") or item.get("url")
            if link:
                links.append(str(link))
    if include_media:
        for item in entities.get("media", []) if isinstance(entities.get("media"), list) else []:
            if isinstance(item, dict):
                link = item.get("media_url_https") or item.get("media_url") or item.get("url")
                if link:
                    links.append(str(link))
    return list(dict.fromkeys(links))
