#!/usr/bin/env python3
"""Fetch and print recent X/Twitter posts through TiKHub.

The API key is intentionally read from TIKHUB_API_KEY and never hard-coded.
The default path follows the common TiKHub Twitter Web operation naming; use
--endpoint and --user-param when the current TiKHub OpenAPI document differs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://api.tikhub.io"
DEFAULT_ENDPOINT = "/api/v1/twitter/web/get_user_tweets"
DEFAULT_ACCOUNT = "MacroMargin"


def redact(value: str) -> str:
    token = os.getenv("TIKHUB_API_KEY")
    if token:
        return value.replace(token, "[REDACTED]")
    return value


def first_value(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def request_json(
    *,
    base_url: str,
    endpoint: str,
    params: dict[str, str],
    api_key: str,
    timeout: int,
) -> tuple[int, dict[str, Any] | list[Any], str]:
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    query = urlencode(params)
    request = Request(
        f"{url}?{query}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "User-Agent": "tikhub-x-latest-demo/0.1",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            raw = response.read().decode("utf-8", errors="replace")
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"TiKHub returned HTTP {error.code}: {redact(body[:1000])}"
        ) from error
    except URLError as error:
        raise RuntimeError(f"Unable to reach TiKHub: {redact(str(error.reason))}") from error

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"TiKHub returned non-JSON HTTP {status}: {redact(raw[:500])}"
        ) from error

    if not isinstance(payload, (dict, list)):
        raise RuntimeError(f"Unexpected TiKHub JSON shape: {type(payload).__name__}")
    if isinstance(payload, dict) and "code" in payload:
        code = payload.get("code")
        if code not in (None, 0, 200, "0", "200"):
            message = payload.get("message") or payload.get("msg") or "unknown error"
            raise RuntimeError(f"TiKHub business error code={code}: {redact(str(message))}")
    return status, payload, raw


def iter_post_objects(node: Any):
    if isinstance(node, dict):
        post_id = first_value(node, ("tweet_id", "status_id", "id_str", "id"))
        text = first_value(
            node,
            ("full_text", "text", "content", "tweet_text", "description"),
        )
        if post_id is not None and isinstance(text, str) and text.strip():
            created_at = first_value(
                node,
                ("created_at", "createdAt", "timestamp", "published_at", "time"),
            )
            author = node.get("author")
            if isinstance(author, dict):
                author_name = first_value(
                    author, ("screen_name", "username", "name", "handle")
                )
            else:
                author_name = first_value(
                    node, ("screen_name", "username", "author_name")
                )
            post_url = first_value(node, ("tweet_url", "status_url", "url"))
            yield {
                "id": str(post_id),
                "text": text.strip(),
                "created_at": created_at,
                "author": author_name,
                "url": post_url,
            }
        for child in node.values():
            yield from iter_post_objects(child)
    elif isinstance(node, list):
        for child in node:
            yield from iter_post_objects(child)


def extract_posts(payload: dict[str, Any] | list[Any], account: str) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for candidate in iter_post_objects(payload):
        post_id = candidate["id"]
        if post_id in unique:
            continue
        if not candidate["url"] and post_id.isdigit():
            candidate["url"] = f"https://x.com/{account}/status/{post_id}"
        unique[post_id] = candidate

    posts = list(unique.values())
    indexed = list(enumerate(posts))

    def sort_key(pair: tuple[int, dict[str, Any]]):
        index, post = pair
        parsed = parse_time(post["created_at"])
        return (parsed is not None, parsed or datetime.min.replace(tzinfo=timezone.utc), -index)

    indexed.sort(key=sort_key, reverse=True)
    return [post for _, post in indexed]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch the latest X/Twitter posts for an account through TiKHub."
    )
    parser.add_argument(
        "--account",
        default=os.getenv("TIKHUB_X_ACCOUNT", DEFAULT_ACCOUNT),
        help=f"X/Twitter account handle (default: {DEFAULT_ACCOUNT})",
    )
    parser.add_argument(
        "--endpoint",
        default=os.getenv("TIKHUB_X_ENDPOINT", DEFAULT_ENDPOINT),
        help=f"TiKHub operation path (default: {DEFAULT_ENDPOINT})",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("TIKHUB_BASE_URL", DEFAULT_BASE_URL),
        help=f"TiKHub API base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--user-param",
        default=os.getenv("TIKHUB_X_USER_PARAM", "username"),
        help="Query parameter carrying the account handle (default: username)",
    )
    parser.add_argument(
        "--limit-param",
        default=os.getenv("TIKHUB_X_LIMIT_PARAM", "count"),
        help="Query parameter carrying the page size (default: count)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=int(os.getenv("TIKHUB_X_LIMIT", "10")),
        help="Number of posts requested from TiKHub (default: 10)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("TIKHUB_TIMEOUT", "45")),
        help="HTTP timeout in seconds (default: 45)",
    )
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Additional query parameter; may be repeated",
    )
    parser.add_argument(
        "--save-raw",
        type=Path,
        help="Save the raw JSON response to this path",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Print the raw JSON response instead of the compact post list",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    api_key = os.getenv("TIKHUB_API_KEY")
    if not api_key:
        print("缺少 TIKHUB_API_KEY 环境变量。", file=sys.stderr)
        return 2
    if args.limit <= 0:
        print("--limit 必须大于 0。", file=sys.stderr)
        return 2

    params = {
        args.user_param: args.account,
        args.limit_param: str(args.limit),
    }
    for item in args.extra:
        if "=" not in item:
            print(f"--extra 参数必须是 KEY=VALUE：{item}", file=sys.stderr)
            return 2
        key, value = item.split("=", 1)
        params[key] = value

    try:
        status, payload, raw = request_json(
            base_url=args.base_url,
            endpoint=args.endpoint,
            params=params,
            api_key=api_key,
            timeout=args.timeout,
        )
    except RuntimeError as error:
        print(redact(str(error)), file=sys.stderr)
        return 1

    if args.save_raw:
        args.save_raw.parent.mkdir(parents=True, exist_ok=True)
        args.save_raw.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if args.raw:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    posts = extract_posts(payload, args.account)
    print(
        json.dumps(
            {
                "http_status": status,
                "account": args.account,
                "endpoint": args.endpoint,
                "requested": args.limit,
                "matched_posts": len(posts),
            },
            ensure_ascii=False,
        )
    )
    for index, post in enumerate(posts[: args.limit], start=1):
        print(f"\n{index}. {post['created_at'] or '(no timestamp)'}")
        print(post["text"][:1000])
        if post["url"]:
            print(f"URL: {post['url']}")

    if not posts:
        top_level = list(payload.keys()) if isinstance(payload, dict) else []
        print(
            "\n未从响应中识别到帖子。请用 --raw 查看响应，"
            "并根据当前 OpenAPI 调整 --endpoint、--user-param 或字段解析。",
            file=sys.stderr,
        )
        if top_level:
            print(f"响应顶层字段：{', '.join(top_level[:30])}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
