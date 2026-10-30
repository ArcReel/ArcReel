"""把消息文本里的项目目录绝对路径改写为项目内相对路径。

面向外部宿主的消息不应带出服务端文件系统布局；项目内相对路径对内嵌 Agent、Web 端与
远程 Agent 同样能定位文件。只改写项目目录前缀本身，消息的其余正文原样保留。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path


def project_path_spellings(project_dir: Path) -> tuple[str, ...]:
    """项目目录在消息里可能出现的绝对路径写法：原样与解析符号链接后的（macOS 的 /tmp → /private/tmp），长者在前。"""
    return tuple(sorted({str(project_dir), str(project_dir.resolve())}, key=len, reverse=True))


def relativize_project_paths(text: str, spellings: Iterable[str]) -> str:
    """把消息里的项目目录绝对路径改写为项目内相对路径；项目目录本身写作 ``.``。"""
    for root in spellings:
        text = re.sub(re.escape(root) + r"(?:([\\/])|(?![\w.\-]))", lambda m: "" if m.group(1) else ".", text)
    return text


__all__ = ["project_path_spellings", "relativize_project_paths"]
