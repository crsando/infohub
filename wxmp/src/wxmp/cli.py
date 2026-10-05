"""wxmp 命令行入口。

单一入口，所有功能挂子命令。退出码是对外契约（见 errors.ExitCode），
脚本化调用时要能靠它判断失败原因。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

from . import __version__, paths
from . import store as store_mod
from .api import TikHubClient
from .config import Account, Config, default_config, normalize_time
from .errors import (
    AuthError,
    ConfigError,
    DisambiguationError,
    ExitCode,
    UsageError,
    WxmpError,
)
from .export import export_articles
from .pipeline import run as run_pipeline
from .resolve import Candidate, normalize_target, resolve_by_name, resolve_by_url
from .store import Store


def _now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )


def _load_config(require: bool = True) -> Config | None:
    p = paths.resolve_config_path()
    if not p.exists():
        if require:
            raise ConfigError(
                f"找不到配置文件: {p}",
                hint="先运行 `wxmp init` 生成一份初始配置",
            )
        return None
    return Config.load(p)


def _open_store(cfg: Config) -> Store:
    return Store(paths.db_path(cfg.storage.data_dir))


# ---------------------------------------------------------------- 命令实现


def cmd_init(args) -> int:
    path = paths.resolve_config_path()
    if path.exists() and not args.force:
        raise ConfigError(
            f"配置已存在: {path}",
            hint="如需覆盖请加 --force",
        )
    cfg = default_config()
    cfg.save(path)
    paths.ensure_dirs(cfg.storage.data_dir)
    print(f"已创建配置: {path}")
    print(f"数据目录:   {paths.data_dir(cfg.storage.data_dir)}")
    print()
    print("下一步：")
    print("  1. 编辑配置填入上游 token:")
    print(f"       {path}")
    print("  2. 添加订阅:")
    print("       wxmp add 仓都加满")
    print("  3. 抓一次:")
    print("       wxmp run")
    return ExitCode.OK


def cmd_add(args) -> int:
    cfg = _load_config()
    kind, value = normalize_target(args.target)

    account: Account
    with _open_store(cfg) as store:
        client = TikHubClient(
            token=cfg.effective_token(),
            base_url=cfg.provider.base_url,
            timeout=cfg.provider.timeout,
            max_retries=cfg.provider.retry.max,
            backoff=cfg.provider.retry.backoff,
            qps=cfg.provider.qps,
        )
        try:
            if kind == "url":
                # 从文章 URL 解析 —— 全程无歧义
                cand = resolve_by_url(client, value)

            elif is_username_like(value):
                # 直接把标识当 username 用。不再搜索，但**调一次资料接口核实**：
                # 这一步能把昵称和主体公司名补全，避免订阅到一个不存在的标识。
                profile = client.account_profile(value)
                if not profile.get("nick_name"):
                    raise UsageError(
                        f"上游查不到账号「{value}」",
                        hint="确认 username 是否正确（gh_… 或自定义微信号），或改用名称搜索",
                    )
                cand = Candidate(
                    nick=profile.get("nick_name") or value,
                    username=value,
                    user_name=profile.get("user_name") or "",
                    media_name=args.media or "",
                    desc=f"原创 {profile.get('original_article_count', '?')} 篇"
                    + (f"，{profile.get('ip_wording')}" if profile.get("ip_wording") else ""),
                )

            else:
                # 按名称搜索 —— 实测有歧义，必须消歧
                cand, candidates = resolve_by_name(
                    client,
                    value,
                    pick=args.pick,
                    prefer_media=args.media or "",
                )
                if cand is None:
                    if not candidates:
                        raise UsageError(f"无法解析: {value}")
                    # 非交互环境绝不猜，直接失败并给出候选
                    if not sys.stdin.isatty():
                        raise DisambiguationError(value, candidates)
                    cand = _interactive_pick(value, candidates)
                    if cand is None:
                        print("已取消。", file=sys.stderr)
                        return ExitCode.GENERIC
        finally:
            client.close()

        times = [normalize_time(t) for t in args.time] if args.time else None
        account = Account(
            nick=args.nick or cand.nick,
            username=cand.username,
            user_name=cand.user_name,
            media_name=cand.media_name,
            enabled=True,
            times=times,
            added_at=_now_iso(),
        )

        cfg.add_account(account)
        cfg.save()
        store.upsert_account(
            username=account.username,
            nick=account.nick,
            user_name=account.user_name,
            media_name=account.media_name,
            enabled=True,
            added_at=account.added_at,
        )

    print(f"已订阅: {account.nick or account.username}")
    print(f"  标识:   {account.username}")
    if account.user_name:
        print(f"  官方号: {account.user_name}")
    if account.media_name:
        print(f"  主体:   {account.media_name}")
    if account.times:
        print(f"  时刻:   {', '.join(account.times)}（号级覆盖）")
    else:
        print(f"  时刻:   {', '.join(cfg.schedule.default_times)}（全局默认）")

    if not args.no_fetch:
        print()
        print("开始首次抓取…")
        return _do_run(cfg, account_keys=[account.key()], pages=args.pages, verbose=args.verbose)
    return ExitCode.OK


def is_username_like(value: str) -> bool:
    """判断输入是否像公众号的 username（可直接当标识用，不必搜索）。

    公众号 username 一律是 ASCII（gh_xxxx 或自定义微信号如 mtlsnow / cangmanjiacang）。
    中文一定是昵称而不是 username —— 这条判据让我们能安全地区分两者，
    否则「仓都加满」这种中文昵称会被误当成标识直接去拉取。
    """
    import re

    return bool(re.fullmatch(r"[A-Za-z0-9_\-]{2,64}", value or ""))


def _interactive_pick(keyword: str, candidates) -> "object | None":
    print(f"关键词「{keyword}」命中 {len(candidates)} 个候选：\n", file=sys.stderr)
    for i, c in enumerate(candidates, 1):
        print(c.render(i), file=sys.stderr)
    print("\n注意：请以**主体公司名**判断真伪，昵称可能被模仿。", file=sys.stderr)
    try:
        raw = input(f"\n选择要订阅的序号 (1-{len(candidates)}，回车取消): ").strip()
    except EOFError:
        return None
    if not raw:
        return None
    try:
        idx = int(raw)
    except ValueError:
        print("输入不是数字，已取消。", file=sys.stderr)
        return None
    if not 1 <= idx <= len(candidates):
        print("序号超出范围，已取消。", file=sys.stderr)
        return None
    return candidates[idx - 1]


def cmd_remove(args) -> int:
    cfg = _load_config()
    acc = cfg.remove_account(args.key)
    cfg.save()
    with _open_store(cfg) as store:
        store.remove_account(acc.username, keep_data=args.keep_data)
    print(f"已移除订阅: {acc.nick or acc.username}")
    if args.keep_data:
        print("  已抓取的文章保留在库中")
    else:
        print("  已同时删除库中该号的文章")
    return ExitCode.OK


def cmd_list(args) -> int:
    cfg = _load_config()
    if args.json:
        print(json.dumps([a.to_dict() for a in cfg.accounts], ensure_ascii=False, indent=2))
        return ExitCode.OK
    if not cfg.accounts:
        print("还没有订阅。用 `wxmp add <名称或URL>` 添加。")
        return ExitCode.OK
    print(f"共 {len(cfg.accounts)} 个订阅：\n")
    for a in cfg.accounts:
        flag = "✓" if a.enabled else "✗"
        times = a.times or cfg.schedule.default_times
        scope = "号级" if a.times else "全局"
        print(f"  {flag} {a.nick or a.username}  ({a.username})")
        if a.media_name:
            print(f"      主体: {a.media_name}")
        print(f"      时刻: {', '.join(times)}  [{scope}]")
        if a.last_seen_url:
            print(f"      最近: {a.last_seen_url[:70]}")
    return ExitCode.OK


def cmd_toggle(args, enabled: bool) -> int:
    cfg = _load_config()
    acc = cfg.find_account(args.key)
    if acc is None:
        raise ConfigError(f"未找到订阅: {args.key}")
    acc.enabled = enabled
    cfg.save()
    with _open_store(cfg) as store:
        store.upsert_account(acc.username, acc.nick, acc.user_name, acc.media_name, enabled)
    print(f"{'已启用' if enabled else '已停用'}: {acc.nick or acc.username}")
    return ExitCode.OK


def cmd_schedule(args) -> int:
    cfg = _load_config()

    if args.action in (None, "show"):
        print(f"时区: {cfg.schedule.timezone}")
        print(f"全局默认时刻: {', '.join(cfg.schedule.default_times)}")
        print(f"补跑: {'开启' if cfg.schedule.catch_up_on_run else '关闭'}")
        overrides = [a for a in cfg.accounts if a.times]
        if overrides:
            print("\n号级覆盖:")
            for a in overrides:
                print(f"  {a.nick or a.username}: {', '.join(a.times)}")
        print("\n注：定时**触发**尚未实现（设计稿 §7，延后）。")
        print("    此处只维护配置，正式跑请手动执行 `wxmp run`。")
        return ExitCode.OK

    if args.action == "set":
        if not args.times:
            raise UsageError("请提供至少一个时刻，例如 `wxmp schedule set 09:00 16:00`")
        times = [normalize_time(t) for t in args.times]
        if args.account:
            acc = cfg.find_account(args.account)
            if acc is None:
                raise ConfigError(f"未找到订阅: {args.account}")
            acc.times = times
            print(f"已设置 {acc.nick or acc.username} 的时刻: {', '.join(times)}")
        else:
            cfg.schedule.default_times = times
            print(f"已设置全局默认时刻: {', '.join(times)}")
        cfg.save()
        return ExitCode.OK

    if args.action == "unset":
        if not args.account:
            raise UsageError("unset 需要 --account 指定要清除覆盖的账号")
        acc = cfg.find_account(args.account)
        if acc is None:
            raise ConfigError(f"未找到订阅: {args.account}")
        acc.times = None
        cfg.save()
        print(f"已清除 {acc.nick or acc.username} 的号级覆盖，回到全局默认")
        return ExitCode.OK

    if args.action == "install":
        raise UsageError(
            "定时安装尚未实现（延后到 M6+）",
            hint="现在请手动执行 `wxmp run`，或自己写一条 crontab 调用它",
        )

    raise UsageError(f"未知的 schedule 动作: {args.action}")


def _do_run(cfg: Config, account_keys=None, pages: int = 1, verbose: bool = False) -> int:
    with _open_store(cfg) as store:
        def _export(summary):
            if not cfg.export.enabled or not cfg.export.dir:
                return
            paths_written = export_articles(store, cfg, only_unexported=True)
            if paths_written:
                print(f"已导出 {len(paths_written)} 篇 Markdown 到 {cfg.export.dir}")

        summary = run_pipeline(
            cfg,
            store,
            account_keys=account_keys,
            pages=pages,
            export_fn=_export,
        )

        for r in summary.results:
            mark = "✓" if r.status == "ok" else "✗"
            name = r.nick or r.username
            print(f"  {mark} {name}: 新增 {r.new_articles} 篇  ({r.message})")
        print()
        print(f"本轮共新增 {summary.total_new} 篇，用时 {summary.ended_at - summary.started_at}s")
        if summary.failed:
            print(f"失败 {len(summary.failed)} 个账号，详见上方日志。", file=sys.stderr)
            return ExitCode.UPSTREAM
    return ExitCode.OK


def cmd_run(args) -> int:
    cfg = _load_config()
    if args.dry_run:
        with _open_store(cfg) as store:
            summary = run_pipeline(cfg, store, account_keys=args.account, pages=args.pages, dry_run=True)
        print("将处理以下账号（未发送任何请求）：")
        for r in summary.results:
            print(f"  - {r.nick or r.username}  ({r.message})")
        return ExitCode.OK
    return _do_run(cfg, account_keys=args.account, pages=args.pages, verbose=args.verbose)


def cmd_search(args) -> int:
    cfg = _load_config()
    with _open_store(cfg) as store:
        rows = store.search(
            args.keyword,
            account=args.account,
            since=_parse_since(args.since),
            limit=args.limit,
        )
    if not rows:
        print("没有命中。")
        return ExitCode.OK
    if args.json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
        return ExitCode.OK
    print(f"命中 {len(rows)} 篇：\n")
    for r in rows:
        when = dt.datetime.fromtimestamp(r["published"]).strftime("%Y-%m-%d")
        print(f"  [{when}] {r['title']}")
        if r["snip"]:
            print(f"      …{r['snip'].replace(chr(10), ' ')}…")
        print(f"      {r['url'][:90]}")
    return ExitCode.OK


def cmd_show(args) -> int:
    cfg = _load_config()
    with _open_store(cfg) as store:
        rows = store.recent(account=args.account, limit=args.limit)
    if not rows:
        print("库里还没有文章。")
        return ExitCode.OK
    for r in rows:
        when = dt.datetime.fromtimestamp(r["published"]).strftime("%Y-%m-%d %H:%M")
        exp = "已导出" if r["exported"] else "未导出"
        print(f"  [{when}] {r['title'][:60]}")
        print(f"      {r['account']}  {exp}")
    return ExitCode.OK


def cmd_export(args) -> int:
    cfg = _load_config()
    if not cfg.export.dir:
        raise ConfigError(
            "export.dir 未配置",
            hint="在 config.json 的 export.dir 指定 Markdown 输出目录",
        )
    with _open_store(cfg) as store:
        written = export_articles(
            store,
            cfg,
            since=_parse_since(args.since),
            account=args.account,
            only_unexported=not args.all,
        )
    if not written:
        print("没有需要导出的文章。")
        return ExitCode.OK
    print(f"已导出 {len(written)} 篇到 {cfg.export.dir}")
    for p in written[:10]:
        print(f"  {p}")
    if len(written) > 10:
        print(f"  … 以及另外 {len(written) - 10} 篇")
    return ExitCode.OK


def cmd_stats(args) -> int:
    cfg = _load_config()
    with _open_store(cfg) as store:
        s = store.stats()
    print(f"配置:   {cfg.path}")
    print(f"数据:   {paths.data_dir(cfg.storage.data_dir)}")
    print(f"订阅:   {s['accounts']} 个（启用 {s['accounts_enabled']}）")
    print(f"文章:   {s['articles']} 篇（已导出 {s['exported']}）")
    print(f"库大小: {s['db_bytes'] / 1024:.1f} KB")
    print(f"检索:   {'FTS5 可用' if s['fts_available'] else '不可用'}")
    if s["last_run"]:
        print(f"最近运行: {dt.datetime.fromtimestamp(s['last_run']).strftime('%Y-%m-%d %H:%M')}")
    if s["errors"]:
        print("\n最近错误:")
        for e in s["errors"]:
            print(f"  {e['nick'] or e['username']}: {e['last_error']}")
    return ExitCode.OK


def cmd_check(args) -> int:
    cfg = _load_config()
    ok = True
    print("配置检查")
    print(f"  ✓ 配置文件: {cfg.path}")
    token = cfg.effective_token()
    if not token:
        print("  ✗ TikHub token 为空（TIKHUB_TOKEN 和 provider.token 均未设置）")
        ok = False
    else:
        print(f"  ✓ token: {token[:6]}…{token[-4:]}（已打码，来源：{cfg.token_source()}）")
    if cfg.export.enabled and cfg.export.dir:
        p = Path(cfg.export.dir).expanduser()
        try:
            p.mkdir(parents=True, exist_ok=True)
            test = p / ".wxmp-write-test"
            test.write_text("ok", encoding="utf-8")
            test.unlink()
            print(f"  ✓ 导出目录可写: {p}")
        except OSError as exc:
            print(f"  ✗ 导出目录不可写: {p} ({exc})")
            ok = False
    else:
        print("  - 导出未启用（export.enabled=false 或 dir 为空）")

    print("\n数据目录检查")
    try:
        paths.ensure_dirs(cfg.storage.data_dir)
        print(f"  ✓ 数据目录: {paths.data_dir(cfg.storage.data_dir)}")
    except OSError as exc:
        print(f"  ✗ 数据目录不可写: {paths.data_dir(cfg.storage.data_dir)} ({exc})")
        ok = False

    with _open_store(cfg) as store:
        st = store.stats()
        print(f"  ✓ 数据库可读写，FTS5 {'可用' if st['fts_available'] else '不可用'}")
        print(f"  ✓ 已收录 {st['articles']} 篇")

    if cfg.accounts:
        print("\n订阅检查")
        for a in cfg.accounts:
            print(f"  ✓ {a.nick or a.username} → {a.username}")

    if ok and token:
        print("\n上游连通性测试（会消耗 1 次调用）…")
        try:
            client = TikHubClient(
                token=token,
                base_url=cfg.provider.base_url,
                timeout=cfg.provider.timeout,
                max_retries=1,
                qps=cfg.provider.qps,
            )
            try:
                if cfg.accounts:
                    arts, _, _ = client.account_articles(cfg.accounts[0].username)
                    print(f"  ✓ 上游可用，{cfg.accounts[0].nick or cfg.accounts[0].username} 列表返回 {len(arts)} 条")
                else:
                    client.search_accounts("微信")
                    print("  ✓ 上游可用")
            finally:
                client.close()
        except WxmpError as exc:
            print(f"  ✗ 上游不可用: {exc.message}")
            ok = False

    print()
    print("体检通过 ✓" if ok else "存在问题 ✗")
    return ExitCode.OK if ok else ExitCode.CONFIG


def _parse_since(value: str | None) -> int | None:
    """支持 2026-01-01、7d、24h 三种写法。"""
    if not value:
        return None
    v = value.strip().lower()
    now = dt.datetime.now()
    import re

    m = re.fullmatch(r"(\d+)([dhw])", v)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = {
            "h": dt.timedelta(hours=n),
            "d": dt.timedelta(days=n),
            "w": dt.timedelta(weeks=n),
        }[unit]
        return int((now - delta).timestamp())
    try:
        d = dt.datetime.strptime(v, "%Y-%m-%d")
        return int(d.timestamp())
    except ValueError:
        raise UsageError(
            f"无法解析时间: {value}",
            hint="支持 2026-01-01、7d、24h 三种写法",
        ) from None


# ---------------------------------------------------------------- 参数定义


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wxmp",
        description="微信公众号订阅采集工具 —— 订阅、抓全文、存 SQLite、导出 Markdown",
        epilog="配置文件位置由环境变量 WXMP_CONFIG 指定，默认 ~/.config/wxmp/config.json",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    p.add_argument("--version", action="version", version=f"wxmp {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<命令>")

    sp = sub.add_parser("init", help="初始化配置与数据目录")
    sp.add_argument("--force", action="store_true", help="覆盖已存在的配置")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("add", help="新增订阅（名称 / 文章URL / 标识）")
    sp.add_argument("target", help="公众号名称、文章 URL，或 gh_xxx 标识")
    sp.add_argument("--nick", help="自定义显示名")
    sp.add_argument("--media", help="限定主体公司名以消歧")
    sp.add_argument("--pick", type=int, help="直接指定第 N 个候选（1 起）")
    sp.add_argument("--time", action="append", default=[], help="号级拉取时刻，可重复")
    sp.add_argument("--pages", type=int, default=1, help="首次抓取翻页数")
    sp.add_argument("--no-fetch", action="store_true", help="只加订阅，不立即抓取")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("remove", help="移除订阅")
    sp.add_argument("key", help="username / gh_号 / 昵称")
    sp.add_argument("--keep-data", action="store_true", default=True, help="保留已抓文章（默认）")
    sp.add_argument("--purge", dest="keep_data", action="store_false", help="同时删除库中文章")
    sp.set_defaults(func=cmd_remove)

    sp = sub.add_parser("list", help="列出订阅")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("enable", help="启用订阅")
    sp.add_argument("key")
    sp.set_defaults(func=lambda a: cmd_toggle(a, True))

    sp = sub.add_parser("disable", help="停用订阅")
    sp.add_argument("key")
    sp.set_defaults(func=lambda a: cmd_toggle(a, False))

    sp = sub.add_parser("schedule", help="查看/设置拉取时刻（定时触发延后）")
    sp.add_argument("action", nargs="?", choices=["show", "set", "unset", "install"], default="show")
    sp.add_argument("times", nargs="*", help="时刻列表，如 09:00 16:00")
    sp.add_argument("--account", help="针对某个账号设置/清除")
    sp.set_defaults(func=cmd_schedule)

    sp = sub.add_parser("run", help="手动刷新一次")
    sp.add_argument("--account", action="append", default=[], help="只跑指定账号，可重复")
    sp.add_argument("--pages", type=int, default=1, help="翻页数")
    sp.add_argument("--dry-run", action="store_true", help="只列账号，不发请求")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("search", help="全文检索")
    sp.add_argument("keyword")
    sp.add_argument("--account", help="限定账号")
    sp.add_argument("--since", help="起始时间：2026-01-01 / 7d / 24h")
    sp.add_argument("--limit", type=int, default=20)
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("show", help="列出最近文章")
    sp.add_argument("--account", help="限定账号")
    sp.add_argument("--limit", type=int, default=20)
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("export", help="导出 Markdown")
    sp.add_argument("--since", help="起始时间")
    sp.add_argument("--account")
    sp.add_argument("--all", action="store_true", help="重新导出全部（含已导出的）")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("stats", help="库统计")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("check", help="配置与连通性体检")
    sp.set_defaults(func=cmd_check)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return ExitCode.OK

    _setup_logging(getattr(args, "verbose", False))

    try:
        return int(args.func(args))
    except DisambiguationError as exc:
        print(exc.render(), file=sys.stderr)
        print("\n候选列表：", file=sys.stderr)
        for i, c in enumerate(exc.candidates, 1):
            print(c.render(i), file=sys.stderr)
        return exc.exit_code
    except WxmpError as exc:
        print(exc.render(), file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
