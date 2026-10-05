"""config.json 的加载、校验与写回。

设计取向（用户明确要求）：**不要过度设计**。
所以这里刻意不做 pydantic、不做 schema 文件、不做密钥管理层。
凭据优先从 `TIKHUB_TOKEN` 环境变量读取，没有设置时再使用 `provider.token`。

唯一的约束：写回必须是原子的（先写临时文件再 rename），
避免配置写到一半断电导致 config.json 变成半截 JSON。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths
from .errors import ConfigError

CURRENT_VERSION = 1
ENV_TIKHUB_TOKEN = "TIKHUB_TOKEN"

# HH:MM，24 小时制。允许 "9:00" 这种写法，读入后归一化成 "09:00"。
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def normalize_time(value: str) -> str:
    """把 '9:00' / '09:00' / '9:0' 统一成 '09:00'。非法则抛 ConfigError。"""
    raw = str(value).strip()
    m = _TIME_RE.match(raw)
    if not m:
        raise ConfigError(
            f"时刻格式非法: {value!r}",
            hint="应为 24 小时制 HH:MM，例如 09:00、16:30",
        )
    return f"{int(m.group(1)):02d}:{m.group(2)}"


@dataclass
class RetryConfig:
    max: int = 3
    backoff: list[int] = field(default_factory=lambda: [2, 5, 15])


@dataclass
class ProviderConfig:
    name: str = "tikhub"
    base_url: str = "https://api.tikhub.io"
    token: str = ""
    timeout: int = 120
    retry: RetryConfig = field(default_factory=RetryConfig)
    qps: float = 1.0


@dataclass
class ScheduleConfig:
    """定时配置。

    注意：定时**触发**延后到 M6+ 实现（设计稿 §7）。
    但这里照常解析与校验，保证将来加定时器时配置文件格式不用改、现有配置不作废。
    """

    timezone: str = "Asia/Shanghai"
    default_times: list[str] = field(default_factory=lambda: ["09:00", "16:00"])
    catch_up_on_run: bool = True


@dataclass
class StorageConfig:
    data_dir: str | None = None
    keep_raw: bool = True


@dataclass
class ExportConfig:
    enabled: bool = True
    dir: str = ""
    layout: str = "by_account"
    filename: str = "{date} {title}.md"
    bold_keywords: bool = True
    include_images: bool = False


@dataclass
class Account:
    nick: str = ""
    username: str = ""
    user_name: str = ""
    media_name: str = ""
    enabled: bool = True
    times: list[str] | None = None
    first_page_only: bool = True
    added_at: str = ""
    last_run_at: str | None = None
    last_seen_url: str | None = None

    def key(self) -> str:
        """用于 CLI 指代的稳定标识。"""
        return self.username or self.nick

    @classmethod
    def from_dict(cls, a: dict[str, Any]) -> "Account":
        times = a.get("times")
        return cls(
            nick=str(a.get("nick") or ""),
            username=str(a.get("username") or ""),
            user_name=str(a.get("user_name") or ""),
            media_name=str(a.get("media_name") or ""),
            enabled=bool(a.get("enabled", True)),
            times=[normalize_time(t) for t in times] if times else None,
            first_page_only=bool(a.get("first_page_only", True)),
            added_at=str(a.get("added_at") or ""),
            last_run_at=a.get("last_run_at"),
            last_seen_url=a.get("last_seen_url"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "nick": self.nick,
            "username": self.username,
            "user_name": self.user_name,
            "media_name": self.media_name,
            "enabled": self.enabled,
            "times": self.times,
            "first_page_only": self.first_page_only,
            "added_at": self.added_at,
            "last_run_at": self.last_run_at,
            "last_seen_url": self.last_seen_url,
        }


@dataclass
class Config:
    version: int = CURRENT_VERSION
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    export: ExportConfig = field(default_factory=ExportConfig)
    accounts: list[Account] = field(default_factory=list)
    path: Path | None = None

    # ---------- 加载 ----------

    @classmethod
    def load(cls, path: "Path | str | None" = None) -> "Config":
        # 容忍传字符串 —— 从环境变量读出来的路径天然是 str
        p = Path(path).expanduser() if path is not None else paths.resolve_config_path()
        if not p.exists():
            raise ConfigError(
                f"找不到配置文件: {p}",
                hint="先运行 `wxmp init` 生成一份初始配置",
            )
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(
                f"配置文件不是合法 JSON: {p}\n  {exc}",
                hint="检查是否有多余逗号或未闭合的引号",
            ) from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"配置文件顶层必须是对象: {p}")
        cfg = cls.from_dict(raw)
        cfg.path = p
        return cfg

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        prov_raw = raw.get("provider") or {}
        retry_raw = prov_raw.get("retry") or {}
        rate_raw = prov_raw.get("rate_limit") or {}
        sched_raw = raw.get("schedule") or {}
        stor_raw = raw.get("storage") or {}
        exp_raw = raw.get("export") or {}

        cfg = cls(
            version=int(raw.get("version", CURRENT_VERSION)),
            provider=ProviderConfig(
                name=str(prov_raw.get("name", "tikhub")),
                base_url=str(prov_raw.get("base_url", "https://api.tikhub.io")).rstrip("/"),
                token=str(prov_raw.get("token") or ""),
                timeout=int(prov_raw.get("timeout", 120)),
                retry=RetryConfig(
                    max=int(retry_raw.get("max", 3)),
                    backoff=[int(x) for x in (retry_raw.get("backoff") or [2, 5, 15])],
                ),
                qps=float(rate_raw.get("qps", 1.0)),
            ),
            schedule=ScheduleConfig(
                timezone=str(sched_raw.get("timezone", "Asia/Shanghai")),
                default_times=[
                    normalize_time(t) for t in (sched_raw.get("default_times") or ["09:00", "16:00"])
                ],
                catch_up_on_run=bool(sched_raw.get("catch_up_on_run", True)),
            ),
            storage=StorageConfig(
                data_dir=stor_raw.get("data_dir"),
                keep_raw=bool(stor_raw.get("keep_raw", True)),
            ),
            export=ExportConfig(
                enabled=bool(exp_raw.get("enabled", True)),
                dir=str(exp_raw.get("dir") or ""),
                layout=str(exp_raw.get("layout", "by_account")),
                filename=str(exp_raw.get("filename", "{date} {title}.md")),
                bold_keywords=bool(exp_raw.get("bold_keywords", True)),
                include_images=bool(exp_raw.get("include_images", False)),
            ),
            accounts=[Account.from_dict(a) for a in (raw.get("accounts") or [])],
        )
        cfg.validate()
        return cfg

    # ---------- 校验 ----------

    def validate(self) -> None:
        if self.version != CURRENT_VERSION:
            raise ConfigError(
                f"配置版本不支持: {self.version}（期望 {CURRENT_VERSION}）",
                hint="本工具暂不支持旧版配置迁移",
            )
        if not self.provider.base_url.startswith(("http://", "https://")):
            raise ConfigError(f"provider.base_url 不是合法 URL: {self.provider.base_url}")
        if self.provider.timeout <= 0:
            raise ConfigError("provider.timeout 必须为正数")
        # 时刻已在校验构造时归一化，这里检查列表本身非空
        if not self.schedule.default_times:
            raise ConfigError("schedule.default_times 不能为空")
        for a in self.accounts:
            if not a.username:
                raise ConfigError(
                    f"账号「{a.nick or '(未命名)'}」缺少 username",
                    hint="username 是拉取时唯一必需的字段",
                )

    def effective_token(self) -> str:
        """返回实际请求使用的 token，环境变量优先且不写回配置。"""
        return os.environ.get(ENV_TIKHUB_TOKEN, "").strip() or self.provider.token.strip()

    def token_source(self) -> str:
        """返回实际 token 的来源，用于体检输出。"""
        return "环境变量 TIKHUB_TOKEN" if os.environ.get(ENV_TIKHUB_TOKEN, "").strip() else "配置文件 provider.token"

    # ---------- 序列化 ----------

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "provider": {
                "name": self.provider.name,
                "base_url": self.provider.base_url,
                "token": self.provider.token,
                "timeout": self.provider.timeout,
                "retry": {
                    "max": self.provider.retry.max,
                    "backoff": self.provider.retry.backoff,
                },
                "rate_limit": {"qps": self.provider.qps},
            },
            "schedule": {
                "timezone": self.schedule.timezone,
                "default_times": self.schedule.default_times,
                "catch_up_on_run": self.schedule.catch_up_on_run,
            },
            "storage": {
                "data_dir": self.storage.data_dir,
                "keep_raw": self.storage.keep_raw,
            },
            "export": {
                "enabled": self.export.enabled,
                "dir": self.export.dir,
                "layout": self.export.layout,
                "filename": self.export.filename,
                "bold_keywords": self.export.bold_keywords,
                "include_images": self.export.include_images,
            },
            "accounts": [a.to_dict() for a in self.accounts],
        }

    def save(self, path: Path | None = None) -> Path:
        """原子写回。先写临时文件，fsync 后 rename 覆盖。"""
        target = path or self.path
        if target is None:
            raise ConfigError("未指定配置路径")
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"
        fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=".config-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, target)
        except BaseException:
            # 失败时别留下垃圾临时文件
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        self.path = target
        return target

    # ---------- 账号操作 ----------

    def find_account(self, key: str) -> Account | None:
        """按 username / user_name / nick 查找账号，大小写不敏感。"""
        want = key.strip().lower()
        for a in self.accounts:
            for candidate in (a.username, a.user_name, a.nick):
                if candidate and candidate.lower() == want:
                    return a
        return None

    def add_account(self, account: Account) -> None:
        """加入订阅，并防止同一个号被重复订阅。

        ⚠️ 必须按三个字段查重，实测踩到过：同一个公众号可以有两个合法标识 ——
        搜索结果给的是自定义微信号（mtlsnow），文章 URL 解析给的是官方 gh_ 号
        （gh_e2899e9a812e）。只比对 username 会漏判，导致同一个号被订阅两次。
        """
        for existing in self.accounts:
            same_username = (
                account.username
                and existing.username
                and account.username.lower() == existing.username.lower()
            )
            same_official = (
                account.user_name
                and existing.user_name
                and account.user_name.lower() == existing.user_name.lower()
            )
            same_nick = (
                account.nick
                and existing.nick
                and account.nick.strip() == existing.nick.strip()
            )
            if same_username or same_official or same_nick:
                raise ConfigError(
                    f"该公众号已订阅: {existing.nick or existing.username}"
                    f"（{existing.username}）",
                    hint="同一个号可能有多个标识（如自定义微信号与 gh_ 号），"
                    "如需修改请先 `wxmp remove`，或用 `wxmp enable/disable` 启停",
                )
        self.accounts.append(account)

    def remove_account(self, key: str) -> Account:
        acc = self.find_account(key)
        if acc is None:
            raise ConfigError(f"未找到订阅: {key}")
        self.accounts.remove(acc)
        return acc

    def effective_times(self, account: Account) -> list[str]:
        """账号级 times 覆盖全局 default_times；未设置则用全局。"""
        return account.times if account.times else self.schedule.default_times


def default_config() -> Config:
    """`wxmp init` 生成的新配置。

    export.dir 留空 —— 用户明确说了"先不管 Obsidian"，
    但 wxmp 必须提供输出 Markdown 正文的能力，由用户在任何时候填上目标目录。
    """
    return Config(
        provider=ProviderConfig(token=""),
        export=ExportConfig(enabled=False, dir=""),
        accounts=[],
    )
