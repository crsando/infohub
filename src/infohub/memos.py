"""Small Memos API adapter isolated from the rest of the pipeline."""

from __future__ import annotations

from typing import Any

import requests

from .config import MemosConfig
from .errors import MemosError


class MemosClient:
    def __init__(self, config: MemosConfig, token: str, session: requests.Session | None = None) -> None:
        if not token:
            raise MemosError("未配置 MEMOS_TOKEN", "设置 MEMOS_TOKEN 后再发布到 Memos")
        self.config = config
        self.token = token
        self.session = session or requests.Session()
        self.base_url = config.effective_base_url()
        self.endpoint = config.endpoint if config.endpoint.startswith("/") else f"/{config.endpoint}"

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

    def check(self) -> dict[str, Any]:
        try:
            response = self.session.get(
                f"{self.base_url}{self.endpoint}",
                params={"pageSize": 1},
                headers=self._headers(),
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {"data": payload}
        except requests.RequestException as exc:
            raise MemosError(f"Memos 连通性检查失败: {exc}") from exc
        except ValueError as exc:
            raise MemosError("Memos 返回不是合法 JSON") from exc

    def create(self, content: str) -> dict[str, Any]:
        try:
            response = self.session.post(
                f"{self.base_url}{self.endpoint}",
                json={"content": content, "visibility": self.config.visibility},
                headers=self._headers(),
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise MemosError("Memos 创建响应不是对象")
            return payload
        except MemosError:
            raise
        except requests.RequestException as exc:
            raise MemosError(f"Memos 创建 memo 失败: {exc}") from exc
        except ValueError as exc:
            raise MemosError("Memos 创建响应不是合法 JSON") from exc


def remote_ids(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Accept the name/uid shapes returned by different Memos versions."""
    nested = payload.get("memo") if isinstance(payload.get("memo"), dict) else payload
    return (
        str(nested.get("name")) if nested.get("name") is not None else None,
        str(nested.get("uid")) if nested.get("uid") is not None else None,
    )
