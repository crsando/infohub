"""Base exception classes and exit codes shared across infohub projects."""

from __future__ import annotations


class ExitCode:
    """Standard CLI exit codes."""
    OK = 0
    GENERIC = 1
    USAGE = 2
    CONFIG = 3
    AUTH = 4
    UPSTREAM = 5
    NEEDS_DISAMBIGUATION = 6


class BaseError(Exception):
    """Base class for all expected operational errors.

    Unexpected bugs should not inherit from this class, allowing them
    to raise naturally with a full stack trace for debugging.
    """
    exit_code: int = ExitCode.GENERIC

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def render(self) -> str:
        """Format the error for CLI display."""
        if self.hint:
            return f"错误: {self.message}\n建议: {self.hint}"
        return f"错误: {self.message}"


class ConfigError(BaseError):
    exit_code = ExitCode.CONFIG


class AuthError(BaseError):
    exit_code = ExitCode.AUTH


class UpstreamError(BaseError):
    exit_code = ExitCode.UPSTREAM


class UsageError(BaseError):
    exit_code = ExitCode.USAGE


class DisambiguationError(BaseError):
    exit_code = ExitCode.NEEDS_DISAMBIGUATION
