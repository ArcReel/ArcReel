"""Central provider/AUTH environment-key lists.

Sole authority: all os.environ-list consumers import here.
"""

from __future__ import annotations

# Anthropic SDK subprocess environment keys, injected through options.env.
ANTHROPIC_ENV_KEYS: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)

# Other provider environment keys, overridden empty in options.env.
OTHER_PROVIDER_ENV_KEYS: tuple[str, ...] = (
    # Claude CLI/SDK prioritize these bypass authentication variables. Outside ArcReel provider
    # credentials, explicitly clear in Agent sessions to prevent host-environment inheritance.
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ARK_API_KEY",
    "XAI_API_KEY",
    "GEMINI_API_KEY",
    "VIDU_API_KEY",
    "DASHSCOPE_API_KEY",
    "MINIMAX_API_KEY",
    "AGNES_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    # OpenAI SDK fallback variables without explicit client options, lacking secret-like names
    # recognized by _SECRET_ENV_NAME_PATTERNS substring matching. Override the whole group
    # to prevent fork inheritance into SDK/Bash subprocesses.
    "OPENAI_BASE_URL",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
    "OPENAI_CUSTOM_HEADERS",
    "GEMINI_BASE_URL",
    "GEMINI_IMAGE_MODEL",
    "GEMINI_VIDEO_MODEL",
    "GEMINI_IMAGE_BACKEND",
    "GEMINI_VIDEO_BACKEND",
    "VERTEX_GCS_BUCKET",
    "FILE_SERVICE_BASE_URL",
    "DEFAULT_VIDEO_PROVIDER",
)

# Startup assertions: actual secret subset, failing fast on hits.
PROVIDER_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ARK_API_KEY",
        "XAI_API_KEY",
        "GEMINI_API_KEY",
        "VIDU_API_KEY",
        "DASHSCOPE_API_KEY",
        "MINIMAX_API_KEY",
        "AGNES_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
    }
)


def is_provider_env_key(name: str) -> bool:
    """Whether environment keys belong to providers."""
    return name in ANTHROPIC_ENV_KEYS or name in OTHER_PROVIDER_ENV_KEYS
