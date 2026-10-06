"""xnews exceptions and CLI exit codes."""

from __future__ import annotations

from infohub_common.errors import BaseError, ExitCode


class XnewsError(BaseError):
    """所有预期内错误的基类。"""

    def render(self) -> str:
        result = f"错误: {self.message}"
        if self.hint:
            result += f"\n建议: {self.hint}"
        return result


class ConfigError(XnewsError):
    exit_code = ExitCode.CONFIG


class AuthError(XnewsError):
    exit_code = ExitCode.AUTH


class UpstreamError(XnewsError):
    exit_code = ExitCode.UPSTREAM


class UsageError(XnewsError):
    exit_code = ExitCode.USAGE


class DisambiguationError(XnewsError):
    exit_code = ExitCode.NEEDS_DISAMBIGUATION

    def __init__(self, keyword: str, candidates: list[object]) -> None:
        self.keyword = keyword
        self.candidates = candidates
        super().__init__(
            f"关键词「{keyword}」命中 {len(candidates)} 个候选，需要人工选择",
            hint="使用 --pick N 指定候选，或提供明确的 @username/rest_id",
        )
