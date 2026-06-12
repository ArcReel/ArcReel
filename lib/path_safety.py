"""路径安全工具：项目目录内相对路径的防穿越检查。"""

from __future__ import annotations

from pathlib import Path


def safe_resolve(base: Path, rel_path: str | None) -> Path | None:
    """解析 base 内的相对路径，返回绝对路径；越界/脏数据/不是已存在的文件时返回 None（防路径穿越）。"""
    if not rel_path:
        return None
    try:
        full = (base / rel_path).resolve()
        if full.is_relative_to(base.resolve()) and full.is_file():
            return full
    except (OSError, ValueError, TypeError):
        # TypeError：rel_path 来自 project.json 原始字段，脏数据（dict/int）按「不存在」处理
        return None
    return None


def safe_exists(base: Path, rel_path: str) -> bool:
    """rel_path 是否为 base 内的合法相对路径且文件存在（防路径穿越）。"""
    return safe_resolve(base, rel_path) is not None
