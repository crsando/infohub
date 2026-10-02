"""采集主流程。

单轮做的事：
    列表 → 本地去重 → 只对新文章抓正文 → 写 raw + 入库 → 导出 Markdown

成本控制的要点全在这里：**只拉第一页 + URL 去重 + 只对新文章抓详情**。
无新文章时整轮只有 1 次调用、约 2.3 秒；有新文章才 2 次调用、约 10 秒。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from . import paths, store as store_mod
from .api import TikHubClient
from .config import Config
from .errors import UpstreamError, WxmpError
from .store import Store, write_raw_atomic

log = logging.getLogger(__name__)


@dataclass
class AccountResult:
    username: str
    nick: str
    new_articles: int = 0
    status: str = "ok"
    message: str = ""
    new_urls: list[str] = field(default_factory=list)


@dataclass
class RunSummary:
    results: list[AccountResult] = field(default_factory=list)
    started_at: int = 0
    ended_at: int = 0

    @property
    def total_new(self) -> int:
        return sum(r.new_articles for r in self.results)

    @property
    def failed(self) -> list[AccountResult]:
        return [r for r in self.results if r.status != "ok"]


def collect_account(
    client: TikHubClient,
    store: Store,
    cfg: Config,
    username: str,
    nick: str = "",
    pages: int = 1,
) -> AccountResult:
    """抓一个号。单账号失败不影响其它账号 —— 由调用方捕获异常后继续下一个。"""
    res = AccountResult(username=username, nick=nick or username)
    known = store.known_urls(username)
    offset: str | None = None
    collected_at = store_mod.now_ts()

    for page in range(max(1, pages)):
        articles, next_offset, is_end = client.account_articles(username, offset=offset)
        if not articles:
            break

        for art in articles:
            raw_url = (art.get("url") or "").strip()
            if not raw_url:
                continue
            # ⚠️ 必须归一化后再比对：上游每次返回的 URL 里 chksm 都不同，
            # 直接拿原串当主键会导致同一篇文章每轮都被重复抓取。
            url = store_mod.canonical_url(raw_url)
            if url in known:
                continue

            published = int(art.get("create_time") or 0)
            ident = store_mod.url_id(raw_url)

            # 只有新文章才付这次调用 —— 这是省钱的关键
            detail = client.article_detail(raw_url)
            content = detail["content"]

            raw_path = ""
            if cfg.storage.keep_raw:
                p = write_raw_atomic(
                    paths.raw_dir(), username, published, ident, detail["envelope"]
                )
                raw_path = str(p)

            inserted = store.insert_article(
                store_mod.ArticleRow(
                    url=url,
                    account=username,
                    title=content.get("title") or art.get("title") or "",
                    digest=art.get("digest") or "",
                    content=content.get("content_text") or "",
                    published=published,
                    collected=collected_at,
                    raw_path=raw_path,
                )
            )
            if inserted:
                res.new_articles += 1
                res.new_urls.append(url)
                known.add(url)
                log.info("新文章: %s", content.get("title") or url[:60])

        # 翻页游标只活在本次调用链里，绝不跨次保存
        if is_end or not next_offset or page + 1 >= pages:
            break
        offset = next_offset

    store.mark_run(username, collected_at)
    return res


def run(
    cfg: Config,
    store: Store,
    account_keys: list[str] | None = None,
    pages: int = 1,
    dry_run: bool = False,
    export_fn=None,
) -> RunSummary:
    """跑一轮采集。

    dry_run 只列将要处理的账号，不发任何请求 —— 用来确认配置对不对再花钱。
    """
    started = store_mod.now_ts()
    summary = RunSummary(started_at=started)

    targets = []
    for acc in cfg.accounts:
        if account_keys and not _matches(acc, account_keys):
            continue
        if not acc.enabled:
            continue
        targets.append(acc)

    if dry_run:
        for acc in targets:
            summary.results.append(
                AccountResult(
                    username=acc.username,
                    nick=acc.nick,
                    status="dry-run",
                    message=f"将拉取（最多 {pages} 页）",
                )
            )
        summary.ended_at = store_mod.now_ts()
        return summary

    if not targets:
        summary.ended_at = store_mod.now_ts()
        return summary

    client = TikHubClient(
        token=cfg.provider.token,
        base_url=cfg.provider.base_url,
        timeout=cfg.provider.timeout,
        max_retries=cfg.provider.retry.max,
        backoff=cfg.provider.retry.backoff,
        qps=cfg.provider.qps,
    )
    try:
        for acc in targets:
            t0 = time.monotonic()
            try:
                r = collect_account(client, store, cfg, acc.username, acc.nick, pages=pages)
                store.set_account_error(acc.username, None)
            except WxmpError as exc:
                # 单个号失败不中断整轮
                log.error("账号 %s 失败: %s", acc.key(), exc.message)
                store.set_account_error(acc.username, exc.message)
                r = AccountResult(
                    username=acc.username,
                    nick=acc.nick,
                    status="error",
                    message=exc.message,
                )
            except Exception as exc:  # noqa: BLE001 — 兜住任何意外，保住其它账号
                log.exception("账号 %s 出现未预期错误", acc.key())
                store.set_account_error(acc.username, repr(exc))
                r = AccountResult(
                    username=acc.username,
                    nick=acc.nick,
                    status="error",
                    message=repr(exc),
                )
            r.message = r.message or f"用时 {time.monotonic() - t0:.1f}s"
            summary.results.append(r)
    finally:
        client.close()

    summary.ended_at = store_mod.now_ts()
    if export_fn is not None and summary.total_new:
        try:
            export_fn(summary)
        except Exception:  # noqa: BLE001 — 导出失败不该让采集成果作废
            log.exception("导出失败，但文章已入库")
    return summary


def _matches(acc, keys: list[str]) -> bool:
    lowered = {k.strip().lower() for k in keys}
    for candidate in (acc.username, acc.user_name, acc.nick):
        if candidate and candidate.lower() in lowered:
            return True
    return False
