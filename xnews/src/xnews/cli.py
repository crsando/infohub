"""xnews command line interface."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import random
import re
import sys
import time
from pathlib import Path

from . import __version__, paths
from .api import TikHubClient
from .config import Account, Config
from .errors import (
    ConfigError,
    DisambiguationError,
    ExitCode,
    UsageError,
    XnewsError,
)
from .export import export_posts
from .parser import normalize_post
from .pipeline import run as run_pipeline
from .resolve import Candidate, normalize_handle, profile_candidate, search_candidates
from .store import Store


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def _load_config() -> Config:
    return Config.load(paths.resolve_config_path())


def _open_store(cfg: Config) -> Store:
    paths.ensure_dirs(cfg.storage.data_dir)
    return Store(paths.db_path(cfg.storage.data_dir))


def _now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def cmd_init(args: argparse.Namespace) -> int:
    path = paths.resolve_config_path()
    if path.exists() and not args.force:
        raise ConfigError(f"配置已存在: {path}", "使用 xnews init --force 覆盖")
    cfg = Config.default()
    cfg.save(path)
    paths.ensure_dirs(cfg.storage.data_dir)
    print(f"已创建配置: {path}")
    print(f"数据目录: {paths.data_dir(cfg.storage.data_dir)}")
    return ExitCode.OK


def _client(cfg: Config) -> TikHubClient:
    return TikHubClient(
        cfg.effective_token(),
        base_url=cfg.provider.base_url,
        timeout=cfg.provider.timeout,
        max_retries=cfg.provider.retry.max,
        backoff=cfg.provider.retry.backoff,
        qps=cfg.provider.qps,
    )


def cmd_add(args: argparse.Namespace) -> int:
    cfg = _load_config()
    target = args.target.strip()
    client = _client(cfg)
    try:
        if target.isdigit():
            candidate = profile_candidate(client.user_profile(rest_id=target), target)
            if candidate is None:
                raise UsageError(f"找不到 rest_id: {target}")
        elif re.fullmatch(r"@?[A-Za-z0-9_]{1,64}", target):
            handle = normalize_handle(target)
            candidate = profile_candidate(client.user_profile(screen_name=handle), handle)
            if candidate is None:
                raise UsageError(f"找不到账号: @{handle}")
        else:
            candidates = search_candidates(
                client.search_timeline(target, search_type="People")
            )
            if args.pick is not None:
                if not 1 <= args.pick <= len(candidates):
                    raise UsageError(f"--pick 超出范围，候选数为 {len(candidates)}")
                candidate = candidates[args.pick - 1]
            elif len(candidates) == 1:
                candidate = candidates[0]
            elif not candidates:
                raise UsageError(f"没有找到 X 账号: {target}")
            elif sys.stdin.isatty():
                candidate = _interactive_pick(target, candidates)
                if candidate is None:
                    return ExitCode.GENERIC
            else:
                raise DisambiguationError(target, candidates)
    finally:
        client.close()

    screen_name = candidate.screen_name or target.lstrip("@")
    key = args.key or screen_name.casefold() or f"user-{candidate.rest_id}"
    account = Account(
        key=key,
        nick=args.nick or candidate.nick or screen_name,
        screen_name=screen_name,
        rest_id=candidate.rest_id,
        enabled=True,
        added_at=_now_iso(),
    )
    cfg.add_account(account)
    cfg.save()
    with _open_store(cfg) as store:
        store.upsert_account(
            account.key,
            account.nick,
            account.screen_name,
            account.rest_id,
            account.enabled,
            account.added_at,
        )
    print(f"已添加监听: {account.nick} (@{account.screen_name})")
    if account.rest_id:
        print(f"  rest_id: {account.rest_id}")
    if not args.no_fetch:
        return _do_run(cfg, [account.key], args.pages, args.max_posts, args.verbose)
    return ExitCode.OK


def _interactive_pick(keyword: str, candidates: list[Candidate]) -> Candidate | None:
    print(f"关键词「{keyword}」命中 {len(candidates)} 个候选：", file=sys.stderr)
    for index, candidate in enumerate(candidates, 1):
        print(candidate.render(index), file=sys.stderr)
    try:
        answer = input(f"选择序号 (1-{len(candidates)}，回车取消): ").strip()
    except EOFError:
        return None
    if not answer:
        return None
    try:
        index = int(answer)
    except ValueError:
        print("输入不是数字。", file=sys.stderr)
        return None
    return candidates[index - 1] if 1 <= index <= len(candidates) else None


def cmd_list(args: argparse.Namespace) -> int:
    cfg = _load_config()
    if args.json:
        print(json.dumps([a.to_dict() for a in cfg.accounts], ensure_ascii=False, indent=2))
        return ExitCode.OK
    if not cfg.accounts:
        print("还没有监听账号。")
        return ExitCode.OK
    for account in cfg.accounts:
        flag = "✓" if account.enabled else "✗"
        print(f"{flag} {account.key}: {account.nick} (@{account.screen_name})")
        if account.rest_id:
            print(f"  rest_id: {account.rest_id}")
        if account.last_run_at:
            print(f"  上次运行: {dt.datetime.fromtimestamp(account.last_run_at).isoformat(sep=' ', timespec='seconds')}")
        if account.last_error:
            print(f"  错误: {account.last_error}")
    return ExitCode.OK


def cmd_toggle(args: argparse.Namespace, enabled: bool) -> int:
    cfg = _load_config()
    account = cfg.find_account(args.key)
    if account is None:
        raise ConfigError(f"未找到监听账号: {args.key}")
    account.enabled = enabled
    cfg.save()
    with _open_store(cfg) as store:
        store.upsert_account(
            account.key,
            account.nick,
            account.screen_name,
            account.rest_id,
            account.enabled,
            account.added_at,
        )
    print(f"{'已启用' if enabled else '已停用'}: {account.key}")
    return ExitCode.OK


def cmd_remove(args: argparse.Namespace) -> int:
    cfg = _load_config()
    account = cfg.remove_account(args.key)
    cfg.save()
    with _open_store(cfg) as store:
        paths_to_delete = store.remove_account(account.key, purge=args.purge)
    for raw_path in paths_to_delete:
        try:
            Path(raw_path).unlink(missing_ok=True)
        except OSError:
            logging.getLogger(__name__).warning("无法删除派生文件: %s", raw_path)
    print(f"已移除监听: {account.nick or account.key}")
    return ExitCode.OK


def _do_run(
    cfg: Config,
    account_keys: list[str] | None,
    pages: int | None,
    max_posts: int | None,
    verbose: bool = False,
    dry_run: bool = False,
) -> int:
    with _open_store(cfg) as store:
        summary = run_pipeline(
            cfg,
            store,
            account_keys=account_keys,
            pages=pages,
            max_posts=max_posts,
            dry_run=dry_run,
        )
    for result in summary.results:
        marker = "✓" if result.status in {"ok", "dry-run"} else "✗"
        print(
            f"{marker} {result.nick} "
            f"pages={result.pages} fetched={result.fetched} "
            f"new={result.new_posts} updated={result.updated_posts}"
            + (f" ({result.message})" if result.message else "")
        )
    print(f"本轮新增 {summary.total_new}，更新 {summary.total_updated}")
    return ExitCode.UPSTREAM if summary.failed else ExitCode.OK


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _load_config()
    return _do_run(cfg, args.account or None, args.pages, args.max_posts, args.verbose, args.dry_run)


def cmd_watch(args: argparse.Namespace) -> int:
    cfg = _load_config()
    first = True
    try:
        while first or not args.once:
            first = False
            code = _do_run(cfg, args.account or None, args.pages, args.max_posts, args.verbose)
            if args.once:
                return code
            interval = cfg.schedule.interval_seconds
            if cfg.schedule.jitter_seconds:
                interval += random.randint(0, cfg.schedule.jitter_seconds)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n已停止监听。")
        return ExitCode.OK


def cmd_search(args: argparse.Namespace) -> int:
    cfg = _load_config()
    with _open_store(cfg) as store:
        rows = store.search(args.keyword, account=args.account, limit=args.limit)
    if args.json:
        print(json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2))
        return ExitCode.OK
    if not rows:
        print("没有命中。")
        return ExitCode.OK
    print(f"命中 {len(rows)} 条：")
    for row in rows:
        when = row["created_at"] or row["collected_at"]
        date = dt.datetime.fromtimestamp(when, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
        print(f"[{date}] @{row['author_screen_name']} {row['text'][:160]}")
        print(f"  {row['url']}")
    return ExitCode.OK


def cmd_show(args: argparse.Namespace) -> int:
    cfg = _load_config()
    with _open_store(cfg) as store:
        rows = store.recent(args.account, args.limit)
    if not rows:
        print("库里还没有帖子。")
        return ExitCode.OK
    for row in rows:
        when = row["created_at"] or row["collected_at"]
        date = dt.datetime.fromtimestamp(when, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
        print(f"[{date}] @{row['author_screen_name']} {row['text'][:160]}")
        print(f"  raw: {row['raw_path'] or '-'}")
        print(f"  md:  {row['markdown_path'] or '-'}")
    return ExitCode.OK


def cmd_export(args: argparse.Namespace) -> int:
    cfg = _load_config()
    with _open_store(cfg) as store:
        written = export_posts(store, cfg, force=args.all)
    print(f"已导出 {len(written)} 个 Markdown 文件")
    for path in written[:10]:
        print(f"  {path}")
    return ExitCode.OK


def cmd_reparse(args: argparse.Namespace) -> int:
    cfg = _load_config()
    updated = 0
    with _open_store(cfg) as store:
        rows = store.posts_for_export([args.post_id] if args.post_id else None)
        for row in rows:
            raw_path = row["raw_path"]
            if not raw_path or not Path(raw_path).exists():
                continue
            try:
                payload = json.loads(Path(raw_path).read_text(encoding="utf-8"))
                raw_object = payload.get("post", payload)
            except (OSError, ValueError):
                continue
            post = normalize_post(raw_object, is_pinned=bool(row["is_pinned"]))
            if post is None:
                continue
            sources = store.source_keys(post.post_id)
            if not sources:
                continue
            with store.transaction():
                store.save_post(
                    post,
                    watch_key=sources[0],
                    collected_at=row["collected_at"],
                    raw_path=raw_path,
                )
            updated += 1
        written = export_posts(store, cfg, post_ids=[r["post_id"] for r in rows], force=True) if cfg.export.dir else []
    print(f"重新解析 {updated} 条，重导 {len(written)} 个 Markdown 文件")
    return ExitCode.OK


def cmd_stats(args: argparse.Namespace) -> int:
    cfg = _load_config()
    with _open_store(cfg) as store:
        stats = store.stats()
    print(f"配置: {cfg.path}")
    print(f"数据: {paths.data_dir(cfg.storage.data_dir)}")
    for key in ("accounts", "accounts_enabled", "posts", "snapshots", "exported"):
        print(f"{key}: {stats[key]}")
    print(f"FTS5: {'可用' if stats['fts_available'] else '不可用'}")
    print(f"数据库: {stats['db_bytes'] / 1024:.1f} KB")
    return ExitCode.OK


def cmd_check(args: argparse.Namespace) -> int:
    cfg = _load_config()
    if args.fix_permissions:
        paths.fix_permissions(cfg.storage.data_dir)
        try:
            cfg.path.chmod(0o600)
        except (AttributeError, OSError):
            pass
    ok = True
    token = cfg.effective_token()
    print(f"配置文件: {cfg.path}")
    if token:
        print(f"token: 已配置（来源：{cfg.token_source()}）")
    else:
        print("token: 缺少")
        ok = False
    try:
        paths.ensure_dirs(cfg.storage.data_dir)
        with _open_store(cfg) as store:
            print(f"数据库: 可读写，{store.stats()['posts']} 条帖子")
    except XnewsError as exc:
        print(exc.render())
        ok = False
    if args.live and token:
        client = _client(cfg)
        try:
            if cfg.accounts:
                account = cfg.accounts[0]
                client.user_posts(
                    screen_name=account.screen_name or None,
                    rest_id=account.rest_id or None,
                )
            else:
                client.search_timeline("Twitter", search_type="People")
            print("上游: 可用")
        except XnewsError as exc:
            print(f"上游: {exc.render()}")
            ok = False
        finally:
            client.close()
    elif args.live:
        print("上游: 未测试（缺少 token）")
    return ExitCode.OK if ok else ExitCode.CONFIG


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xnews",
        description="X/Twitter 账号监听与本地 raw/Markdown 归档",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"xnews {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<命令>")

    sp = sub.add_parser("init", help="初始化配置")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("add", help="添加监听账号")
    sp.add_argument("target")
    sp.add_argument("--key")
    sp.add_argument("--nick")
    sp.add_argument("--pick", type=int)
    sp.add_argument("--pages", type=int)
    sp.add_argument("--max-posts", type=int)
    sp.add_argument("--no-fetch", action="store_true")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("remove", help="删除监听账号")
    sp.add_argument("key")
    sp.add_argument("--purge", action="store_true")
    sp.set_defaults(func=cmd_remove)

    sp = sub.add_parser("list", help="列出监听账号")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_list)

    for name, enabled in (("enable", True), ("disable", False)):
        sp = sub.add_parser(name, help=f"{'启用' if enabled else '停用'}监听账号")
        sp.add_argument("key")
        sp.set_defaults(func=lambda args, enabled=enabled: cmd_toggle(args, enabled))

    for name, func in (("run", cmd_run), ("watch", cmd_watch)):
        sp = sub.add_parser(name, help="执行一轮采集" if name == "run" else "持续轮询")
        sp.add_argument("--account", action="append", default=[])
        sp.add_argument("--pages", type=int)
        sp.add_argument("--max-posts", type=int)
        sp.add_argument("--dry-run", action="store_true") if name == "run" else sp.add_argument("--once", action="store_true")
        sp.set_defaults(func=func)

    sp = sub.add_parser("search", help="本地全文检索")
    sp.add_argument("keyword")
    sp.add_argument("--account")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("show", help="列出最近帖子")
    sp.add_argument("--account")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("export", help="导出 Markdown")
    sp.add_argument("--all", action="store_true")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("reparse", help="从 raw 重新解析")
    sp.add_argument("--post-id")
    sp.set_defaults(func=cmd_reparse)

    sp = sub.add_parser("stats", help="查看统计")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("check", help="检查配置和连通性")
    sp.add_argument("--live", action="store_true")
    sp.add_argument("--fix-permissions", action="store_true")
    sp.set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return ExitCode.OK
    _setup_logging(args.verbose)
    try:
        return int(args.func(args))
    except DisambiguationError as exc:
        print(exc.render(), file=sys.stderr)
        for index, candidate in enumerate(exc.candidates, 1):
            print(candidate.render(index), file=sys.stderr)
        return exc.exit_code
    except XnewsError as exc:
        print(exc.render(), file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
