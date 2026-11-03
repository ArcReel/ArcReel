"""输入数量类能力的生效上限：条目与通道两侧取较小值，再由能力覆盖整维替换（docs/adr/0104）。

参考图上限这类维度有三种情况，用 :class:`InputLimit` 一种表示区分：

- **不支持**：这条输入路径走不通（图片的图生图不在生效能力里、视频的通道没有实现参考生视频），数量无从谈起；
- **未收录**：条目与通道都没有声明数量，执行时不裁剪，超出部分由上游拒绝；
- **上限 N**：至少一侧声明了数量，取较小的那个；``N`` 可以为 ``0``，即条目显式记录「不接受」。

``0`` 只表示「上限为 0」，不再兼作「不裁剪」。上限为 0 与不支持都不接受这类输入：候选不列、执行时拒绝，
编排层不把它们裁成 0 张后静默改走别的路径。

本模块只依赖标准库，图片与视频的能力求值共用，候选、执行时的校验、编排层的裁剪与设置页都读同一个求值结果。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class LimitState(StrEnum):
    UNSUPPORTED = "unsupported"
    UNRECORDED = "unrecorded"
    BOUNDED = "bounded"


@dataclass(frozen=True)
class InputLimit[N: (int, float)]:
    """一类输入的生效上限。``maximum`` 只在 ``BOUNDED`` 时有值。"""

    state: LimitState
    maximum: N | None = None

    def __post_init__(self) -> None:
        if (self.state == LimitState.BOUNDED) != (self.maximum is not None):
            raise ValueError(f"InputLimit {self.state} with maximum={self.maximum!r}")
        if self.maximum is not None and self.maximum < 0:
            raise ValueError(f"InputLimit maximum must be non-negative: {self.maximum!r}")

    @classmethod
    def unsupported(cls) -> InputLimit[N]:
        return cls(LimitState.UNSUPPORTED)

    @classmethod
    def unrecorded(cls) -> InputLimit[N]:
        return cls(LimitState.UNRECORDED)

    @classmethod
    def at_most(cls, maximum: N) -> InputLimit[N]:
        return cls(LimitState.BOUNDED, maximum)

    @classmethod
    def from_json(cls, value: N | None) -> InputLimit[N]:
        """:meth:`to_json` 的逆：``None`` 为未收录，其余为上限（``0`` 即不接受）。

        读已按接口形状存下的生效值（如视频能力 ``VideoCapabilities`` 的参考图与参考音频上限）时用它，
        不支持与上限为 0 在这一形状里同为 ``0``，读回后的判定（候选、执行时的校验、编排层的裁剪）相同。
        """
        return cls.unrecorded() if value is None else cls.at_most(value)

    @property
    def accepts_input(self) -> bool:
        """能不能带上这类输入：未收录，或上限大于 0。"""
        if self.state == LimitState.UNRECORDED:
            return True
        return self.maximum is not None and self.maximum > 0

    def clamp_bound(self) -> N | None:
        """编排层裁剪用的上限：正数上限原样给出；``None`` 表示不裁剪。

        不接受这类输入时同样不裁剪：输入原样交给执行时的校验拒绝，不裁成 0 张后静默改走别的路径。
        """
        if self.state == LimitState.BOUNDED and self.maximum is not None and self.maximum > 0:
            return self.maximum
        return None

    def to_json(self) -> N | int | None:
        """接口与界面上的形状：未收录为 ``None``，不接受（上限为 0 或不支持）为 ``0``，其余为上限。

        不支持与上限为 0 在界面上同显「不接受」；输入路径本身是否走得通由对应的维度表达（图片的图生图、
        视频的参考生视频）。
        """
        if self.state == LimitState.UNRECORDED:
            return None
        if self.state == LimitState.UNSUPPORTED:
            return 0
        return self.maximum


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
