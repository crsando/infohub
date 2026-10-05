"""xnews exceptions and CLI exit codes."""

from __future__ import annotations


class ExitCode:
    OK = 0
    GENERIC = 1
    USAGE = 2
    CONFIG = 3
    AUTH = 4
    UPSTREAM = 5
    NEEDS_DISAMBIGUATION = 6


class XnewsError(Exception):
    exit_code = ExitCode.GENERIC

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

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
