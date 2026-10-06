"""Simplified configuration using Pydantic for validation and serialization."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from infohub_common.errors import ConfigError
from infohub_common.paths import resolve_config_path
from infohub_common.store import write_json_atomic

CURRENT_VERSION = 1
APP_NAME = "infohub"
ENV_CONFIG = "INFOHUB_CONFIG"

# Prompt templates stored as code constants, not in JSON config
DEFAULT_PROMPTS = {
    "summary-v1": {
        "system": (
            "你是中文资讯编辑。请严格依据原文，提炼 2-4 条简短要点。"
            "只陈述原文明确表达的事实、作者判断或预测，不补充常识，不猜测缺失信息。"
            "保留重要主体、数字、时间和条件；不要给出投资建议或风险评级。"
        ),
        "user": (
            "平台：{source}\n"
            "作者：{author}\n"
            "标题：{title}\n"
            "发布时间：{published_at}\n"
            "原文链接：{source_url}\n"
            "正文：\n{body}\n\n"
            "请输出简短中文摘要，只保留 2-4 条要点。"
        ),
    }
}


class SourceConfig(BaseModel):
    enabled: bool = True
    config: str = ""
    database: str = ""
    run_command: list[str] = Field(default_factory=list)


class LLMConfig(BaseModel):
    base_url: str = "http://192.168.5.17:8000/v1"
    model: str = "incoai/Qwen3.8-27B-Splash"
    api_key_env: str = "LLM_API_KEY"
    timeout_seconds: int = 180
    temperature: float = 0.1
    max_tokens: int = 700
    max_input_chars: int = 24000
    retries: int = 1
    prompt_version: str = "summary-v1"
    extra_params: dict[str, Any] = Field(
        default_factory=lambda: {"chat_template_kwargs": {"enable_thinking": False}}
    )

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url must be HTTP(S) URL")
        return v

    @field_validator("timeout_seconds", "max_tokens", "max_input_chars")
    @classmethod
    def validate_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be positive")
        return v

    @field_validator("retries")
    @classmethod
    def validate_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("retries cannot be negative")
        return v

    def effective_base_url(self) -> str:
        """Allow LLM_BASE_URL to override config."""
        override = os.environ.get("LLM_BASE_URL", "").strip().rstrip("/")
        return override or self.base_url

    def get_system_prompt(self) -> str:
        """Get system prompt for current prompt_version."""
        return DEFAULT_PROMPTS.get(self.prompt_version, {}).get(
            "system", DEFAULT_PROMPTS["summary-v1"]["system"]
        )

    def get_user_template(self) -> str:
        """Get user template for current prompt_version."""
        return DEFAULT_PROMPTS.get(self.prompt_version, {}).get(
            "user", DEFAULT_PROMPTS["summary-v1"]["user"]
        )


class MemosConfig(BaseModel):
    base_url: str = "http://127.0.0.1:5230"
    token_env: str = "MEMOS_TOKEN"
    visibility: str = "PRIVATE"
    endpoint: str = "/api/v1/memos"
    timeout_seconds: int = 30
    tags: list[str] = Field(default_factory=lambda: ["infohub"])

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url must be HTTP(S) URL")
        return v

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, v: str) -> str:
        if not v.startswith("/"):
            raise ValueError("endpoint must start with /")
        return v

    def effective_base_url(self) -> str:
        """Allow MEMOS_URL to override config."""
        override = os.environ.get("MEMOS_URL", "").strip().rstrip("/")
        return override or self.base_url


class PipelineConfig(BaseModel):
    run_sources: bool = True
    summarize_new_only: bool = True
    publish_to_memos: bool = True
    max_items_per_run: int = 100
    retry_failed: bool = True

    @field_validator("max_items_per_run")
    @classmethod
    def validate_max_items(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_items_per_run must be positive")
        return v


class Config(BaseModel):
    version: int = CURRENT_VERSION
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    memos: MemosConfig = Field(default_factory=MemosConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: int) -> int:
        if v != CURRENT_VERSION:
            raise ValueError(f"Unsupported version {v}, expected {CURRENT_VERSION}")
        return v

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, v: dict[str, SourceConfig]) -> dict[str, SourceConfig]:
        if not v:
            raise ValueError("sources cannot be empty")
        for name in v:
            if name not in {"wxmp", "xnews"}:
                raise ValueError(f"Unsupported source: {name}")
            if not v[name].database:
                raise ValueError(f"Source {name} missing database")
        return v

    @classmethod
    def default(cls) -> Config:
        """Create default configuration."""
        return cls(
            sources={
                "wxmp": SourceConfig(
                    config="~/.config/wxmp/config.json",
                    database="~/.local/share/wxmp/wxmp.db",
                    run_command=["uv", "run", "--directory", "wxmp", "wxmp", "run"],
                ),
                "xnews": SourceConfig(
                    config="~/.config/xnews/config.json",
                    database="~/.local/share/xnews/xnews.db",
                    run_command=["uv", "run", "--directory", "xnews", "xnews", "run"],
                ),
            }
        )

    @classmethod
    def load(cls, path: Path | str | None = None) -> Config:
        """Load configuration from file."""
        target = Path(path).expanduser() if path else resolve_config_path(APP_NAME, ENV_CONFIG)
        if not target.exists():
            raise ConfigError(f"找不到配置文件: {target}", "先运行 `infohub init`")

        try:
            import json
            data = json.loads(target.read_text(encoding="utf-8"))
            config = cls.model_validate(data)
            return config
        except Exception as exc:
            raise ConfigError(f"配置文件加载失败: {exc}") from exc

    def save(self, path: Path | str | None = None) -> Path:
        """Save configuration to file."""
        target = Path(path).expanduser() if path else resolve_config_path(APP_NAME, ENV_CONFIG)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.parent.chmod(0o700)
        except OSError:
            pass

        write_json_atomic(target, self.model_dump(mode="json"), mode=0o600)
        return target

    def llm_api_key(self) -> str:
        """Get LLM API key from environment."""
        return os.environ.get(self.llm.api_key_env, "").strip()

    def memos_token(self) -> str:
        """Get Memos token from environment."""
        return os.environ.get(self.memos.token_env, "").strip()
