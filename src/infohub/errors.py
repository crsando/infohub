"""Application-specific errors for infohub."""

from infohub_common.errors import (
    AuthError,
    BaseError,
    ConfigError,
    DisambiguationError,
    ExitCode,
    UpstreamError,
    UsageError,
)

__all__ = [
    "BaseError",
    "ExitCode",
    "ConfigError",
    "AuthError",
    "UpstreamError",
    "UsageError",
    "DisambiguationError",
    "InfohubError",
    "LLMError",
    "MemosError",
    "SourceError",
    "StoreError",
]


# Alias for backward compatibility
InfohubError = BaseError


class LLMError(UpstreamError):
    """LLM service error."""
    pass


class MemosError(UpstreamError):
    """Memos service error."""
    pass


class SourceError(BaseError):
    """Source database error."""
    pass


class StoreError(BaseError):
    """Local store error."""
    pass

