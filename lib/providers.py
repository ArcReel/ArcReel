"""供应商调用的共享词汇：供应商名称常量与调用的类型 / 状态 / 来源。

供应商名称供 image_backends / video_backends 共用；调用词汇（``CallType`` / ``CallStatus`` /
``CallPurpose``）供记账层、仓储与路由共用——``api_calls`` 的状态与来源取值只在这里定义一次，
调用点不再各写字符串字面量。
"""

from enum import StrEnum
from typing import Literal

PROVIDER_GEMINI = "gemini"
PROVIDER_ARK = "ark"
PROVIDER_ARK_AGENT_PLAN = "ark-agent-plan"
PROVIDER_GROK = "grok"
PROVIDER_OPENAI = "openai"
PROVIDER_VIDU = "vidu"
PROVIDER_NEWAPI = "newapi"
PROVIDER_DASHSCOPE = "dashscope"
PROVIDER_MINIMAX = "minimax"
PROVIDER_KLING = "kling"
PROVIDER_AGNES = "agnes"
PROVIDER_ANTHROPIC = "anthropic"

CallType = Literal["image", "video", "text", "audio"]
CALL_TYPE_IMAGE: CallType = "image"
CALL_TYPE_VIDEO: CallType = "video"
CALL_TYPE_TEXT: CallType = "text"
CALL_TYPE_AUDIO: CallType = "audio"


class CallStatus(StrEnum):
    """一次供应商调用的状态。与任务状态是两套词汇，互不取用。

    ``cancelled`` 是用户放弃的调用（零费用），与 ``failed`` 分开，免得取消污染失败率。
    """

    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CallPurpose(StrEnum):
    """一次供应商调用被发起的原因（CONTEXT「来源（purpose）」词条的七个取值）。

    生成任务经 ``api_calls.task_id`` 回指它服务的任务，其余六个是无任务的调用各自的来源。
    """

    SCRIPT_GENERATION = "script_generation"
    EPISODE_PLANNING = "episode_planning"
    PROJECT_OVERVIEW = "project_overview"
    STYLE_ANALYSIS = "style_analysis"
    ASSISTANT_SESSION = "assistant_session"
    ENDPOINT_TRIAL = "endpoint_trial"
    GENERATION_TASK = "generation_task"


def require_provider_pair(kind: str, backend: object | None, provider_id: str | None) -> None:
    """构造期成对不变量：媒体/文本 backend 与其解析层 registry provider_id 必须同在同缺。

    记账 provider 一律取解析层 provider_id（单一真相源），backend 仅承担生成调用与日志/错误
    上下文。二者缺一即为装配错误：backend 有而 provider_id 无 → 记账失去身份来源；provider_id
    有而 backend 无 → 声明了该 lane 却没有可用 backend。此处一次性拦截，调用期不再逐次校验、
    也不设豁免名单。
    """
    if (backend is None) != (provider_id is None):
        raise ValueError(
            f"{kind} backend 与 provider_id 必须成对提供："
            f"backend={'set' if backend is not None else 'None'}, provider_id={provider_id!r}"
        )
