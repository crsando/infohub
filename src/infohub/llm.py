"""OpenAI-compatible Qwen client used by the summary stage."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import requests

from .config import LLMConfig
from .errors import LLMError


@dataclass
class SummaryResult:
    summary: str
    response: dict[str, Any]
    input_chars: int
    duration_ms: int


def build_payload(item: dict[str, Any], config: LLMConfig) -> dict[str, Any]:
    body = str(item.get("body") or "").strip()
    body = limit_text(body, config.max_input_chars)
    source = "微信公众号" if item.get("source") == "wxmp" else "X/Twitter"
    values = {
        "source": source,
        "author": item.get("author") or "未知",
        "title": item.get("title") or "（无标题）",
        "published_at": item.get("published_at") or "未知",
        "source_url": item.get("source_url") or "未知",
        "body": body,
    }
    try:
        user_content = config.user_prompt_template.format(**values)
    except (KeyError, ValueError) as exc:
        raise LLMError(f"llm.user_prompt_template 无效: {exc}") from exc
    return {
        "model": config.model,
        "messages": [
            {"role": "system", "content": config.system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
        "chat_template_kwargs": dict(config.chat_template_kwargs),
    }


def limit_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit < 100:
        return text[:limit]
    head = limit // 2
    tail = limit - head
    return text[:head] + "\n\n[正文过长，中间内容已截断]\n\n" + text[-tail:]


class LLMClient:
    def __init__(self, config: LLMConfig, api_key: str = "", session: requests.Session | None = None) -> None:
        self.config = config
        self.api_key = api_key
        self.session = session or requests.Session()
        self.base_url = config.effective_base_url()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def models(self) -> dict[str, Any]:
        try:
            response = self.session.get(
                f"{self.base_url}/models", headers=self._headers(), timeout=10
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise LLMError("LLM /models 返回格式不是对象")
            return payload
        except requests.RequestException as exc:
            raise LLMError(f"LLM /models 请求失败: {exc}") from exc
        except ValueError as exc:
            raise LLMError("LLM /models 返回不是合法 JSON") from exc

    def summarize(self, item: dict[str, Any]) -> SummaryResult:
        payload = build_payload(item, self.config)
        input_chars = len(payload["messages"][1]["content"])
        last_error: LLMError | None = None
        for attempt in range(self.config.retries + 1):
            started = time.monotonic()
            try:
                response = self.session.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers=self._headers(),
                    timeout=self.config.timeout_seconds,
                )
                if response.status_code in {429, 500, 502, 503, 504} and attempt < self.config.retries:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                response.raise_for_status()
                result = response.json()
                message = self._message(result)
                summary = str(message.get("content") or "").strip()
                if not summary:
                    raise LLMError(
                        "LLM 返回空摘要"
                        + self._usage_hint(result)
                    )
                return SummaryResult(
                    summary=summary,
                    response=result,
                    input_chars=input_chars,
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            except LLMError as exc:
                last_error = exc
            except requests.RequestException as exc:
                last_error = LLMError(f"LLM 摘要请求失败: {exc}")
            except ValueError as exc:
                last_error = LLMError(f"LLM 返回不是合法 JSON: {exc}")
            if attempt < self.config.retries:
                time.sleep(min(2 ** attempt, 8))
        raise last_error or LLMError("LLM 摘要失败")

    @staticmethod
    def _message(result: Any) -> dict[str, Any]:
        if not isinstance(result, dict):
            raise LLMError("LLM 响应不是对象")
        choices = result.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise LLMError("LLM 响应缺少 choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise LLMError("LLM 响应缺少 message")
        return message

    @staticmethod
    def _usage_hint(result: dict[str, Any]) -> str:
        usage = result.get("usage")
        if not usage:
            return ""
        try:
            return f"（usage={json.dumps(usage, ensure_ascii=False, separators=(',', ':'))}）"
        except (TypeError, ValueError):
            return ""
