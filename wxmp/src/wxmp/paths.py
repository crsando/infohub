"""路径解析 —— 全部走 POSIX / XDG 约定。

刻意不处理 Windows 路径：本工具的设计运行环境是 Linux / macOS / WSL。
在 Windows 盘上执行时，请从 WSL 里通过 /mnt/c/... 访问。

config.json 的定位规则（设计稿 §2.2）：
    1. $WXMP_CONFIG           —— 显式指定 config.json 的绝对路径
    2. $XDG_CONFIG_HOME/wxmp/config.json
    3. ~/.config/wxmp/config.json
"""

from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "wxmp"

# config.json 的环境变量名。整个工具只有这一个环境变量是"必需知道"的。
ENV_CONFIG = "WXMP_CONFIG"

# 允许用环境变量覆盖各个目录，方便测试与多实例部署。
ENV_CONFIG_DIR = "WXMP_CONFIG_DIR"
ENV_DATA_DIR = "WXMP_DATA_DIR"
ENV_CACHE_DIR = "WXMP_CACHE_DIR"


def config_dir() -> Path:
    """配置文件所在目录。"""
    override = os.environ.get(ENV_CONFIG_DIR)
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / APP_NAME


def default_config_path() -> Path:
    """未设置 WXMP_CONFIG 时的回落位置。"""
    return config_dir() / "config.json"


def resolve_config_path() -> Path:
    """按优先级解析 config.json 路径。

    注意：这里只负责"算出路径"，不判断文件是否存在 ——
    是否存在由 config.py 负责，因为 `wxmp init` 需要往这个不存在的路径写。
    """
    override = os.environ.get(ENV_CONFIG)
    if override:
        return Path(override).expanduser()
    return default_config_path()


def data_dir() -> Path:
    """数据目录：SQLite、raw JSON、日志都在这里。"""
    override = os.environ.get(ENV_DATA_DIR)
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / APP_NAME


def cache_dir() -> Path:
    override = os.environ.get(ENV_CACHE_DIR)
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / APP_NAME


def db_path() -> Path:
    return data_dir() / "wxmp.db"


def raw_dir() -> Path:
    """原始响应存档根目录。

    永久保留。上游接口一变，这份存档就是无损重建全部衍生数据的地基。
    """
    return data_dir() / "raw"


def log_dir() -> Path:
    return data_dir() / "logs"


def ensure_dirs() -> None:
    """创建运行所需的全部目录（幂等）。"""
    for p in (data_dir(), raw_dir(), log_dir()):
        p.mkdir(parents=True, exist_ok=True)
