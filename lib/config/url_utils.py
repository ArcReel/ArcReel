"""URL 归一化工具函数。"""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse


def is_official_openai_base_url(base_url: str | None) -> bool:
    """判断 OpenAI 兼容 base_url 是否指向官方 api.openai.com。

    官方端点上 max_tokens 已弃用且被推理模型（o 系列 / gpt-5 等）拒绝，
    应改用 max_completion_tokens；第三方兼容端点（vLLM、各类中转）对新
    参数支持情况不一，须保守沿用 max_tokens。

    base_url 为空（None/空白串）时，openai SDK 会回落到 OPENAI_BASE_URL
    环境变量，再回落到官方端点，此处判定与 SDK 行为保持一致。

    已知限制：指向中转/代理的 base_url 一律判非官方，若中转将 max_tokens
    原样转发给官方推理模型仍会被拒（显式 400，报错信息自描述）。
    """
    effective = (base_url or "").strip() or (os.environ.get("OPENAI_BASE_URL") or "").strip()
    if not effective:
        return True
    # hostname 自带小写化与去端口；无 scheme 时 hostname 为 None → 保守判非官方
    return urlparse(effective).hostname == "api.openai.com"


def ensure_openai_base_url(url: str | None) -> str | None:
    """自动补全 OpenAI 兼容 API 的 /v1 路径后缀。

    用户可能只填了 ``https://api.example.com``，但 OpenAI SDK 期望
    ``https://api.example.com/v1``。本函数在缺少版本路径时自动追加。
    """
    if not url:
        return url
    stripped = url.strip().rstrip("/")
    if not re.search(r"/v\d+$", stripped):
        stripped += "/v1"
    return stripped


def normalize_base_url(url: str | None) -> str | None:
    """确保 base_url 以 / 结尾。

    Google genai SDK 的 http_options.base_url 要求尾部带 /，
    否则请求路径拼接会失败。预置 Gemini 后端使用此函数。
    """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    if not url.endswith("/"):
        url += "/"
    return url


def ensure_google_base_url(url: str | None) -> str | None:
    """规范化 Google genai SDK 的 base_url。

    Google genai SDK 会自动在 base_url 后拼接 ``api_version``（默认 ``v1beta``）。
    如果用户误填了 ``https://example.com/v1beta``，SDK 会拼出
    ``https://example.com/v1beta/v1beta/models``，导致请求失败。

    本函数剥离末尾的版本路径（如 ``/v1beta``、``/v1``），并确保尾部带 ``/``。
    """
    if not url:
        return None
    url = url.strip()
    if not url:
        return None
    url = url.rstrip("/")
    # 剥离末尾的版本路径（/v1, /v1beta, /v1alpha 等）
    # 用 [a-zA-Z] 代替 \w：\d+\w* 的重叠会触发 CodeQL polynomial regex 警告
    url = re.sub(r"/v\d+[a-zA-Z]*$", "", url)
    if not url.endswith("/"):
        url += "/"
    return url


def ensure_anthropic_base_url(url: str | None) -> str | None:
    """规范化 Anthropic base_url。

    @anthropic-ai/sdk 内部会拼接 /v1/messages、/v1/models 等，所以
    base_url 必须是根级形态。如用户填了 https://example.com/v1 或
    /v1beta、/v1/messages 等带版本前缀的形式，需要剥掉，否则会拼出
    /v1/v1/messages 报 404。
    """
    if not url:
        return None
    s = url.strip().rstrip("/")
    if not s:
        return None
    # [a-zA-Z]* 兼容 /v1beta /v2alpha 等带后缀的版本号
    # 用 [a-zA-Z] 代替 \w：\d+\w* 的重叠会触发 CodeQL polynomial regex 警告
    s = re.sub(r"/v\d+[a-zA-Z]*(?:/messages)?$", "", s)
    s = re.sub(r"/messages$", "", s)
    return s
