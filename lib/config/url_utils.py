"""URL 归一化工具函数。"""

from __future__ import annotations

import re


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
