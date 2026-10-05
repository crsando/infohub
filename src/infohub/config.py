"""Configuration for the root infohub orchestrator."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths
from .errors import ConfigError

CURRENT_VERSION = 1
DEFAULT_PROMPT_VERSION = "summary-v1"
DEFAULT_SYSTEM_PROMPT = (
    "你是中文资讯编辑。请严格依据原文，提炼 2-4 条简短要点。"
    "只陈述原文明确表达的事实、作者判断或预测，不补充常识，不猜测缺失信息。"
    "保留重要主体、数字、时间和条件；不要给出投资建议或风险评级。"
)
DEFAULT_USER_PROMPT_TEMPLATE = (
    "平台：{source}\n"
    "作者：{author}\n"
    "标题：{title}\n"
    "发布时间：{published_at}\n"
    "原文链接：{source_url}\n"
    "正文：\n{body}\n\n"
    "请输出简短中文摘要，只保留 2-4 条要点。"
)


@dataclass
class SourceConfig:
    name: str
    enabled: bool = True
    config: str = ""
    database: str = ""
    run_command: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, name: str, raw: dict[str, Any]) -> "SourceConfig":
        command = raw.get("run_command") or []
        if isinstance(command, str):
            command = command.split()
        return cls(
            name=name,
            enabled=bool(raw.get("enabled", True)),
            config=str(raw.get("config") or ""),
            database=str(raw.get("database") or ""),
            run_command=[str(value) for value in command],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "config": self.config,
            "database": self.database,
            "run_command": self.run_command,
        }


@dataclass
class LLMConfig:
    base_url: str = "http://192.168.5.17:8000/v1"
    model: str = "incoai/Qwen3.8-27B-Splash"
    api_key_env: str = "LLM_API_KEY"
    timeout_seconds: int = 180
    temperature: float = 0.1
    max_tokens: int = 700
    max_input_chars: int = 24000
    retries: int = 1
    prompt_version: str = DEFAULT_PROMPT_VERSION
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    user_prompt_template: str = DEFAULT_USER_PROMPT_TEMPLATE
    chat_template_kwargs: dict[str, Any] = field(
        default_factory=lambda: {"enable_thinking": False}
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "timeout_seconds": self.timeout_seconds,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "max_input_chars": self.max_input_chars,
            "retries": self.retries,
            "prompt_version": self.prompt_version,
            "system_prompt": self.system_prompt,
            "user_prompt_template": self.user_prompt_template,
            "chat_template_kwargs": self.chat_template_kwargs,
        }

    def effective_base_url(self) -> str:
        return os.environ.get("LLM_BASE_URL", "").strip().rstrip("/") or self.base_url.rstrip("/")


@dataclass
class MemosConfig:
    base_url: str = "http://127.0.0.1:5230"
    token_env: str = "MEMOS_TOKEN"
    visibility: str = "PRIVATE"
    endpoint: str = "/api/v1/memos"
    timeout_seconds: int = 30
    tags: list[str] = field(default_factory=lambda: ["infohub"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "token_env": self.token_env,
            "visibility": self.visibility,
            "endpoint": self.endpoint,
            "timeout_seconds": self.timeout_seconds,
            "tags": self.tags,
        }

    def effective_base_url(self) -> str:
        return os.environ.get("MEMOS_URL", "").strip().rstrip("/") or self.base_url.rstrip("/")


@dataclass
class PipelineConfig:
    run_sources: bool = True
    summarize_new_only: bool = True
    publish_to_memos: bool = True
    max_items_per_run: int = 100
    retry_failed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_sources": self.run_sources,
            "summarize_new_only": self.summarize_new_only,
            "publish_to_memos": self.publish_to_memos,
            "max_items_per_run": self.max_items_per_run,
            "retry_failed": self.retry_failed,
        }


@dataclass
class Config:
    version: int = CURRENT_VERSION
    sources: dict[str, SourceConfig] = field(default_factory=dict)
    llm: LLMConfig = field(default_factory=LLMConfig)
    memos: MemosConfig = field(default_factory=MemosConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    path: Path | None = None

    @classmethod
    def default(cls) -> "Config":
        return cls(
            sources={
                "wxmp": SourceConfig(
                    name="wxmp",
                    config="~/.config/wxmp/config.json",
                    database="~/.local/share/wxmp/wxmp.db",
                    run_command=["uv", "run", "--directory", "wxmp", "wxmp", "run"],
                ),
                "xnews": SourceConfig(
                    name="xnews",
                    config="~/.config/xnews/config.json",
                    database="~/.local/share/xnews/xnews.db",
                    run_command=["uv", "run", "--directory", "xnews", "xnews", "run"],
                ),
            }
        )

    @classmethod
    def load(cls, path: Path | str | None = None) -> "Config":
        target = Path(path).expanduser() if path is not None else paths.resolve_config_path()
        if not target.exists():
            raise ConfigError(f"找不到配置文件: {target}", "先运行 `infohub init`")
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigError(f"配置文件不是合法 JSON: {target}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError("配置文件顶层必须是对象")
        config = cls.from_dict(raw)
        config.path = target
        return config

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Config":
        source_raw = raw.get("sources") or {}
        config = cls(
            version=int(raw.get("version", CURRENT_VERSION)),
            sources={
                name: SourceConfig.from_dict(name, value or {})
                for name, value in source_raw.items()
                if isinstance(value, dict)
            },
            llm=_llm_from_dict(raw.get("llm") or {}),
            memos=_memos_from_dict(raw.get("memos") or {}),
            pipeline=_pipeline_from_dict(raw.get("pipeline") or {}),
        )
        config.validate()
        return config

    def validate(self) -> None:
        if self.version != CURRENT_VERSION:
            raise ConfigError(f"配置版本不支持: {self.version}（当前为 {CURRENT_VERSION}）")
        if not self.sources:
            raise ConfigError("sources 不能为空")
        for name, source in self.sources.items():
            if name not in {"wxmp", "xnews"}:
                raise ConfigError(f"不支持的数据源: {name}")
            if not source.database:
                raise ConfigError(f"数据源 {name} 缺少 database")
        if not self.llm.base_url.startswith(("http://", "https://")):
            raise ConfigError("llm.base_url 必须是 HTTP(S) URL")
        if self.llm.timeout_seconds <= 0 or self.llm.max_tokens <= 0:
            raise ConfigError("LLM timeout_seconds 和 max_tokens 必须为正数")
        if self.llm.max_input_chars <= 0 or self.llm.retries < 0:
            raise ConfigError("LLM max_input_chars 必须为正数，retries 不能为负数")
        if not self.llm.prompt_version.strip():
            raise ConfigError("llm.prompt_version 不能为空")
        if not self.llm.system_prompt.strip() or not self.llm.user_prompt_template.strip():
            raise ConfigError("llm.system_prompt 和 llm.user_prompt_template 不能为空")
        if not self.memos.base_url.startswith(("http://", "https://")):
            raise ConfigError("memos.base_url 必须是 HTTP(S) URL")
        if not self.memos.endpoint.startswith("/"):
            raise ConfigError("memos.endpoint 必须以 / 开头")
        if self.pipeline.max_items_per_run <= 0:
            raise ConfigError("pipeline.max_items_per_run 必须为正数")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "sources": {name: source.to_dict() for name, source in self.sources.items()},
            "llm": self.llm.to_dict(),
            "memos": self.memos.to_dict(),
            "pipeline": self.pipeline.to_dict(),
        }

    def save(self, path: Path | str | None = None) -> Path:
        target = Path(path).expanduser() if path is not None else (self.path or paths.resolve_config_path())
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.parent.chmod(0o700)
        except OSError:
            pass
        fd, temporary_name = tempfile.mkstemp(dir=str(target.parent), prefix=".config-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, target)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise
        self.path = target
        return target

    def llm_api_key(self) -> str:
        return os.environ.get(self.llm.api_key_env, "").strip()

    def memos_token(self) -> str:
        return os.environ.get(self.memos.token_env, "").strip()


def _llm_from_dict(raw: dict[str, Any]) -> LLMConfig:
    defaults = LLMConfig()
    kwargs = {
        "base_url": str(raw.get("base_url", defaults.base_url)).rstrip("/"),
        "model": str(raw.get("model", defaults.model)),
        "api_key_env": str(raw.get("api_key_env", defaults.api_key_env)),
        "timeout_seconds": int(raw.get("timeout_seconds", defaults.timeout_seconds)),
        "temperature": float(raw.get("temperature", defaults.temperature)),
        "max_tokens": int(raw.get("max_tokens", defaults.max_tokens)),
        "max_input_chars": int(raw.get("max_input_chars", defaults.max_input_chars)),
        "retries": int(raw.get("retries", defaults.retries)),
        "prompt_version": str(raw.get("prompt_version", defaults.prompt_version)),
        "system_prompt": str(raw.get("system_prompt", defaults.system_prompt)),
        "user_prompt_template": str(
            raw.get("user_prompt_template", defaults.user_prompt_template)
        ),
        "chat_template_kwargs": dict(raw.get("chat_template_kwargs") or defaults.chat_template_kwargs),
    }
    return LLMConfig(**kwargs)


def _memos_from_dict(raw: dict[str, Any]) -> MemosConfig:
    defaults = MemosConfig()
    return MemosConfig(
        base_url=str(raw.get("base_url", defaults.base_url)).rstrip("/"),
        token_env=str(raw.get("token_env", defaults.token_env)),
        visibility=str(raw.get("visibility", defaults.visibility)),
        endpoint=str(raw.get("endpoint", defaults.endpoint)),
        timeout_seconds=int(raw.get("timeout_seconds", defaults.timeout_seconds)),
        tags=[str(tag) for tag in (raw.get("tags") or defaults.tags)],
    )


def _pipeline_from_dict(raw: dict[str, Any]) -> PipelineConfig:
    defaults = PipelineConfig()
    return PipelineConfig(
        run_sources=bool(raw.get("run_sources", defaults.run_sources)),
        summarize_new_only=bool(raw.get("summarize_new_only", defaults.summarize_new_only)),
        publish_to_memos=bool(raw.get("publish_to_memos", defaults.publish_to_memos)),
        max_items_per_run=int(raw.get("max_items_per_run", defaults.max_items_per_run)),
        retry_failed=bool(raw.get("retry_failed", defaults.retry_failed)),
    )
