"""TikHub 上游客户端。

两个实测得到的硬性要求，写进代码而不是写在文档里靠人记：

1. **必须带 User-Agent**。不带的话请求会被 Cloudflare 挡下返回 **403**，
   实测用 Python 默认 UA 直接失败，加上 Mozilla/5.0 才通。
2. **next_offset 游标不跨次保存**。它是 base64 且与当前列表快照绑定，
   隔天用会错。所以翻页游标只活在单次调用链里。
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .errors import AuthError, UpstreamError

log = logging.getLogger(__name__)

# 这行不是可选项。不带 UA 会被 Cloudflare 403。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

EP_SEARCH = "/api/v1/wechat_search/v2/fetch_search"
EP_ACCOUNT_ARTICLES = "/api/v1/wechat_mp/v2/fetch_account_articles"
EP_ACCOUNT_PROFILE = "/api/v1/wechat_mp/v2/fetch_account_profile"
EP_ARTICLE_DETAIL_H5 = "/api/v1/wechat_mp/v2/fetch_article_detail_h5"


class TikHubClient:
    def __init__(
        self,
        token: str,
        base_url: str = "https://api.tikhub.io",
        timeout: int = 120,
        max_retries: int = 3,
        backoff: list[int] | None = None,
        qps: float = 1.0,
    ) -> None:
        if not token:
            raise AuthError(
                "未配置上游 token",
                hint="在 config.json 的 provider.token 填入 TikHub 的 API key",
            )
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff or [2, 5, 15]
        self.min_interval = 1.0 / qps if qps > 0 else 0.0
        self._last_call = 0.0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            }
        )

    # ---------- 内部 ----------

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        delta = time.monotonic() - self._last_call
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta)
        self._last_call = time.monotonic()

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = self.base_url + endpoint
        last_exc: Exception | None = None

        for attempt in range(self.max_retries + 1):
            if attempt:
                wait = self.backoff[min(attempt - 1, len(self.backoff) - 1)]
                log.warning("第 %d 次重试 %s，等待 %ds", attempt, endpoint, wait)
                time.sleep(wait)
            self._throttle()
            try:
                resp = self.session.post(url, json=payload, timeout=self.timeout)
            except requests.exceptions.Timeout as exc:
                last_exc = exc
                log.warning("请求超时 (%ss): %s", self.timeout, endpoint)
                continue
            except requests.exceptions.RequestException as exc:
                last_exc = exc
                log.warning("网络错误: %s", exc)
                continue

            # 401/403 分两种：Cloudflare 拦（UA 问题）和真凭据错。都归到鉴权类，提示分开。
            if resp.status_code in (401, 403):
                body = resp.text[:200]
                if "cloudflare" in body.lower() or resp.status_code == 403:
                    raise AuthError(
                        f"上游拒绝请求 (HTTP {resp.status_code})",
                        hint="403 通常是缺少 User-Agent 被 Cloudflare 拦截；"
                        "若 UA 正常则检查 provider.token 是否有效",
                    )
                raise AuthError(
                    f"凭据无效 (HTTP {resp.status_code})",
                    hint="检查 config.json 里的 provider.token",
                )

            if resp.status_code == 429:
                last_exc = UpstreamError("触发上游限流 (HTTP 429)")
                continue

            if resp.status_code >= 500:
                last_exc = UpstreamError(f"上游服务错误 (HTTP {resp.status_code})")
                continue

            if resp.status_code != 200:
                raise UpstreamError(
                    f"上游返回异常状态 {resp.status_code}: {resp.text[:200]}"
                )

            try:
                data = resp.json()
            except ValueError as exc:
                raise UpstreamError(f"上游返回非 JSON 内容: {resp.text[:200]}") from exc

            # TikHub 用 body 里的 code 表达业务错误，HTTP 仍是 200。
            code = data.get("code")
            if code not in (None, 200):
                msg = data.get("message_zh") or data.get("message") or code
                raise UpstreamError(f"上游业务错误: {msg}")

            return data

        raise UpstreamError(
            f"请求失败，已重试 {self.max_retries} 次: {last_exc}",
            hint="检查网络连通性，或稍后再试（上游可能临时限流）",
        )

    # ---------- 业务接口 ----------

    def search_accounts(self, keyword: str, limit: int = 20) -> list[dict]:
        """按关键词搜公众号。

        实测：搜「仓都加满」返回 14 条，含同名视频号与蹭名号 —— 所以调用方必须消歧，
        并打印 media_name（主体公司名）作为辨真伪的依据。
        """
        data = self._post(
            EP_SEARCH,
            {"keyword": keyword, "business_type": "account", "raw": False},
        )
        items = (data.get("data") or {}).get("items") or []
        out: list[dict] = []
        for it in items[:limit]:
            jump = it.get("jumpInfo") or {}
            source = it.get("source") or {}
            # title 带 <em class="highlight"> 高亮标签，清掉再用
            raw_title = str(it.get("title") or "")
            clean = _strip_tags(raw_title)
            out.append(
                {
                    "nick": clean,
                    "username": it.get("userName") or jump.get("aliasName") or "",
                    "user_name": it.get("userName") or "",
                    "media_name": source.get("title") or "",
                    "desc": _strip_tags(str(it.get("desc") or it.get("acctDesc") or "")),
                    "doc_id": it.get("docID") or "",
                    "raw": it,
                }
            )
        return out

    def account_articles(
        self,
        username: str,
        offset: str | None = None,
    ) -> tuple[list[dict], str | None, bool]:
        """拉一个号的文章列表。

        返回 (文章列表, next_offset, is_end)。

        注意：上游**忽略 page_size**，实测取 1 与取 40 结果完全相同，
        每页固定约 10 条。所以只能靠 next_offset 翻页，
        且 offset 传数字无效（每页都会回到第一页）。
        """
        payload: dict[str, Any] = {"username": username, "raw": False}
        if offset:
            payload["offset"] = offset
        data = self._post(EP_ACCOUNT_ARTICLES, payload)
        d = data.get("data") or {}
        articles = d.get("articles") or []
        return articles, d.get("next_offset"), bool(d.get("is_end"))

    def article_detail(self, url: str) -> dict:
        """抓文章正文。

        返回 content_noencode(HTML) 与 content_text(纯文本)。
        实测 content_text 是**有损转换**：原文的加粗、红色强调全部丢失。
        所以这里两个都返回 —— 纯文本供导出，HTML 存档备查。
        """
        data = self._post(EP_ARTICLE_DETAIL_H5, {"url": url, "raw": False})
        d = data.get("data") or {}
        content = d.get("content") or {}
        if not content:
            raise UpstreamError(
                f"未能解析文章内容: {url[:80]}",
                hint="文章可能已被删除、转为付费或需要登录",
            )
        return {"content": content, "envelope": data}

    def account_profile(self, username: str) -> dict:
        data = self._post(EP_ACCOUNT_PROFILE, {"username": username, "raw": False})
        return data.get("data") or {}

    def close(self) -> None:
        self.session.close()


def _strip_tags(text: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", text).strip()
