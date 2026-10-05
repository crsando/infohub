"""Command line entry point for infohub."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import __version__, paths
from .config import Config
from .errors import ConfigError, InfohubError
from .llm import LLMClient
from .memos import MemosClient
from .pipeline import ensure_summary_queue, publish_ready, run_pipeline, summarize_pending
from .store import Store

REPO_ROOT = Path(__file__).resolve().parents[2]


def cmd_init(args: argparse.Namespace) -> int:
    target = paths.resolve_config_path()
    if target.exists() and not args.force:
        raise ConfigError(f"配置已存在: {target}", "使用 `infohub init --force` 覆盖")
    config = Config.default()
    config.save(target)
    paths.ensure_dirs()
    print(f"已创建配置: {target}")
    print(f"数据目录: {paths.data_dir()}")
    return 0


def load_config() -> Config:
    return Config.load()


def cmd_check(args: argparse.Namespace) -> int:
    config = load_config()
    paths.ensure_dirs()
    print(f"配置文件: {config.path}")
    print(f"数据目录: {paths.data_dir()}")
    print(f"Qwen: {config.llm.effective_base_url()} / {config.llm.model}")
    print(f"Memos: {config.memos.effective_base_url()}{config.memos.endpoint}")
    ok = True
    for name, source in config.sources.items():
        database = Path(source.database).expanduser()
        if not database.is_absolute():
            database = REPO_ROOT / database
        state = "可读" if database.exists() else "不存在"
        print(f"source {name}: {state} ({database})")
        if source.enabled and not database.exists():
            ok = False
    if args.llm:
        try:
            payload = LLMClient(config.llm, config.llm_api_key()).models()
            print(f"LLM: 可用（{len(payload.get('data', [])) if isinstance(payload.get('data'), list) else '响应正常'}）")
        except InfohubError as exc:
            print(f"LLM: {exc.render()}", file=sys.stderr)
            ok = False
    if args.memos:
        try:
            MemosClient(config.memos, config.memos_token()).check()
            print("Memos: 可用")
        except InfohubError as exc:
            print(f"Memos: {exc.render()}", file=sys.stderr)
            ok = False
    with Store(paths.db_path()) as store:
        print(f"timeline.db: {store.stats()}")
    return 0 if ok else 1


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config()
    with Store(paths.db_path()) as store:
        result = run_pipeline(
            config,
            store,
            REPO_ROOT,
            source_names=args.source or None,
            run_sources=False if args.no_source_run else None,
            do_summarize=not args.no_summarize,
            do_publish=False if args.no_publish else None,
            dry_run=args.dry_run,
            limit=args.limit,
        )
    print_result(result)
    return 1 if result.errors else 0


def cmd_ingest(args: argparse.Namespace) -> int:
    config = load_config()
    with Store(paths.db_path()) as store:
        result = run_pipeline(
            config,
            store,
            REPO_ROOT,
            source_names=args.source or None,
            run_sources=False,
            do_summarize=False,
            do_publish=False,
            limit=args.limit,
        )
    print_result(result)
    return 1 if result.errors else 0


def cmd_summarize(args: argparse.Namespace) -> int:
    config = load_config()
    with Store(paths.db_path()) as store:
        ensure_summary_queue(config, store, limit=args.limit)
        success, failed, errors = summarize_pending(config, store, limit=args.limit)
    print(f"摘要完成: {success}，失败: {failed}")
    for error in errors[:10]:
        print(f"  {error}", file=sys.stderr)
    return 1 if failed else 0


def cmd_publish(args: argparse.Namespace) -> int:
    config = load_config()
    with Store(paths.db_path()) as store:
        success, failed, errors = publish_ready(config, store, limit=args.limit)
    print(f"Memos 发布完成: {success}，失败: {failed}")
    for error in errors[:10]:
        print(f"  {error}", file=sys.stderr)
    return 1 if failed else 0


def cmd_status(args: argparse.Namespace) -> int:
    with Store(paths.db_path()) as store:
        print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
    return 0


def cmd_permissions(args: argparse.Namespace) -> int:
    paths.fix_permissions()
    print(f"已修复 infohub 数据目录权限: {paths.data_dir()}")
    return 0


def print_result(result: object) -> None:
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="infohub",
        description="统一运行 wxmp/xnews，调用 Qwen 摘要并发布到 Memos",
    )
    parser.add_argument("--version", action="version", version=f"infohub {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<命令>")

    command = sub.add_parser("init", help="创建配置和数据目录")
    command.add_argument("--force", action="store_true")
    command.set_defaults(func=cmd_init)

    command = sub.add_parser("check", help="检查配置、数据源和外部服务")
    command.add_argument("--llm", action="store_true", help="检查 Qwen /v1/models")
    command.add_argument("--memos", action="store_true", help="检查 Memos API")
    command.set_defaults(func=cmd_check)

    command = sub.add_parser("run", help="执行采集、增量同步、摘要和发布")
    command.add_argument("--source", action="append", choices=["wxmp", "xnews"])
    command.add_argument("--no-source-run", action="store_true", help="不运行上游采集器")
    command.add_argument("--no-summarize", action="store_true")
    command.add_argument("--no-publish", action="store_true")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--limit", type=int)
    command.set_defaults(func=cmd_run)

    command = sub.add_parser("ingest", help="只从源 SQLite 同步增量")
    command.add_argument("--source", action="append", choices=["wxmp", "xnews"])
    command.add_argument("--limit", type=int)
    command.set_defaults(func=cmd_ingest)

    command = sub.add_parser("summarize", help="处理待摘要队列")
    command.add_argument("--limit", type=int)
    command.set_defaults(func=cmd_summarize)

    command = sub.add_parser("publish", help="发布已完成摘要到 Memos")
    command.add_argument("--limit", type=int)
    command.set_defaults(func=cmd_publish)

    command = sub.add_parser("status", help="查看本地队列状态")
    command.set_defaults(func=cmd_status)

    command = sub.add_parser("fix-permissions", help="修复 infohub 管理目录权限")
    command.set_defaults(func=cmd_permissions)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        return int(args.func(args))
    except InfohubError as exc:
        print(exc.render(), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
