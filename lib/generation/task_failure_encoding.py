"""把任务执行异常编码为落库的 ``error_message``。

常规执行（``GenerationWorker._process_task``）与续跑（``VideoResumeRunner.run``）两条任务执行路径
捕获同一批异常，共用这份编码，避免同一处理漂移成两份。编码原语与读侧渲染见
``lib.generation.task_failure``。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from arcreel_market_core.video_backend_contract import VideoCapabilityError
from lib.asset_library.generation_inputs import ProviderAssetLinkUnavailableError
from lib.backends.grok_shared import grpc_rejection
from lib.backends.http_status_errors import ArtifactDownloadError, ProviderRejectedError, TextWaitTimeout
from lib.backends.image_backends.base import ImageCapabilityError, ImageOutputFormatError, ImageOutputMissingError
from lib.backends.provider_assets import ProviderInputImageRejectedError
from lib.billing.call_failure import CallErrorCode, classify_call_failure
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.resolver import ImageBucketCapabilityError, NoAvailableProviderError, VideoBucketCapabilityError
from lib.custom_provider.comfyui.failures import ComfyuiError
from lib.custom_provider.declarative_backend import DeclarativeRuntimeError
from lib.generation.task_failure import encode_failure
from lib.generation.video_request_facts import VideoRequestFactsError
from lib.infra.api_errors import ApiError
from lib.references.reference_compression import ReferencePayloadFloorError
from lib.script.reference_video.execution_checkpoint import ReferenceExecutionIdentityError
from lib.script.reference_video.request_projection import ReferenceProjectionBlockedError
from lib.script.script_editor import ScriptEditError

logger = logging.getLogger(__name__)


def encode_task_failure_message(exc: Exception) -> str:
    """已知拒绝走 code/params；数据库异常隐去 SQL 与参数，其余异常沿用 str(exc)。

    落库只存机器码，本地化文案由读侧按 ``Accept-Language`` 渲染——worker 后台无 request
    上下文，在这里渲染会把任务的失败原因锁死成单一语言。
    """
    if is_database_failure(exc):
        return encode_failure("internal_server_error")
    if isinstance(exc, NoAvailableProviderError):
        return _try_encode_failure(f"{exc.media_type}_default_model_not_set", {"media_type": exc.media_type}) or str(
            exc
        )
    if isinstance(exc, ScriptEditError):
        return _try_encode_failure(exc.key, exc.params) or encode_failure("script_edit_error")
    if isinstance(exc, ApiError):
        return _try_encode_failure(exc.key, exc.params) or str(exc)
    if isinstance(exc, ProviderRejectedError):
        params: dict[str, Any] = {"status": exc.response.status_code}
        if exc.provider_reason:
            params["provider_reason"] = exc.provider_reason
        return _try_encode_failure("provider_rejected", params) or str(exc)
    if (rejection := grpc_rejection(exc)) is not None:
        status, reason = rejection
        grpc_params: dict[str, Any] = {"status": status}
        if reason:
            grpc_params["provider_reason"] = reason
        return _try_encode_failure("provider_rejected", grpc_params) or str(exc)
    if isinstance(
        exc,
        ArtifactDownloadError
        | TextWaitTimeout
        | ImageCapabilityError
        | ImageOutputMissingError
        | ImageOutputFormatError
        | VideoCapabilityError
        | ReferencePayloadFloorError
        | ImageBucketCapabilityError
        | ModelNotInCatalogError
        | VideoBucketCapabilityError
        | ReferenceProjectionBlockedError
        | VideoRequestFactsError
        | ReferenceExecutionIdentityError
        | DeclarativeRuntimeError
        | ComfyuiError
        | ProviderAssetLinkUnavailableError
        | ProviderInputImageRejectedError,
    ):
        return _try_encode_failure(exc.code, exc.params) or str(exc)
    return str(exc)


def is_database_failure(exc: BaseException) -> bool:
    """沿异常包装链识别数据库失败，避免 SQL 与绑定参数进入任务信息。"""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, SQLAlchemyError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def is_provider_refusal(exc: BaseException) -> bool:
    """供应商是否真正拒绝了这次请求的内容或输入：原样重发只会被同样拒绝，该改输入。

    与 :func:`encode_task_failure_message` 落 ``provider_rejected`` 的判据同口径（httpx 通道的确定性 4xx、
    gRPC 的确定性拒绝），另认上游错误体里的内容策略码（与记账层 ``content_policy`` 同一判据）。
    沿 ``__cause__`` 链逐层判：调用方常把 SDK 异常包一层再抛。其余异常都不算拒绝。
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, ProviderRejectedError) or grpc_rejection(current) is not None:
            return True
        seen.add(id(current))
        current = current.__cause__
    return classify_call_failure(exc).error_code == CallErrorCode.CONTENT_POLICY


def _try_encode_failure(code: str, params: dict[str, Any]) -> str | None:
    """结构化编码失败原因，编不出来返回 None 交调用方降级。

    编码异常绝不能打断 mark_task_failed，否则任务会卡死在 running。
    """
    try:
        return encode_failure(code, **params)
    except KeyError:
        logger.warning("失败 code 未登记进 FAILURE_CODE_KEYS，降级为通用失败原因: code=%s", code)
    except (TypeError, ValueError, RecursionError):
        logger.warning("失败 params 无法序列化，降级为通用失败原因: code=%s", code)
    return None
