"""AgnesTextBackend — Agnes 文本生成后端（OpenAI 兼容 /v1/chat/completions）。

Agnes 经 apihub 网关提供 OpenAI 风格 Chat Completions。鉴权与 base_url 归一化复用
agnes_shared（Bearer 单 key + host/{/v1} 后缀容错），生成流水线直接复用 OpenAITextBackend：
原生 ``response_format`` json_schema 优先，schema 不兼容或代理未真正强制 schema 时按需降级到
Instructor（选择性降级）。本类只在构造期注入 Agnes 鉴权 / 默认模型 / provider 计费归因，
并裁掉未实测的 vision 能力声明。
"""

from __future__ import annotations

from lib.agnes_shared import agnes_base_url, resolve_agnes_api_key
from lib.providers import PROVIDER_AGNES
from lib.text_backends.base import TextCapability
from lib.text_backends.openai import OpenAITextBackend

DEFAULT_MODEL = "agnes-2.0-flash"


class AgnesTextBackend(OpenAITextBackend):
    """Agnes 文本后端：复用 OpenAITextBackend 的原生 + Instructor 降级逻辑，仅替换鉴权与默认值。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        # Bearer 单 key 在构造期校验：缺失即本地 raise，与 AgnesImageBackend 同 fail-loud，不把缺失
        # key 拖到请求期才收上游 401。base_url 归一化为 {host}/v1，容忍用户填 host 或带 /v1 后缀。
        super().__init__(
            api_key=resolve_agnes_api_key(api_key),
            model=model or DEFAULT_MODEL,
            base_url=agnes_base_url(base_url),
            provider_name=PROVIDER_AGNES,
        )
        # agnes-2.0-flash 仅声明文本生成与结构化输出；vision 未实测，不纳入能力集（父类默认含 VISION）。
        self._capabilities = {TextCapability.TEXT_GENERATION, TextCapability.STRUCTURED_OUTPUT}
