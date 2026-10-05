"""Configuration model, validation and atomic persistence."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from . import paths
from .errors import ConfigError

CURRENT_VERSION = 1
ENV_TIKHUB_TOKEN = "TIKHUB_TOKEN"


@dataclass
class RetryConfig:
    max: int = 3
    backoff: list[int] = field(default_factory=lambda: [2, 5, 15])


@dataclass
class ProviderConfig:
    name: str = "tikhub"
    base_url: str = "https://api.tikhub.io"
    token: str = ""
    timeout: int = 60
    retry: RetryConfig = field(default_factory=RetryConfig)
    qps: float = 1.0


@dataclass
class ScheduleConfig:
    timezone: str = "Asia/Shanghai"
    interval_seconds: int = 900
    jitter_seconds: int = 0


@dataclass
class StorageConfig:
    data_dir: str | None = None
    keep_raw: bool = True
    keep_response_snapshots: bool = True


@dataclass
class ExportConfig:
    enabled: bool = True
    dir: str = ""
    layout: str = "by_account"
    filename: str = "{date} {screen_name} {id}.md"
    include_media_links: bool = True
    include_pinned: bool = False


@dataclass
class CollectConfig:
    pages: int = 1
    max_posts_per_account: int = 50
    stop_at_known: bool = True
    known_streak: int = 1


@dataclass
class Account:
    key: str = ""
    nick: str = ""
    screen_name: str = ""
    rest_id: str = ""
    enabled: bool = True
    pages: int | None = None
    max_posts: int | None = None
    include_pinned: bool | None = None
    added_at: str = ""
    last_run_at: int | None = None
    last_error: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Account":
        return cls(
            key=str(data.get("key") or "").strip(),
            nick=str(data.get("nick") or "").strip(),
            screen_name=str(data.get("screen_name") or "").strip().lstrip("@"),
            rest_id=str(data.get("rest_id") or "").strip(),
            enabled=bool(data.get("enabled", True)),
            pages=_optional_int(data.get("pages")),
            max_posts=_optional_int(data.get("max_posts")),
            include_pinned=_optional_bool(data.get("include_pinned")),
            added_at=str(data.get("added_at") or ""),
            last_run_at=_optional_int(data.get("last_run_at")),
            last_error=str(data["last_error"]) if data.get("last_error") else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "nick": self.nick,
            "screen_name": self.screen_name,
            "rest_id": self.rest_id,
            "enabled": self.enabled,
            "pages": self.pages,
            "max_posts": self.max_posts,
            "include_pinned": self.include_pinned,
            "added_at": self.added_at,
            "last_run_at": self.last_run_at,
            "last_error": self.last_error,
        }

    def identity(self) -> str:
        return self.rest_id or self.screen_name or self.key


@dataclass
class Config:
    version: int = CURRENT_VERSION
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    collect: CollectConfig = field(default_factory=CollectConfig)
    accounts: list[Account] = field(default_factory=list)
    path: Path | None = None

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Config":
        p = Path(path).expanduser() if path is not None else paths.resolve_config_path()
        if not p.exists():
            raise ConfigError(f"找不到配置文件: {p}", "先运行 xnews init")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"配置文件不是合法 JSON: {p}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError("配置根节点必须是 JSON 对象")
        cfg = cls.from_dict(data)
        cfg.path = p
        cfg.validate()
        return cfg

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        provider_data = data.get("provider") or {}
        retry_data = provider_data.get("retry") or {}
        rate_data = provider_data.get("rate_limit") or {}
        provider = ProviderConfig(
            name=str(provider_data.get("name") or "tikhub"),
            base_url=str(provider_data.get("base_url") or "https://api.tikhub.io").rstrip("/"),
            token=str(provider_data.get("token") or ""),
            timeout=int(provider_data.get("timeout", 60)),
            retry=RetryConfig(
                max=int(retry_data.get("max", 3)),
                backoff=[int(x) for x in retry_data.get("backoff", [2, 5, 15])],
            ),
            qps=float(rate_data.get("qps", 1)),
        )
        schedule_data = data.get("schedule") or {}
        storage_data = data.get("storage") or {}
        export_data = data.get("export") or {}
        collect_data = data.get("collect") or {}
        cfg = cls(
            version=int(data.get("version", CURRENT_VERSION)),
            provider=provider,
            schedule=ScheduleConfig(
                timezone=str(schedule_data.get("timezone") or "Asia/Shanghai"),
                interval_seconds=int(schedule_data.get("interval_seconds", 900)),
                jitter_seconds=int(schedule_data.get("jitter_seconds", 0)),
            ),
            storage=StorageConfig(
                data_dir=str(storage_data["data_dir"]) if storage_data.get("data_dir") else None,
                keep_raw=bool(storage_data.get("keep_raw", True)),
                keep_response_snapshots=bool(storage_data.get("keep_response_snapshots", True)),
            ),
            export=ExportConfig(
                enabled=bool(export_data.get("enabled", True)),
                dir=str(export_data.get("dir") or ""),
                layout=str(export_data.get("layout") or "by_account"),
                filename=str(export_data.get("filename") or "{date} {screen_name} {id}.md"),
                include_media_links=bool(export_data.get("include_media_links", True)),
                include_pinned=bool(export_data.get("include_pinned", False)),
            ),
            collect=CollectConfig(
                pages=int(collect_data.get("pages", 1)),
                max_posts_per_account=int(collect_data.get("max_posts_per_account", 50)),
                stop_at_known=bool(collect_data.get("stop_at_known", True)),
                known_streak=int(collect_data.get("known_streak", 1)),
            ),
            accounts=[Account.from_dict(x) for x in data.get("accounts", [])],
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.version != CURRENT_VERSION:
            raise ConfigError(f"不支持的配置版本: {self.version}")
        parsed = urlparse(self.provider.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigError("provider.base_url 必须是合法 HTTP(S) URL")
        if self.provider.timeout <= 0 or self.provider.retry.max < 0:
            raise ConfigError("provider.timeout 必须为正，retry.max 不能为负")
        if any(x < 0 for x in self.provider.retry.backoff):
            raise ConfigError("retry.backoff 不能包含负数")
        if self.schedule.interval_seconds <= 0 or self.schedule.jitter_seconds < 0:
            raise ConfigError("schedule.interval_seconds 必须为正，jitter 不能为负")
        if self.collect.pages <= 0 or self.collect.max_posts_per_account <= 0:
            raise ConfigError("collect.pages 和 max_posts_per_account 必须为正")
        if self.collect.known_streak <= 0:
            raise ConfigError("collect.known_streak 必须为正")
        if self.export.layout not in {"by_account", "flat"}:
            raise ConfigError("export.layout 只能是 by_account 或 flat")
        keys: set[str] = set()
        rest_ids: set[str] = set()
        handles: set[str] = set()
        for account in self.accounts:
            if not account.key:
                raise ConfigError("每个账号必须有 key")
            if account.key.lower() in keys:
                raise ConfigError(f"账号 key 重复: {account.key}")
            keys.add(account.key.lower())
            if not account.screen_name and not account.rest_id:
                raise ConfigError(f"账号 {account.key} 缺少 screen_name/rest_id")
            if account.rest_id:
                if account.rest_id in rest_ids:
                    raise ConfigError(f"rest_id 重复: {account.rest_id}")
                rest_ids.add(account.rest_id)
            if account.screen_name:
                normalized = account.screen_name.casefold()
                if normalized in handles:
                    raise ConfigError(f"screen_name 重复: {account.screen_name}")
                handles.add(normalized)
            if account.pages is not None and account.pages <= 0:
                raise ConfigError(f"账号 {account.key} 的 pages 必须为正")
            if account.max_posts is not None and account.max_posts <= 0:
                raise ConfigError(f"账号 {account.key} 的 max_posts 必须为正")

    def effective_token(self) -> str:
        return os.environ.get(ENV_TIKHUB_TOKEN, "").strip() or self.provider.token.strip()

    def token_source(self) -> str:
        return "环境变量 TIKHUB_TOKEN" if os.environ.get(ENV_TIKHUB_TOKEN, "").strip() else "配置文件 provider.token"

    def find_account(self, key: str) -> Account | None:
        wanted = key.strip().casefold()
        for account in self.accounts:
            if wanted in {account.key.casefold(), account.nick.casefold(), account.screen_name.casefold(), account.rest_id.casefold()}:
                return account
        return None

    def add_account(self, account: Account) -> None:
        for existing in self.accounts:
            if account.key and account.key.casefold() == existing.key.casefold():
                raise ConfigError(f"监听项已存在: {existing.key}")
            if account.rest_id and account.rest_id == existing.rest_id:
                raise ConfigError(f"账号已监听: {existing.nick or existing.screen_name}")
            if account.screen_name and account.screen_name.casefold() == existing.screen_name.casefold():
                raise ConfigError(f"账号已监听: @{existing.screen_name}")
        self.accounts.append(account)

    def remove_account(self, key: str) -> Account:
        account = self.find_account(key)
        if account is None:
            raise ConfigError(f"未找到监听账号: {key}")
        self.accounts.remove(account)
        return account

    def effective_pages(self, account: Account, override: int | None = None) -> int:
        return override or account.pages or self.collect.pages

    def effective_max_posts(self, account: Account, override: int | None = None) -> int:
        return override or account.max_posts or self.collect.max_posts_per_account

    def effective_include_pinned(self, account: Account) -> bool:
        return self.export.include_pinned if account.include_pinned is None else account.include_pinned

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "provider": {
                "name": self.provider.name,
                "base_url": self.provider.base_url,
                "token": self.provider.token,
                "timeout": self.provider.timeout,
                "retry": {"max": self.provider.retry.max, "backoff": self.provider.retry.backoff},
                "rate_limit": {"qps": self.provider.qps},
            },
            "schedule": {
                "timezone": self.schedule.timezone,
                "interval_seconds": self.schedule.interval_seconds,
                "jitter_seconds": self.schedule.jitter_seconds,
            },
            "storage": {
                "data_dir": self.storage.data_dir,
                "keep_raw": self.storage.keep_raw,
                "keep_response_snapshots": self.storage.keep_response_snapshots,
            },
            "export": {
                "enabled": self.export.enabled,
                "dir": self.export.dir,
                "layout": self.export.layout,
                "filename": self.export.filename,
                "include_media_links": self.export.include_media_links,
                "include_pinned": self.export.include_pinned,
            },
            "collect": {
                "pages": self.collect.pages,
                "max_posts_per_account": self.collect.max_posts_per_account,
                "stop_at_known": self.collect.stop_at_known,
                "known_streak": self.collect.known_streak,
            },
            "accounts": [a.to_dict() for a in self.accounts],
        }

    def save(self, path: Path | str | None = None) -> Path:
        self.validate()
        target = Path(path).expanduser() if path is not None else self.path
        if target is None:
            target = paths.resolve_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.parent.chmod(0o700)
        except OSError:
            pass
        fd, tmp_name = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, target)
            try:
                target.chmod(0o600)
            except OSError:
                pass
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        self.path = target
        return target

    @classmethod
    def default(cls) -> "Config":
        return cls(export=ExportConfig(enabled=False, dir=""), accounts=[])


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)
