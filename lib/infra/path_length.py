"""路径长度上限的判定：Windows 上超限的路径报「找不到」，不是「太长」。

Windows 未开启长路径支持时，单条路径（含结尾 NUL）不得超过 260 个字符，可用长度是 259。
超出后 ``CreateFileW`` 失败，系统给的是 ``ERROR_PATH_NOT_FOUND``，Python 映射成
``FileNotFoundError``：调用方看到的是「文件不存在」，很容易误判成素材缺失、字体没随包。

渲染要写的路径由项目数据根目录、渲染子目录与产物名拼成，长度不受调用方控制，
因此落盘前要先量一次长度，把「路径太长」与「文件确实不在」区分开。
"""

from __future__ import annotations

import os

WINDOWS_MAX_PATH = 260
"""Windows 的 ``MAX_PATH``：含结尾 NUL 的长度上限。"""

WINDOWS_MAX_PATH_LENGTH = WINDOWS_MAX_PATH - 1
"""Windows 上可用的路径长度（字符数）：``MAX_PATH`` 减去结尾 NUL。"""

POSIX_PATH_MAX = 4096
"""POSIX 的单条路径长度上限。"""

__all__ = [
    "POSIX_PATH_MAX",
    "WINDOWS_MAX_PATH",
    "WINDOWS_MAX_PATH_LENGTH",
    "path_length",
    "path_limit",
]


def path_limit() -> int:
    """当前平台可用的路径长度上限（字符数）。"""
    return WINDOWS_MAX_PATH_LENGTH if os.name == "nt" else POSIX_PATH_MAX


def path_length(path: str | os.PathLike[str]) -> int:
    """路径的字符长度；决定能不能落盘。"""
    return len(os.fspath(path))
