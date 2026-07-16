"""供应商名称常量，image_backends / video_backends 共用。"""

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
