"""输入数量类能力的生效上限：条目与通道两侧取较小值，再由能力覆盖整维替换（docs/adr/0104）。

参考图上限这类维度有三种情况，用 :class:`InputLimit` 一种表示区分：

- **不支持**：这条输入路径走不通（图片的图生图不在生效能力里、视频的通道没有实现参考生视频），数量无从谈起；
- **未收录**：条目与通道都没有声明数量，执行时不裁剪，超出部分由上游拒绝；
- **上限 N**：至少一侧声明了数量，取较小的那个；``N`` 可以为 ``0``，即条目显式记录「不接受」。

``0`` 只表示「上限为 0」，不表示「不裁剪」。上限为 0 与不支持都不接受这类输入：候选不列、执行时拒绝，
编排层不把它们裁成 0 张后静默改走别的路径。

图片与视频的能力求值共用，候选、执行时的校验、编排层的数量检查与设置页都读同一个求值结果。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from lib.capabilities.limits import InputLimit as InputLimit
from lib.capabilities.limits import LimitState as LimitState

_reference_sources: ContextVar[Mapping[Path, Path] | None] = ContextVar("reference_image_sources", default=None)


@contextmanager
def reference_image_sources(paths: Mapping[Path, Path]):
    """将本次调用的暂存／冻结／压缩路径还原为作者认识的来源名，多个并发请求各自隔离。"""
    token = _reference_sources.set(paths)
    try:
        yield
    finally:
        _reference_sources.reset(token)


def reference_image_name(path: Path) -> str:
    return (_reference_sources.get() or {}).get(path, path).name


def excess_image_names(limit: int, count: int, paths: Iterable[str | Path] = ()) -> str:
    """供应商请求没有资产名时优先报告文件名，否则给出从 1 开始的图片序号。"""
    names = [reference_image_name(Path(path)) for path in paths]
    return ", ".join(names[i] if i < len(names) and names[i] else f"#{i + 1}" for i in range(limit, count))


def effective_input_limit[N: (int, float)](
    *, path_supported: bool, declared: Iterable[N | None], override: N | None = None
) -> InputLimit[N]:
    """一类输入的生效上限。

    - ``path_supported`` 为假：不支持，能力覆盖也打不开（路径本身由它自己的维度决定）；
    - ``override`` 不为 ``None``：能力覆盖整维替换两侧求值的结果；
    - 否则取 ``declared``（条目值、通道值，``None`` 表示这一侧没有声明）中较小的那个，都没有声明时为未收录。
    """
    if not path_supported:
        return InputLimit.unsupported()
    if override is not None:
        return InputLimit.at_most(override)
    values = [value for value in declared if value is not None]
    if not values:
        return InputLimit.unrecorded()
    return InputLimit.at_most(min(values))
