"""统一异常与退出码。

退出码是 CLI 对外契约的一部分（设计稿 §5.3），脚本化调用时要能靠它判断失败原因。
"""

from __future__ import annotations


class ExitCode:
    OK = 0
    GENERIC = 1
    USAGE = 2
    CONFIG = 3
    AUTH = 4
    UPSTREAM = 5
    NEEDS_DISAMBIGUATION = 6


class WxmpError(Exception):
    """所有预期内错误的基类。

    `exit_code` 决定进程退出码；`hint` 是给人看的一句话建议。
    非预期异常（真 bug）不继承本类，让它照常抛栈，便于排查。
    """

    exit_code: int = ExitCode.GENERIC

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def render(self) -> str:
        out = f"错误: {self.message}"
        if self.hint:
            out += f"\n建议: {self.hint}"
        return out


class ConfigError(WxmpError):
    exit_code = ExitCode.CONFIG


class AuthError(WxmpError):
    exit_code = ExitCode.AUTH


class UpstreamError(WxmpError):
    exit_code = ExitCode.UPSTREAM


class UsageError(WxmpError):
    exit_code = ExitCode.USAGE


class DisambiguationError(WxmpError):
    """搜索命中多个候选，且当前是非交互环境，无法替用户做决定。

    刻意不猜（设计稿 §5.2）：猜错会静默订阅到李鬼号，比直接失败糟糕得多。
    """

    exit_code = ExitCode.NEEDS_DISAMBIGUATION

    def __init__(self, keyword: str, candidates: list) -> None:
        self.keyword = keyword
        self.candidates = candidates
        super().__init__(
            f"关键词「{keyword}」命中 {len(candidates)} 个候选，需要人工消歧",
            hint="请加上 --pick N 指定第 N 个候选，或先用 wxmp search 查看候选列表",
        )
