"""Encode execution exceptions into persisted ``error_message``.

Normal ``GenerationWorker._process_task`` and resume ``VideoResumeRunner.run`` catch identical
exceptions through shared encoding, avoiding duplicated drift. Primitives/read rendering:
``lib.generation.task_failure``.
"""

from __future__ import annotations

import logging
from typing import Any

from arcreel_market_core.video_backend_contract import VideoCapabilityError
from lib.asset_library.generation_inputs import ProviderAssetLinkUnavailableError
from lib.backends.grok_shared import grpc_rejection
from lib.backends.http_status_errors import ArtifactDownloadError, ProviderRejectedError
from lib.backends.image_backends.base import ImageCapabilityError, ImageOutputFormatError, ImageOutputMissingError
from lib.backends.provider_assets import ProviderInputImageRejectedError
from lib.billing.call_failure import CallErrorCode, classify_call_failure
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.resolver import ImageBucketCapabilityError, VideoBucketCapabilityError
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
    """ScriptEditError, deterministic upstream 4xx, and structured execution refusals use code/params; others retain str(exc).

    Persist machine codes only; readers localize by ``Accept-Language``. Workers lack request
    contexts, so rendering here would freeze failures into single languages.
    """
    if isinstance(exc, ScriptEditError):
        # Fallback script_edit_error retains script-edit failure classification when specific encoding fails.
        return _try_encode_failure(exc.key, exc.params) or encode_failure("script_edit_error")
    if isinstance(exc, ApiError):
        return _try_encode_failure(exc.key, exc.params) or str(exc)
    if isinstance(exc, ProviderRejectedError):
        # Deterministic upstream 4xx: status/redacted summaries are separate parameters. Raw summaries stay outside translations,
        # read unchanged from error_params while outer wording follows Accept-Language.
        # Missing summaries still encode statuses, enabling localized rejection/FIX_INPUT.
        params: dict[str, Any] = {"status": exc.response.status_code}
        if exc.provider_reason:
            params["provider_reason"] = exc.provider_reason
        return _try_encode_failure("provider_rejected", params) or str(exc)
    if (rejection := grpc_rejection(exc)) is not None:
        # Deterministic xAI gRPC refusal shares HTTP 4xx encoding, never AioRpcError debug repr.
        status, reason = rejection
        grpc_params: dict[str, Any] = {"status": status}
        if reason:
            grpc_params["provider_reason"] = reason
        return _try_encode_failure("provider_rejected", grpc_params) or str(exc)
    if isinstance(
        exc,
        ArtifactDownloadError
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
        # Structured refusals lack generic fallback codes; retain str(exc), the code itself,
        # preserving reasons through unchanged read-side raw text.
        return _try_encode_failure(exc.code, exc.params) or str(exc)
    return str(exc)


def is_provider_refusal(exc: BaseException) -> bool:
    """Whether providers rejected content/inputs, requiring changes rather than identical resends.

    Match :func:`encode_task_failure_message` ``provider_rejected`` rules: deterministic HTTP 4xx /
    gRPC refusals, plus upstream policy codes shared with accounting ``content_policy``.
    Traverse ``__cause__`` because callers wrap SDK exceptions. Other errors are not refusals.
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
    """Encode structured failures; None if impossible for caller fallback.

    Encoding failures must never interrupt mark_task_failed, stranding running tasks.
    """
    try:
        return encode_failure(code, **params)
    except KeyError:
        # Codes absent from FAILURE_CODE_KEYS: two lists synchronize by convention, not runtime validation.
        logger.warning("失败 code 未登记进 FAILURE_CODE_KEYS，降级为通用失败原因: code=%s", code)
    except (TypeError, ValueError, RecursionError):
        # Non-JSON params: TypeError for key types even default=str cannot convert;
        # ValueError for json.dumps default check_circular cycles;
        # RecursionError for deep containers, not handled by default=str.
        logger.warning("失败 params 无法序列化，降级为通用失败原因: code=%s", code)
    return None
