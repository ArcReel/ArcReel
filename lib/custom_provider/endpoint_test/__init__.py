"""端点测试三模式：预览请求、验证响应、测试连接。

三者对同一份定义回答三个层层递进的问题——「我要发出去的是什么」「供应商这样回我时我读成了什么」
「真发一次到底能不能成」。前两者不外发一个字节，第三者真实提交并计费。

服务层与 HTTP 形状 1:1、无 session 态、返回体不含凭证：router 是薄封装，外部 Agent 走同一批
HTTP 接口，两侧不会各自长出一套语义。定义的判定始终委托 ``endpoint_definition`` 的共享校验器，
执行始终委托 ``declarative_backend`` 的运行时——本包不新增第二份判定。
"""

from .check import STAGES, FieldExtraction, PathAttempt, StageReport, check_response, parse_response_body
from .errors import EndpointTestDefinitionError
from .inputs import (
    ASSET_SOURCES,
    EndpointTestAssets,
    EndpointTestCredentials,
    EndpointTestParameters,
)
from .preview import PreviewedRequest, RequestPreview, preview_request
from .trial_run import (
    MAX_POLL_RESPONSES,
    TRIAL_RUN_TTL_SECONDS,
    TrialRun,
    TrialRunBusyError,
    TrialRunManager,
    TrialRunStatus,
    TrialRunTarget,
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
    "TRIAL_RUN_TTL_SECONDS",
    "EndpointTestAssets",
    "EndpointTestCredentials",
    "EndpointTestDefinitionError",
    "EndpointTestParameters",
    "FieldExtraction",
    "PathAttempt",
    "PreviewedRequest",
    "RequestPreview",
    "StageReport",
    "TrialRun",
    "TrialRunBusyError",
    "TrialRunManager",
    "TrialRunStatus",
    "TrialRunTarget",
    "check_response",
    "declarative_target",
    "model_ref_target",
    "parse_response_body",
    "preview_request",
    "provider_from_base_url",
    "shutdown_trial_runs",
    "stage_report_payload",
    "trial_run_manager",
]
