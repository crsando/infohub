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

from infohub_common.paths import (
    cache_dir as _cache_dir,
    config_dir as _config_dir,
    data_dir as _data_dir,
    resolve_config_path as _resolve_config_path,
)

APP_NAME = "wxmp"
ENV_CONFIG = "WXMP_CONFIG"
ENV_CONFIG_DIR = "WXMP_CONFIG_DIR"
ENV_DATA_DIR = "WXMP_DATA_DIR"
ENV_CACHE_DIR = "WXMP_CACHE_DIR"


def config_dir() -> Path:
    """配置文件所在目录。"""
    return _config_dir(APP_NAME, ENV_CONFIG_DIR)


def default_config_path() -> Path:
    """未设置 WXMP_CONFIG 时的回落位置。"""
    return config_dir() / "config.json"


def resolve_config_path() -> Path:
    """按优先级解析 config.json 路径。

    注意：这里只负责"算出路径"，不判断文件是否存在 ——
    是否存在由 config.py 负责，因为 `wxmp init` 需要往这个不存在的路径写。
    """
    return _resolve_config_path(APP_NAME, ENV_CONFIG)


def data_dir(configured: str | None = None) -> Path:
    """数据目录：SQLite、raw JSON、日志都在这里。"""
    override = os.environ.get(ENV_DATA_DIR)
    if override:
        return Path(override).expanduser()
    if configured:
        return Path(configured).expanduser()
    return _data_dir(APP_NAME, None)


def cache_dir() -> Path:
    return _cache_dir(APP_NAME, ENV_CACHE_DIR)


def db_path(configured: str | None = None) -> Path:
    return data_dir(configured) / "wxmp.db"


def raw_dir(configured: str | None = None) -> Path:
    """原始响应存档根目录。

    永久保留。上游接口一变，这份存档就是无损重建全部衍生数据的地基。
    """
    return data_dir(configured) / "raw"


def log_dir(configured: str | None = None) -> Path:
    return data_dir(configured) / "logs"


def ensure_dirs(configured: str | None = None) -> None:
    """创建运行所需的全部目录（幂等）。"""
    for p in (data_dir(configured), raw_dir(configured), log_dir(configured)):
        p.mkdir(parents=True, exist_ok=True)
