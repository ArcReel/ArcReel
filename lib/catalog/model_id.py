"""供应商模型 ID 的路径段校验。

模型 ID 会作为 URL 路径（``/models/{model_id:path}``）的一部分出现，WHATWG URL 把 ``.``、``..`` 与它们的
百分号编码都当作点段规范化掉，``a/../b`` 会被解析成 ``b``。所以拒绝必须在服务端按 ``/`` 切分后判定，
只在前端编码挡不住。
"""

from __future__ import annotations


def has_dot_segment(model_id: str) -> bool:
    """模型 ID 按 ``/`` 切分后有任一段等于 ``.`` 或 ``..``。``a.b``、``gpt-4.1``、``x/y`` 不受影响。"""
    return any(segment in (".", "..") for segment in model_id.split("/"))
