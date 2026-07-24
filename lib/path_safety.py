"""路径安全工具：项目内「路径必须落在某个基准目录之内」的唯一校验入口。

实现刻意使用 ``os.path.realpath`` + ``startswith`` 前缀比较，而不是 ``Path.resolve()``
配合 ``relative_to()`` / ``is_relative_to()``：两者防御强度等价（``realpath`` 同样展开
symlink），但前缀比较是 CodeQL ``py/path-injection`` 能识别的 sanitizer 收敛模式，后者
不被识别、会持续产生存量告警。

返回值一律由 ``realpath`` 的输出构造，而非调用方传入的原始路径，避免污染值继续外流。
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "PathTraversalError",
    "safe_exists",
    "safe_join",
    "safe_resolve",
    "try_safe_join",
]


class PathTraversalError(ValueError):
    """解析后的路径逃出了基准目录。

    继承 ``ValueError``，既能被 ``except ValueError`` 的既有调用点接住，也允许需要区分
    「越界」与「其它非法入参」的调用点精确捕获。
    """


def _realpath(value: str | os.PathLike[str]) -> str:
    return os.path.realpath(os.fspath(value))


def safe_join(
    base: str | os.PathLike[str],
    *parts: str | os.PathLike[str],
    allow_base: bool = False,
    must_exist: bool = False,
    require_file: bool = False,
) -> Path:
    """把不可信的 ``parts`` 拼到 ``base`` 下，校验未越界后返回规范化的绝对路径。

    ``parts`` 中出现绝对路径时按 ``os.path.join`` 语义丢弃前缀，随后仍要通过包含校验，
    因此「传入绝对路径」等价于「校验该绝对路径是否在 base 内」。

    Args:
        base: 基准目录，结果必须落在其内。
        parts: 待拼接的路径片段，通常来自用户输入或磁盘上的不可信数据。
        allow_base: 拼接结果恰等于 ``base`` 本身时是否算通过。默认 False。
        must_exist: 为 True 时结果必须已存在（文件或目录），否则抛 ``FileNotFoundError``。
        require_file: 为 True 时结果必须是已存在的**文件**，否则抛 ``FileNotFoundError``。

    Raises:
        PathTraversalError: 解析结果不在 ``base`` 内。
        FileNotFoundError: ``must_exist`` / ``require_file`` 校验未通过。
        TypeError: ``parts`` 含非路径类型（如 project.json 里的脏数据）。
    """
    base_real = _realpath(base)
    joined = os.path.join(base_real, *(os.fspath(part) for part in parts))
    candidate_real = os.path.realpath(joined)

    if candidate_real == base_real:
        contained = allow_base
    else:
        contained = candidate_real.startswith(base_real + os.sep)
    if not contained:
        raise PathTraversalError(f"路径越界：{candidate_real!r} 不在 {base_real!r} 内")

    # is_file/exists 直接查 candidate_real（携带 sanitizer barrier 的字符串），
    # 而不是先包进 Path 再查：Path() 包装会打断 CodeQL 对该 barrier 的识别，
    # 导致下面两次文件系统探测被当成未经校验的 py/path-injection sink。
    if require_file and not os.path.isfile(candidate_real):
        raise FileNotFoundError(candidate_real)
    if must_exist and not os.path.exists(candidate_real):
        raise FileNotFoundError(candidate_real)
    return Path(candidate_real)


def try_safe_join(
    base: str | os.PathLike[str],
    *parts: str | os.PathLike[str],
    allow_base: bool = False,
    must_exist: bool = False,
    require_file: bool = False,
) -> Path | None:
    """``safe_join`` 的静默版本：越界 / 不存在 / 脏数据一律返回 None。

    供「拿不到就跳过」而非「拒绝请求」的调用点使用（校验汇总、候选路径遍历等）。
    """
    try:
        return safe_join(
            base,
            *parts,
            allow_base=allow_base,
            must_exist=must_exist,
            require_file=require_file,
        )
    except (OSError, ValueError, TypeError):
        # TypeError：片段来自 project.json 原始字段，脏数据（dict/int）按「不存在」处理
        return None


def safe_resolve(base: Path, rel_path: str | None) -> Path | None:
    """解析 base 内的相对路径，返回绝对路径；越界/脏数据/不是已存在的文件时返回 None。"""
    if not rel_path:
        return None
    return try_safe_join(base, rel_path, require_file=True)


def safe_exists(base: Path, rel_path: str) -> bool:
    """rel_path 是否为 base 内的合法相对路径且文件存在（防路径穿越）。"""
    return safe_resolve(base, rel_path) is not None
