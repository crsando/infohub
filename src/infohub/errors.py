from __future__ import annotations


class InfohubError(Exception):
    """Expected operational error with a user-facing Chinese message."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def render(self) -> str:
        return f"{self.message}\n提示：{self.hint}" if self.hint else self.message


class ConfigError(InfohubError):
    pass


class SourceError(InfohubError):
    pass


class LLMError(InfohubError):
    pass


class MemosError(InfohubError):
    pass
