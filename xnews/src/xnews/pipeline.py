"""Collection pipeline for X/Twitter timelines."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from . import paths
from .api import TikHubClient
from .config import Account, Config
from .errors import XnewsError
from .export import export_posts
from .parser import NormalizedPost, parse_page
from .store import Store, json_hash, now_ts, write_json_atomic

log = logging.getLogger(__name__)


@dataclass
class AccountResult:
    key: str
    nick: str
    status: str = "ok"
    message: str = ""
    pages: int = 0
    fetched: int = 0
    new_posts: int = 0
    updated_posts: int = 0
    post_ids: list[str] = field(default_factory=list)


@dataclass
class RunSummary:
    run_id: int | None = None
    results: list[AccountResult] = field(default_factory=list)
    started_at: int = 0
    ended_at: int = 0

    @property
    def total_new(self) -> int:
        return sum(item.new_posts for item in self.results)

    @property
    def total_updated(self) -> int:
        return sum(item.updated_posts for item in self.results)

    @property
    def failed(self) -> list[AccountResult]:
        return [item for item in self.results if item.status != "ok"]


def collect_account(
    client: TikHubClient,
    store: Store,
    cfg: Config,
    account: Account,
    run_id: int,
    *,
    pages: int | None = None,
    max_posts: int | None = None,
) -> AccountResult:
    result = AccountResult(key=account.key, nick=account.nick or account.screen_name or account.key)
    page_limit = cfg.effective_pages(account, pages)
    post_limit = cfg.effective_max_posts(account, max_posts)
    cursor: str | None = None
    seen_in_run: set[str] = set()
    known_streak = 0
    collected_at = now_ts()

    for page_no in range(1, page_limit + 1):
        payload = client.user_posts(
            screen_name=account.screen_name or None,
            rest_id=account.rest_id or None,
            cursor=cursor,
        )
        if cfg.storage.keep_response_snapshots:
            response_path = (
                paths.response_raw_dir(cfg.storage.data_dir)
                / paths.safe_component(str(run_id))
                / paths.safe_component(account.key)
                / f"page-{page_no:03d}.json"
            )
            write_json_atomic(response_path, payload)

        parsed = parse_page(payload)
        result.pages += 1
        if parsed.warnings:
            log.warning("%s parser warnings: %s", account.key, "; ".join(parsed.warnings[:3]))

        candidates = list(parsed.timeline)
        if cfg.effective_include_pinned(account):
            candidates.extend(parsed.pinned)
        page_known_break = False

        for post in candidates:
            if len(seen_in_run) >= post_limit:
                break
            if post.post_id in seen_in_run:
                continue
            seen_in_run.add(post.post_id)
            result.fetched += 1

            existing = store.get_post(post.post_id)
            is_known_unchanged = existing is not None and existing["content_hash"] == post.content_hash
            raw_path: str | None = None
            raw_value = {
                "post_id": post.post_id,
                "watch_key": account.key,
                "collected_at": collected_at,
                "post": post.raw_object,
            }
            if cfg.storage.keep_raw and not is_known_unchanged:
                raw_path_obj = (
                    paths.post_raw_dir(cfg.storage.data_dir)
                    / paths.safe_component(post.post_id)
                    / f"{run_id}-{paths.safe_component(account.key)}-{collected_at}-{post.content_hash[:12]}.json"
                )
                write_json_atomic(raw_path_obj, raw_value)
                raw_path = str(raw_path_obj)

            with store.transaction():
                status = store.save_post(
                    post,
                    watch_key=account.key,
                    collected_at=collected_at,
                    raw_path=raw_path,
                )
                if raw_path:
                    store.add_raw_snapshot(
                        post_id=post.post_id,
                        watch_key=account.key,
                        run_id=run_id,
                        path=raw_path,
                        collected_at=collected_at,
                        content_hash=json_hash(raw_value),
                    )

            if status == "new":
                result.new_posts += 1
                result.post_ids.append(post.post_id)
                known_streak = 0
            elif status == "updated":
                result.updated_posts += 1
                result.post_ids.append(post.post_id)
                known_streak = 0
            else:
                known_streak += 1
                if cfg.collect.stop_at_known and known_streak >= cfg.collect.known_streak:
                    page_known_break = True
                    break

        if page_known_break or len(seen_in_run) >= post_limit:
            break
        if not parsed.next_cursor:
            break
        cursor = parsed.next_cursor

    store.set_account_status(account.key, last_run_at=collected_at, last_error=None)
    store.finish_run_account(
        run_id,
        account.key,
        status="ok",
        pages=result.pages,
        fetched=result.fetched,
        new_count=result.new_posts,
        updated_count=result.updated_posts,
    )
    return result


def run(
    cfg: Config,
    store: Store,
    *,
    account_keys: list[str] | None = None,
    pages: int | None = None,
    max_posts: int | None = None,
    dry_run: bool = False,
    export_fn: Callable[[list[str]], object] | None = None,
) -> RunSummary:
    started = now_ts()
    summary = RunSummary(started_at=started)
    targets = [
        account
        for account in cfg.accounts
        if account.enabled and (not account_keys or _matches(account, account_keys))
    ]
    if dry_run:
        summary.ended_at = now_ts()
        summary.results = [
            AccountResult(
                key=a.key,
                nick=a.nick or a.screen_name or a.key,
                status="dry-run",
                message=f"最多抓取 {cfg.effective_pages(a, pages)} 页/{cfg.effective_max_posts(a, max_posts)} 条",
            )
            for a in targets
        ]
        return summary
    if not targets:
        summary.ended_at = now_ts()
        return summary

    token = cfg.effective_token()
    client = TikHubClient(
        token,
        base_url=cfg.provider.base_url,
        timeout=cfg.provider.timeout,
        max_retries=cfg.provider.retry.max,
        backoff=cfg.provider.retry.backoff,
        qps=cfg.provider.qps,
    )
    run_id = store.begin_run()
    summary.run_id = run_id
    for account in targets:
        store.upsert_account(
            account.key,
            account.nick or account.screen_name or account.key,
            account.screen_name,
            account.rest_id,
            account.enabled,
            account.added_at,
            account.last_run_at,
            account.last_error,
        )
    export_ids: list[str] = []
    try:
        for account in targets:
            store.begin_run_account(run_id, account.key)
            try:
                result = collect_account(
                    client,
                    store,
                    cfg,
                    account,
                    run_id,
                    pages=pages,
                    max_posts=max_posts,
                )
                export_ids.extend(result.post_ids)
            except XnewsError as exc:
                store.set_account_status(account.key, last_error=exc.message)
                store.finish_run_account(run_id, account.key, status="error", error=exc.message)
                result = AccountResult(
                    key=account.key,
                    nick=account.nick or account.screen_name or account.key,
                    status="error",
                    message=exc.message,
                )
            except Exception as exc:
                log.exception("账号 %s 采集失败", account.key)
                message = repr(exc)
                store.set_account_status(account.key, last_error=message)
                store.finish_run_account(run_id, account.key, status="error", error=message)
                result = AccountResult(
                    key=account.key,
                    nick=account.nick or account.screen_name or account.key,
                    status="error",
                    message=message,
                )
            summary.results.append(result)
    finally:
        client.close()

    if export_ids and cfg.export.enabled and cfg.export.dir:
        try:
            if export_fn:
                export_fn(list(dict.fromkeys(export_ids)))
            else:
                export_posts(store, cfg, post_ids=list(dict.fromkeys(export_ids)))
        except Exception:
            log.exception("Markdown 导出失败，采集结果已保留")
    summary.ended_at = now_ts()
    status = "error" if summary.failed and not export_ids else ("partial" if summary.failed else "ok")
    store.finish_run(run_id, status, f"新增 {summary.total_new}，更新 {summary.total_updated}")
    return summary


def _matches(account: Account, keys: list[str]) -> bool:
    wanted = {key.strip().casefold() for key in keys}
    return any(value and value.casefold() in wanted for value in (
        account.key, account.nick, account.screen_name, account.rest_id
    ))
