"""Account resolution helpers for xnews add."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .errors import UsageError

@dataclass(frozen=True)
class Candidate:
    nick: str
    screen_name: str
    rest_id: str
    description: str = ""
    avatar: str = ""
    raw: dict[str, Any] | None = None

    def render(self, index: int) -> str:
        line = f"{index}. {self.nick or self.screen_name}  @{self.screen_name or '?'}"
        if self.rest_id:
            line += f"  ({self.rest_id})"
        if self.description:
            line += f"\n   {self.description[:160]}"
        return line


def profile_candidate(payload: dict[str, Any], requested: str = "") -> Candidate | None:
    data = payload.get("data") if isinstance(payload, dict) else None
    info = data if isinstance(data, dict) else payload
    info = _find_user(info) or {}
    screen_name = _value(info, "screen_name", "username", "handle") or requested.lstrip("@")
    rest_id = _value(info, "rest_id", "user_id", "id_str", "id") or ""
    nick = _value(info, "name", "display_name", "nick_name") or screen_name
    if not screen_name and not rest_id:
        return None
    return Candidate(
        nick=nick,
        screen_name=screen_name,
        rest_id=rest_id,
        description=_value(info, "description", "bio", "desc") or "",
        avatar=_value(info, "profile_image_url_https", "avatar", "avatar_url") or "",
        raw=info,
    )


def search_candidates(payload: dict[str, Any]) -> list[Candidate]:
    result: list[Candidate] = []
    seen: set[str] = set()
    for item in _find_items(payload):
        info = _find_user(item) or item
        candidate = profile_candidate({"data": info})
        if candidate is None:
            continue
        identity = candidate.rest_id or candidate.screen_name.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        result.append(candidate)
    return result


def normalize_handle(value: str) -> str:
    value = value.strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", value):
        raise UsageError(f"不是合法的 X 用户名: {value}")
    return value


def _find_items(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        for key in ("items", "users", "results", "accounts"):
            value = node.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        for value in node.values():
            found = _find_items(value)
            if found:
                return found
    elif isinstance(node, list):
        return [item for item in node if isinstance(item, dict)]
    return []


def _find_user(node: Any) -> dict[str, Any] | None:
    if isinstance(node, dict):
        if any(node.get(key) not in (None, "") for key in ("screen_name", "username", "rest_id", "user_id")):
            return node
        for key in ("user_info", "userInfo", "user", "author", "account"):
            if isinstance(node.get(key), dict):
                found = _find_user(node[key])
                if found:
                    return found
        for value in node.values():
            found = _find_user(value)
            if found:
                return found
    return None


def _value(data: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return str(value)
    return None
