"""路径安全工具：项目目录内相对路径的防穿越检查。"""

from __future__ import annotations

from pathlib import Path


def safe_exists(base: Path, rel_path: str) -> bool:
    """rel_path 是否为 base 内的合法相对路径且为既存文件（防路径穿越）。

    调用方（剧本指针、资产 sheet 指针、产品原图列表）语义上均指向文件，
    目录路径属于脏数据，按「不存在」处理。
    """
    if not rel_path:
        return False
    try:
        full = (base / rel_path).resolve()
        return full.is_relative_to(base.resolve()) and full.is_file()
    except (OSError, ValueError, TypeError):
        # TypeError：rel_path 来自 project.json 原始字段，脏数据（dict/int）按「不存在」处理
        return False
