"""OpenAI model-catalog helpers shared by configuration and provider APIs.

The Models API intentionally exposes only basic model metadata.  ArcReel
therefore keeps endpoint compatibility and reasoning-effort metadata locally
while using the live API response as the source of truth for account access.
"""

from __future__ import annotations

from collections.abc import Iterable

OPENAI_GPT_56_REASONING_EFFORTS: tuple[str, ...] = (
    "none",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)

_TEXT_MODEL_PREFIXES = ("gpt-", "chatgpt-", "o1", "o3", "o4")
_NON_CHAT_MODEL_MARKERS = (
    "audio",
    "codex",
    "computer-use",
    "dall-e",
    "deep-research",
    "embedding",
    "image",
    "instruct",
    "moderation",
    "realtime",
    "search",
    "sora",
    "transcribe",
    "tts",
)


def is_openai_chat_text_model(model_id: str) -> bool:
    """Return whether an API model ID is a plausible Chat Completions text model.

    ``GET /v1/models`` does not declare endpoint or media capabilities.  This
    conservative filter keeps known non-chat families out of ArcReel's text
    picker instead of claiming capabilities that the API response did not
    provide.
    """

    normalized = model_id.strip().lower()
    if not normalized or not normalized.startswith(_TEXT_MODEL_PREFIXES):
        return False
    return not any(marker in normalized for marker in _NON_CHAT_MODEL_MARKERS)


def filter_openai_chat_text_models(model_ids: Iterable[str]) -> list[str]:
    """Normalize, de-duplicate, and sort live OpenAI text-model IDs."""

    return sorted({model_id.strip() for model_id in model_ids if is_openai_chat_text_model(model_id)})


def openai_reasoning_efforts_for_model(model_id: str) -> tuple[str, ...]:
    """Return locally verified reasoning-effort values for an OpenAI model."""

    normalized = model_id.strip().lower()
    if normalized == "gpt-5.6" or normalized.startswith("gpt-5.6-"):
        return OPENAI_GPT_56_REASONING_EFFORTS
    return ()


def effective_openai_reasoning_effort(model_id: str, configured: str | None) -> str | None:
    """Return a configured effort only when the selected model supports it."""

    effort = (configured or "").strip().lower()
    if effort in openai_reasoning_efforts_for_model(model_id):
        return effort
    return None
