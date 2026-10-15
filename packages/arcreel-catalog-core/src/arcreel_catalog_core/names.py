"""模型名的归一化（见 docs/adr/0105）。"""

from __future__ import annotations


def normalize_model_name(name: str) -> str:
    """忽略大小写，去掉最后一个 ``/`` 及之前的命名空间，点号视同连字符。

    不去掉日期后缀，也不去掉 ``:free``、``-thinking``、``-preview`` 一类后缀：带日期的快照是否
    属于同一个模型，由目录数据里的模型别名表达。
    """
    return name.rpartition("/")[2].lower().replace(".", "-")
