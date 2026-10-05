"""Normalize the changing TiKHub X response into stable post objects."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any


@dataclass(frozen=True)
class NormalizedPost:
    post_id: str
    author_rest_id: str
    author_screen_name: str
    author_name: str
    text: str
    created_at: int | None
    url: str
    is_pinned: bool
    is_reply: bool
    is_repost: bool
    entities: dict[str, Any]
    raw_object: dict[str, Any]
    content_hash: str


@dataclass(frozen=True)
class ParsedPage:
    timeline: list[NormalizedPost]
    pinned: list[NormalizedPost]
    next_cursor: str | None
    warnings: list[str]


def parse_page(payload: dict[str, Any]) -> ParsedPage:
    data = payload.get("data") if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        data = {}
    timeline_items = _as_list(data.get("timeline")) or _find_list(payload, "timeline")
    pinned_items = _as_list(data.get("pinned")) or _find_list(payload, "pinned")
    cursor = _first_nonempty(
        data.get("next_cursor"),
        data.get("nextCursor"),
        payload.get("next_cursor") if isinstance(payload, dict) else None,
    )
    warnings: list[str] = []
    timeline = _normalize_items(timeline_items, False, warnings)
    pinned = _normalize_items(pinned_items, True, warnings)
    return ParsedPage(timeline=timeline, pinned=pinned, next_cursor=cursor, warnings=warnings)


def _normalize_items(items: list[Any], pinned: bool, warnings: list[str]) -> list[NormalizedPost]:
    result: list[NormalizedPost] = []
    seen: set[str] = set()
    for item in items:
        for candidate in _candidate_dicts(item):
            post = normalize_post(candidate, is_pinned=pinned)
            if post is None:
                continue
            if post.post_id in seen:
                continue
            seen.add(post.post_id)
            result.append(post)
    return result


def _candidate_dicts(node: Any) -> list[dict[str, Any]]:
    if not isinstance(node, dict):
        return []
    result: list[dict[str, Any]] = []
    if _post_id(node) and _post_text(node):
        result.append(node)
    for key, child in node.items():
        if key in {"user_info", "author", "user", "entities", "legacy"}:
            if key == "legacy" and isinstance(child, dict) and (_post_id(child) or _post_text(child)):
                result.extend(_candidate_dicts(child))
            continue
        if isinstance(child, dict):
            result.extend(_candidate_dicts(child))
        elif isinstance(child, list):
            for item in child:
                result.extend(_candidate_dicts(item))
    return result


def normalize_post(node: dict[str, Any], *, is_pinned: bool = False) -> NormalizedPost | None:
    post_id = _post_id(node)
    text = _post_text(node)
    if not post_id or not text:
        return None

    author = _author(node)
    author_rest_id = _first_nonempty(
        author.get("rest_id"),
        author.get("user_id"),
        author.get("id_str"),
        author.get("id"),
    ) or ""
    author_screen_name = _first_nonempty(
        author.get("screen_name"),
        author.get("username"),
        author.get("handle"),
    ) or ""
    author_name = _first_nonempty(author.get("name"), author.get("display_name")) or ""
    created_at = _parse_time(_post_value(node, ("created_at", "createdAt", "timestamp", "time")))
    url = _first_nonempty(
        _post_value(node, ("tweet_url", "status_url", "url")),
    ) or (
        f"https://x.com/{author_screen_name}/status/{post_id}"
        if author_screen_name
        else f"https://x.com/i/web/status/{post_id}"
    )
    entities = _entities(node)
    content_hash = hashlib.sha256(
        json.dumps(
            {"text": text, "entities": entities, "author": author_rest_id},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return NormalizedPost(
        post_id=str(post_id),
        author_rest_id=str(author_rest_id),
        author_screen_name=str(author_screen_name),
        author_name=str(author_name),
        text=text,
        created_at=created_at,
        url=str(url),
        is_pinned=is_pinned,
        is_reply=bool(_post_value(node, ("is_reply", "in_reply_to_status_id_str", "in_reply_to_id"))),
        is_repost=bool(_post_value(node, ("is_repost", "retweeted_status", "retweet"))),
        entities=entities,
        raw_object=node,
        content_hash=content_hash,
    )


def _post_id(node: dict[str, Any]) -> str | None:
    direct = _first_nonempty(*(node.get(key) for key in ("rest_id", "tweet_id", "status_id", "id_str", "id")))
    if direct is not None:
        return str(direct)
    for key in ("tweet", "tweetResult", "result", "legacy", "status", "content"):
        child = node.get(key)
        if isinstance(child, dict):
            value = _first_nonempty(*(child.get(k) for k in ("rest_id", "tweet_id", "status_id", "id_str", "id")))
            if value is not None:
                return str(value)
    return None


def _post_text(node: dict[str, Any]) -> str | None:
    direct = _first_nonempty(*(node.get(key) for key in ("text", "full_text", "tweet_text", "content_text")))
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for key in ("note_tweet", "legacy", "tweet", "tweetResult", "result", "content"):
        child = node.get(key)
        if isinstance(child, dict):
            value = _first_nonempty(*(child.get(k) for k in ("text", "full_text", "tweet_text", "content_text")))
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _author(node: dict[str, Any]) -> dict[str, Any]:
    for key in ("user_info", "author", "user", "author_info"):
        value = node.get(key)
        if isinstance(value, dict):
            return value
    for key in ("tweet", "tweetResult", "result", "legacy"):
        child = node.get(key)
        if isinstance(child, dict):
            found = _author(child)
            if found:
                return found
    return {}


def _entities(node: dict[str, Any]) -> dict[str, Any]:
    for key in ("entities", "extended_entities"):
        value = node.get(key)
        if isinstance(value, dict):
            return value
    for key in ("legacy", "tweet", "tweetResult", "result"):
        child = node.get(key)
        if isinstance(child, dict):
            value = _entities(child)
            if value:
                return value
    return {}


def _post_value(node: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if node.get(key) not in (None, ""):
            return node[key]
    for container in ("legacy", "tweet", "tweetResult", "result", "content"):
        child = node.get(container)
        if isinstance(child, dict):
            for key in keys:
                if child.get(key) not in (None, ""):
                    return child[key]
    return None


def _find_list(node: Any, key: str) -> list[Any]:
    if isinstance(node, dict):
        if isinstance(node.get(key), list):
            return node[key]
        for child in node.values():
            found = _find_list(child, key)
            if found:
                return found
    elif isinstance(node, list):
        for child in node:
            found = _find_list(child, key)
            if found:
                return found
    return []


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _first_nonempty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _parse_time(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())
