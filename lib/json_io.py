"""统一的 JSON 读写工具，提供严格加载与原子写入。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    """严格加载 JSON。异常直接抛出，调用方按业务需要做 try/except。"""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_json_or_none(path: Path) -> Any | None:
    """容错加载 JSON：读取或解析失败返回 None。"""
    try:
        return load_json(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def atomic_write_json(path: Path, data: Any) -> None:
    """同目录 tempfile + os.replace 原子写入 JSON。"""
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=".project.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            json.dump(data, tmp, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass
