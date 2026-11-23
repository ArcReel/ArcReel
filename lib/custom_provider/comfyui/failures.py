"""ComfyUI 通道的失败载体与失败码。

一个异常类型贯穿构造期与执行期：两段都在同一次生成里，落库的形状（稳定 ``code`` + 可序列化
``params``）与读侧的渲染路径也是同一条，分成两个类型只会让 worker 的异常联合与 ``call_failure``
的分类各多一格，而没有任何一处按类型分叉。

``code`` 必须已登记进 ``lib.generation.task_failure`` 的 ``FAILURE_CODE_KEYS`` 与 ``lib.generation.generation_result``
的 ``_TASK_FAILURE_ACTIONS``，否则 worker 编码失败原因时会降级成裸文本。
"""

from __future__ import annotations

from typing import Any

UPLOAD_FAILED = "comfyui_upload_failed"

NODE_ERRORS = "comfyui_node_errors"

JOB_LOST = "comfyui_job_lost"

EXECUTION_ERROR = "comfyui_execution_error"

INTERRUPTED = "comfyui_interrupted"

OUTPUT_MISSING = "comfyui_output_missing"

OUTPUT_TYPE_MISMATCH = "comfyui_output_type_mismatch"

OUTPUT_CONTAINER_MISMATCH = "comfyui_output_container_mismatch"

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
