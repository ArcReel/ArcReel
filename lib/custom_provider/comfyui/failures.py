"""ComfyUI 通道的失败载体与失败码。

一个异常类型贯穿构造期与执行期：两段都在同一次生成里，落库的形状（稳定 ``code`` + 可序列化
``params``）与读侧的渲染路径也是同一条，分成两个类型只会让 worker 的异常联合与 ``call_failure``
的分类各多一格，而没有任何一处按类型分叉。

``code`` 必须已登记进 ``lib.task_failure`` 的 ``FAILURE_CODE_KEYS`` 与 ``lib.generation_result``
的 ``_TASK_FAILURE_ACTIONS``，否则 worker 编码失败原因时会降级成裸文本。
"""

from __future__ import annotations

from typing import Any

#: 素材上传失败：地址、凭据或磁盘的问题，重发同一请求可能就好了。
UPLOAD_FAILED = "comfyui_upload_failed"

#: ComfyUI 拒收这份 workflow（400，或 200 带非空 ``node_errors``）：缺模型、参数越界、节点不存在。
NODE_ERRORS = "comfyui_node_errors"

#: 执行到了终态，但 ``output`` 绑定的节点没有产出任何 ``type == "output"`` 的文件。
OUTPUT_MISSING = "comfyui_output_missing"

#: 产物在，但扩展名不是这个端点 ``media_type`` 该有的那一类。
OUTPUT_TYPE_MISMATCH = "comfyui_output_type_mismatch"

#: 参考图或首尾帧要删的读图节点，其级联触到了产物节点（构造期，见 ``request_builder``）。
IMAGE_DROP_UNSUPPORTED = "comfyui_image_drop_unsupported"


class ComfyuiError(RuntimeError):
    """ComfyUI 通道失败，携带可持久化、可本地化的稳定失败码。

    形状与声明式运行时的 ``DeclarativeRuntimeError`` 一致（``code`` + ``params``），失败原因的
    编码与渲染两侧因此不必为 ComfyUI 另写一条路径。
    """

    def __init__(self, code: str, **params: Any) -> None:
        self.code = code
        self.params: dict[str, Any] = params
        super().__init__(code)
