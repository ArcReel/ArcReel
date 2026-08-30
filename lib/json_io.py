"""统一的 JSON 读写工具，提供严格加载与原子写入。"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    """严格加载 JSON。异常直接抛出，调用方按业务需要做 try/except。"""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


@contextmanager
def domain_error_on_value_error(
    factory: Callable[[ValueError], BaseException],
    *,
    extra_passthrough: tuple[type[BaseException], ...] = (),
) -> Iterator[None]:
    """把业务解析调用抛出的 ``ValueError`` 转换为调用方指定的领域错误。

    ``json.JSONDecodeError`` 是 ``ValueError`` 子类，须先于通用 ``ValueError`` 分支放行——
    它代表 JSON 文件本身损坏（语法错误），与业务级非法值（如路径穿越、非法项目名）语义不同，
    不能被误判为后者、进而返回一个误导性的 4xx。放行后由调用方外层自行收口。
    `extra_passthrough` 追加调用方需要按同一理由豁免的其它 ``ValueError`` 子类（如读取非
    UTF-8 剧本文件时的 ``UnicodeDecodeError``，或调用方另有专属分支处理的领域异常）。
    """
    try:
        yield
    except (json.JSONDecodeError, *extra_passthrough):
        raise
    except ValueError as exc:
        raise factory(exc) from exc


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
            with suppress(OSError):
                tmp_path.unlink()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """同目录 tempfile + ``os.replace`` 原子恢复文件字节。

    多文件补偿事务在后续写入失败时需要逐字恢复已经替换的旧文件；重新序列化 JSON 会改变
    空白与键序，无法证明恢复的是提交前的 durable state，因此回滚走字节快照。
    """
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=str(path.parent),
            prefix=".rollback.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            with suppress(OSError):
                tmp_path.unlink()
