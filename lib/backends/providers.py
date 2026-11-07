"""Shared provider-call vocabulary: provider-name constants and call types/statuses/purposes.

Provider names serve image_backends / video_backends; ``CallType`` / ``CallStatus`` / ``CallPurpose``
serve accounting, repositories, and routers. Define ``api_calls`` statuses/purposes once here,
never duplicate string literals at call sites.
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
PROVIDER_OPENROUTER = "openrouter"

CallType = Literal["image", "video", "text", "audio"]
CALL_TYPE_IMAGE: CallType = "image"
CALL_TYPE_VIDEO: CallType = "video"
CALL_TYPE_TEXT: CallType = "text"
CALL_TYPE_AUDIO: CallType = "audio"


class CallStatus(StrEnum):
    """Provider-call states, separate from task states; neither borrows the other's vocabulary.

    ``cancelled`` identifies user-abandoned zero-cost calls, distinct from ``failed`` to keep cancellations out of failure rates.
    """

    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CallPurpose(StrEnum):
    """Why provider calls were initiated: seven values in GLOSSARY.md's purpose entry.

    Generation calls trace served tasks through ``api_calls.task_id``; the remaining six identify taskless call origins.
    """

    SCRIPT_GENERATION = "script_generation"
    EPISODE_PLANNING = "episode_planning"
    PROJECT_OVERVIEW = "project_overview"
    STYLE_ANALYSIS = "style_analysis"
    ASSISTANT_SESSION = "assistant_session"
    ENDPOINT_TRIAL = "endpoint_trial"
    GENERATION_TASK = "generation_task"


def require_provider_pair(kind: str, backend: object | None, provider_id: str | None) -> None:
    """Construction invariant: media/text backends and resolved registry provider_id must both exist or both be absent.

    Accounting always uses resolved provider_id as sole authority; backends handle generation and log/error
    context only. Missing either is assembly failure: backend without provider_id loses accounting identity;
    provider_id without backend declares an unusable lane. Check once here, without repeated call-time
    validation or exemption lists.
    """
    if (backend is None) != (provider_id is None):
        raise ValueError(
            f"{kind} backend 与 provider_id 必须成对提供："
            f"backend={'set' if backend is not None else 'None'}, provider_id={provider_id!r}"
        )
