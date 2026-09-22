"""把任务执行异常编码为落库的 ``error_message``。

常规执行（``GenerationWorker._process_task``）与续跑（``VideoResumeRunner.run``）两条任务执行路径
捕获同一批异常，共用这份编码，避免同一处理漂移成两份。编码原语与读侧渲染见
``lib.generation.task_failure``。
"""

from __future__ import annotations

import logging
from typing import Any

from lib.backends.http_status_errors import ArtifactDownloadError, ProviderRejectedError
from lib.backends.image_backends.base import ImageCapabilityError
from lib.backends.video_backend_contract import VideoCapabilityError
from lib.config.resolver import ImageBucketCapabilityError, VideoBucketCapabilityError
from lib.custom_provider.comfyui.failures import ComfyuiError
from lib.custom_provider.declarative_backend import DeclarativeRuntimeError
from lib.generation.task_failure import encode_failure
from lib.infra.api_errors import ApiError
from lib.references.reference_compression import ReferencePayloadFloorError
from lib.script.reference_video.execution_checkpoint import ReferenceExecutionIdentityError
from lib.script.reference_video.request_projection import ReferenceProjectionBlockedError
from lib.script.script_editor import ScriptEditError
from lib.speech.narration_delivery import NarratedVideoDurationBlockedError

logger = logging.getLogger(__name__)


def encode_task_failure_message(exc: Exception) -> str:
    """ScriptEditError、上游确定性 4xx 拒绝与结构化执行拒绝走 code/params 结构化，其余异常沿用 str(exc)。

    落库只存机器码，本地化文案由读侧按 ``Accept-Language`` 渲染——worker 后台无 request
    上下文，在这里渲染会把任务的失败原因锁死成单一语言。
    """
    if isinstance(exc, ScriptEditError):
        # 编不出来时退到通用 script_edit_error，保住"是剧本编辑失败"这一层信息。
        return _try_encode_failure(exc.key, exc.params) or encode_failure("script_edit_error")
    if isinstance(exc, ApiError):
        return _try_encode_failure(exc.key, exc.params) or str(exc)
    if isinstance(exc, ProviderRejectedError):
        # 上游确定性 4xx：状态码与脱敏摘要各占一个参数。摘要是上游原文，不进译文模板——
        # 读侧按 error_params 里的独立字段原样展示，只有外层措辞随 Accept-Language 变。
        # 摘要缺席时只落状态码：拒绝本身仍要结构化，读侧才有本地化文案与 FIX_INPUT。
        params: dict[str, Any] = {"status": exc.response.status_code}
        if exc.provider_reason:
            params["provider_reason"] = exc.provider_reason
        return _try_encode_failure("provider_rejected", params) or str(exc)
    if isinstance(
        exc,
        ArtifactDownloadError
        | ImageCapabilityError
        | VideoCapabilityError
        | ReferencePayloadFloorError
        | ImageBucketCapabilityError
        | VideoBucketCapabilityError
        | ReferenceProjectionBlockedError
        | NarratedVideoDurationBlockedError
        | ReferenceExecutionIdentityError
        | DeclarativeRuntimeError
        | ComfyuiError,
    ):
        # 结构化执行拒绝没有通用兜底 code 可退，退回 str(exc)（即 code 本身）——
        # 非结构化文本在读侧原样透传，不会丢失原因。
        return _try_encode_failure(exc.code, exc.params) or str(exc)
    return str(exc)


def _try_encode_failure(code: str, params: dict[str, Any]) -> str | None:
    """结构化编码失败原因，编不出来返回 None 交调用方降级。

    编码异常绝不能打断 mark_task_failed，否则任务会卡死在 running。
    """
    try:
        return encode_failure(code, **params)
    except KeyError:
        # code 未登记进 FAILURE_CODE_KEYS——两份清单靠约定同步而非运行时校验。
        logger.warning("失败 code 未登记进 FAILURE_CODE_KEYS，降级为通用失败原因: code=%s", code)
    except (TypeError, ValueError, RecursionError):
        # params 无法 JSON 序列化（TypeError：default=str 也转不了的键类型；
        # ValueError：json.dumps 默认 check_circular 检出循环引用；
        # RecursionError：嵌套过深的容器，default=str 不接管容器本身）。
        logger.warning("失败 params 无法序列化，降级为通用失败原因: code=%s", code)
    return None
