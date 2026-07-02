"""Unit tests for token/cost extraction pure functions.

These feed result-message data directly to the module functions without
constructing a SessionManager instance.
"""

import pytest

from server.agent_runtime.usage_extraction import (
    extract_assistant_cost,
    extract_float,
    extract_int,
    extract_model_usage_tokens,
    extract_text_token_usage,
    first_int,
    resolve_assistant_model,
    resolve_configured_assistant_model,
)


class TestExtractTextTokenUsage:
    def test_extract_text_token_usage_accepts_numeric_strings(self):
        input_tokens, output_tokens, usage_tokens = extract_text_token_usage(
            {
                "usage": {
                    "input_tokens": "1000.0",
                    "output_tokens": "200",
                    "cache_read_input_tokens": 50.0,
                }
            }
        )

        # input_tokens includes prompt cache read/creation tokens for aggregate reporting.
        assert input_tokens == 1050
        assert output_tokens == 200
        assert usage_tokens == 1250

    def test_extract_text_token_usage_preserves_missing_as_none(self):
        input_tokens, output_tokens, usage_tokens = extract_text_token_usage(
            {"usage": {"input_tokens": None, "output_tokens": "not-a-number"}}
        )

        assert input_tokens is None
        assert output_tokens is None
        assert usage_tokens is None

    def test_extract_text_token_usage_rejects_invalid_values(self):
        assert extract_text_token_usage({"usage": {"input_tokens": "inf"}}) == (None, None, None)
        assert extract_text_token_usage({"usage": {"input_tokens": "1.9"}}) == (None, None, None)
        assert extract_text_token_usage({"usage": {"input_tokens": 1.9}}) == (None, None, None)
        assert extract_text_token_usage({"model_usage": {"m": {"inputTokens": float("nan")}}}) == (
            None,
            None,
            None,
        )

    def test_extract_text_token_usage_falls_back_to_model_usage(self):
        """Empty usage dict delegates to model_usage aggregation."""
        result = extract_text_token_usage(
            {
                "model_usage": {
                    "claude-sonnet-4": {
                        "inputTokens": 100,
                        "outputTokens": 20,
                        "cacheCreationInputTokens": 30,
                        "cacheReadInputTokens": 40,
                    }
                }
            }
        )
        assert result == (170, 20, 190)


class TestExtractModelUsageTokens:
    def test_sums_across_models(self):
        result = extract_model_usage_tokens(
            {
                "model_usage": {
                    "a": {"inputTokens": 10, "outputTokens": 2},
                    "b": {"inputTokens": 5, "outputTokens": 3, "cacheReadInputTokens": 1},
                }
            }
        )
        assert result == (16, 5, 21)

    def test_missing_model_usage_returns_none(self):
        assert extract_model_usage_tokens({}) == (None, None, None)
        assert extract_model_usage_tokens({"model_usage": "not-a-dict"}) == (None, None, None)


class TestExtractAssistantCost:
    def test_extract_assistant_cost_rejects_invalid_values(self):
        assert extract_assistant_cost({"total_cost_usd": -1}) is None
        assert extract_assistant_cost({"total_cost_usd": "nan"}) is None
        assert extract_assistant_cost({"model_usage": {"m": {"costUSD": -0.1}}}) is None

    def test_prefers_total_cost(self):
        assert extract_assistant_cost({"total_cost_usd": 1.5}) == pytest.approx(1.5)

    def test_sums_model_cost_when_no_total(self):
        cost = extract_assistant_cost({"model_usage": {"a": {"costUSD": 0.01}, "b": {"costUSD": 0.02}}})
        assert cost == pytest.approx(0.03)


class TestNumericHelpers:
    def test_extract_int(self):
        assert extract_int(5) == 5
        assert extract_int(True) is None
        assert extract_int(1.9) is None
        assert extract_int("3") == 3
        assert extract_int("  ") is None

    def test_extract_float(self):
        assert extract_float(1.5) == pytest.approx(1.5)
        assert extract_float(True) is None
        assert extract_float(-1) is None
        assert extract_float("nope") is None

    def test_first_int_prefers_earliest_key(self):
        assert first_int({"a": 1, "b": 2}, "a", "b") == 1
        assert first_int({"b": 2}, "a", "b") == 2
        assert first_int({}, "a", "b") is None


class TestResolveModel:
    def test_resolve_assistant_model_prefers_result_model(self):
        assert resolve_assistant_model({"model": " claude-x "}, "cfg") == "claude-x"

    def test_resolve_assistant_model_falls_back_to_configured(self):
        assert resolve_assistant_model({}, " cfg-model ") == "cfg-model"

    def test_resolve_assistant_model_single_model_usage(self):
        assert resolve_assistant_model({"model_usage": {"claude-y": {}}}) == "claude-y"

    def test_resolve_configured_assistant_model(self):
        assert resolve_configured_assistant_model({"ANTHROPIC_MODEL": " m "}) == "m"
        assert resolve_configured_assistant_model({"ANTHROPIC_DEFAULT_OPUS_MODEL": "opus"}) == "opus"
        assert resolve_configured_assistant_model(None) == ""
        assert resolve_configured_assistant_model({}) == ""
