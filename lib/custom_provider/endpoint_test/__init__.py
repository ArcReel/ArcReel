"""Three endpoint-test modes: preview requests, validate responses, test connections.

Progressively answer what will be sent, how provider responses will be interpreted,
and whether real requests succeed. First two transmit no bytes; third submits/bills real calls.

Services match HTTP shapes 1:1 without session state/response credentials. Thin routers expose the same APIs to external
Agents, avoiding diverging semantics. Definition decisions always delegate to shared ``endpoint_definition`` validators;
execution always delegates to ``declarative_backend`` runtimes, never adding second interpretations here.
"""

from .check import STAGES, FieldExtraction, PathAttempt, StageReport, check_response, parse_response_body
from .comfyui import ComfyuiConversions, comfyui_target, preview_comfyui_request
from .errors import EndpointTestDefinitionError
from .inputs import (
    ASSET_SOURCES,
    EndpointTestAssets,
    EndpointTestCredentials,
    EndpointTestParameters,
)
from .kinds import (
    SUPPORTED_KINDS,
    KindTestSupport,
    support_for_kind,
)
from .modes import TESTABLE_KINDS, EndpointTestMode, supports_test_mode
from .preview import PreviewedRequest, RequestPreview, preview_request
from .trial_run import (
    MAX_POLL_RESPONSES,
    TRIAL_RUN_STAGES,
    TRIAL_RUN_TTL_SECONDS,
    TrialRun,
    TrialRunBusyError,
    TrialRunManager,
    TrialRunStatus,
    TrialRunTarget,
    artifact_media_type,
    declarative_target,
    model_ref_target,
    provider_from_base_url,
    shutdown_trial_runs,
    stage_report_payload,
    trial_run_manager,
)

__all__ = [
    "ASSET_SOURCES",
    "MAX_POLL_RESPONSES",
    "STAGES",
    "SUPPORTED_KINDS",
    "TESTABLE_KINDS",
    "TRIAL_RUN_STAGES",
    "TRIAL_RUN_TTL_SECONDS",
    "ComfyuiConversions",
    "EndpointTestAssets",
    "EndpointTestCredentials",
    "EndpointTestDefinitionError",
    "EndpointTestMode",
    "EndpointTestParameters",
    "FieldExtraction",
    "KindTestSupport",
    "PathAttempt",
    "PreviewedRequest",
    "RequestPreview",
    "StageReport",
    "TrialRun",
    "TrialRunBusyError",
    "TrialRunManager",
    "TrialRunStatus",
    "TrialRunTarget",
    "artifact_media_type",
    "check_response",
    "comfyui_target",
    "declarative_target",
    "model_ref_target",
    "parse_response_body",
    "preview_comfyui_request",
    "preview_request",
    "provider_from_base_url",
    "shutdown_trial_runs",
    "stage_report_payload",
    "support_for_kind",
    "supports_test_mode",
    "trial_run_manager",
]
