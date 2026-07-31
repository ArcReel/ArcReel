import pytest

from lib.openai_model_catalog import (
    OPENAI_GPT_56_REASONING_EFFORTS,
    effective_openai_reasoning_effort,
    filter_openai_chat_text_models,
    is_openai_chat_text_model,
    openai_reasoning_efforts_for_model,
)

pytestmark = pytest.mark.unit


def test_filters_non_text_openai_models_from_live_catalog():
    model_ids = [
        "gpt-5.6-sol",
        "gpt-4o-realtime-preview",
        "gpt-image-2",
        "text-embedding-3-large",
        "o4-mini",
        "sora-2",
    ]

    assert filter_openai_chat_text_models(model_ids) == ["gpt-5.6-sol", "o4-mini"]


def test_chat_text_filter_is_conservative_for_unknown_families():
    assert is_openai_chat_text_model("gpt-5.6-terra") is True
    assert is_openai_chat_text_model("codex-mini-latest") is False
    assert is_openai_chat_text_model("whisper-1") is False


def test_gpt_56_family_exposes_verified_reasoning_efforts():
    assert openai_reasoning_efforts_for_model("gpt-5.6") == OPENAI_GPT_56_REASONING_EFFORTS
    assert openai_reasoning_efforts_for_model("gpt-5.6-sol-2026-07-01") == OPENAI_GPT_56_REASONING_EFFORTS
    assert openai_reasoning_efforts_for_model("gpt-5.5") == ()


def test_reasoning_effort_is_only_applied_to_supported_model():
    assert effective_openai_reasoning_effort("gpt-5.6-sol", "HIGH") == "high"
    assert effective_openai_reasoning_effort("gpt-5.5", "high") is None
    assert effective_openai_reasoning_effort("gpt-5.6-sol", "ultra") is None
