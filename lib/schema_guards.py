"""持久化 schema 的运行期形状判定。

数据类的字段标注描述的是**期望** schema；实例既由本仓库的类型化代码构造，也由
``from_dict`` 之类的入口从磁盘 JSON 重建，故构造期仍须校验真实形状。这些判定函数
以 ``object`` 收参，把「标注表达期望、判定负责实际」的分工落到类型上，构造期校验
因此不会退化成对自身标注的同义反复。
"""

from collections.abc import Mapping
from math import isfinite

__all__ = [
    "is_bool",
    "is_finite_number",
    "is_int",
    "is_mapping",
    "is_shape",
    "is_str",
]


def is_int(value: object, *, minimum: int | None = None) -> bool:
    """严格整数判定：``bool`` 是 ``int`` 的子类，此处按非整数处理。"""
    if not isinstance(value, int) or isinstance(value, bool):
        return False
    return minimum is None or value >= minimum


def is_finite_number(value: object) -> bool:
    """有限实数判定，同样排除 ``bool``。"""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    return isfinite(value)


def is_bool(value: object) -> bool:
    return isinstance(value, bool)


def is_str(value: object) -> bool:
    return isinstance(value, str)


def is_mapping(value: object) -> bool:
    return isinstance(value, Mapping)


def is_shape(value: object, expected: type | tuple[type, ...]) -> bool:
    """任意类型的形状判定，供无专用谓词的持久化字段使用。"""
    return isinstance(value, expected)
