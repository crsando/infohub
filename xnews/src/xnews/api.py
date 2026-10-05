"""TiKHub X/Twitter HTTP client."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .errors import AuthError, UpstreamError

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

EP_SEARCH = "/api/v1/twitter/web/fetch_search_timeline"
EP_PROFILE = "/api/v1/twitter/web/fetch_user_profile"
EP_USER_POSTS = "/api/v1/twitter/web/fetch_user_post_tweet"
EP_FOLLOWINGS = "/api/v1/twitter/web/fetch_user_followings"


class TikHubClient:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://api.tikhub.io",
        timeout: int = 60,
        max_retries: int = 3,
        backoff: list[int] | None = None,
        qps: float = 1.0,
    ) -> None:
        if not token.strip():
            raise AuthError("未配置 TiKHub token", "设置 TIKHUB_TOKEN 或 provider.token")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.backoff = backoff or [2, 5, 15]
        self.min_interval = 1.0 / qps if qps > 0 else 0.0
        self._last_call = 0.0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            }
        )
        self._token = token

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        delay = self.min_interval - (time.monotonic() - self._last_call)
        if delay > 0:
            time.sleep(delay)
        self._last_call = time.monotonic()

    def _get(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        url = self.base_url + endpoint
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if attempt:
                wait = self.backoff[min(attempt - 1, len(self.backoff) - 1)] if self.backoff else 1
                log.warning("第 %d 次重试 %s，等待 %ds", attempt, endpoint, wait)
                time.sleep(wait)
            self._throttle()
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
            except requests.exceptions.Timeout as exc:
                last_error = exc
                continue
            except requests.exceptions.RequestException as exc:
                last_error = exc
                continue

            if response.status_code in (401, 403):
                raise AuthError(
                    f"TiKHub 拒绝请求 (HTTP {response.status_code})",
                    "检查 TIKHUB_TOKEN、套餐权限和 User-Agent",
                )
            if response.status_code == 429:
                last_error = UpstreamError("TiKHub 限流 (HTTP 429)")
                continue
            if response.status_code >= 500:
                last_error = UpstreamError(f"TiKHub 服务错误 (HTTP {response.status_code})")
                continue
            if response.status_code != 200:
                raise UpstreamError(
                    f"TiKHub 返回 HTTP {response.status_code}: {redact(response.text[:300], self._token)}"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise UpstreamError("TiKHub 返回了非 JSON 内容") from exc
            if not isinstance(payload, dict):
                raise UpstreamError("TiKHub 返回的 JSON 根节点不是对象")
            code = payload.get("code")
            if code not in (None, 0, 200, "0", "200"):
                message = payload.get("message_zh") or payload.get("message") or payload.get("msg") or code
                raise UpstreamError(f"TiKHub 业务错误: {redact(str(message), self._token)}")
            return payload

        raise UpstreamError(
            f"请求失败，已重试 {self.max_retries} 次: {redact(str(last_error), self._token)}",
            "检查网络、配额或稍后再试",
        )

    def search_timeline(
        self,
        keyword: str,
        *,
        search_type: str = "People",
        cursor: str | None = None,
    ) -> dict[str, Any]:
        params = {"keyword": keyword, "search_type": search_type}
        if cursor:
            params["cursor"] = cursor
        return self._get(EP_SEARCH, params)

    def user_profile(self, *, screen_name: str | None = None, rest_id: str | None = None) -> dict[str, Any]:
        params = _identity_params(screen_name=screen_name, rest_id=rest_id)
        return self._get(EP_PROFILE, params)

    def user_posts(
        self,
        *,
        screen_name: str | None = None,
        rest_id: str | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        params = _identity_params(screen_name=screen_name, rest_id=rest_id)
        if cursor:
            params["cursor"] = cursor
        return self._get(EP_USER_POSTS, params)

    def user_followings(
        self,
        screen_name: str,
        *,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        params = {"screen_name": screen_name}
        if cursor:
            params["cursor"] = cursor
        return self._get(EP_FOLLOWINGS, params)

    def close(self) -> None:
        self.session.close()


def _identity_params(*, screen_name: str | None, rest_id: str | None) -> dict[str, str]:
    if rest_id and str(rest_id).strip():
        return {"rest_id": str(rest_id).strip()}
    if screen_name and str(screen_name).strip():
        return {"screen_name": str(screen_name).strip().lstrip("@")}
    raise ValueError("必须提供 screen_name 或 rest_id")


def redact(value: str, token: str) -> str:
    return value.replace(token, "[REDACTED]") if token else value
