"""集中维护 provider / AUTH 相关的环境变量 key 清单。

唯一真相源 — 凡是涉及 os.environ 名单的代码都从这里 import。
"""

from __future__ import annotations

# —— SDK 子进程需要的 Anthropic env keys（通过 options.env 注入）——
ANTHROPIC_ENV_KEYS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)

# —— 其他 provider env keys（options.env 用空值覆盖兜底）——
OTHER_PROVIDER_ENV_KEYS: tuple[str, ...] = (
    "ARK_API_KEY",
    "XAI_API_KEY",
    "GEMINI_API_KEY",
    "VIDU_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GEMINI_BASE_URL",
    "GEMINI_IMAGE_MODEL",
    "GEMINI_VIDEO_MODEL",
    "GEMINI_IMAGE_BACKEND",
    "GEMINI_VIDEO_BACKEND",
    "VERTEX_GCS_BUCKET",
    "FILE_SERVICE_BASE_URL",
    "DEFAULT_VIDEO_PROVIDER",
)

# —— 启动断言：真密钥子集，命中即 fail-fast（spec §7.2）——
PROVIDER_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ARK_API_KEY",
        "XAI_API_KEY",
        "GEMINI_API_KEY",
        "VIDU_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
    }
)


def is_provider_env_key(name: str) -> bool:
    """判断给定 env key 是否属于 provider 相关。"""
    return name in ANTHROPIC_ENV_KEYS or name in OTHER_PROVIDER_ENV_KEYS
