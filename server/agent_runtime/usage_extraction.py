"""Pure functions extracting token usage and cost from SDK result messages.

These operate purely on the result-message dict (no session state), so they
can be unit-tested by feeding message data directly. The usage-accounting
action (writing the extracted numbers to UsageTracker) stays in SessionManager.
"""

import math
import os
from typing import Any


def extract_text_token_usage(result_msg: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    usage = result_msg.get("usage")
    usage_dict = usage if isinstance(usage, dict) else {}
    raw_input_tokens = first_int(usage_dict, "input_tokens", "prompt_tokens")
    output_tokens = first_int(usage_dict, "output_tokens", "completion_tokens")
    cache_creation_tokens = first_int(usage_dict, "cache_creation_input_tokens")
    cache_read_tokens = first_int(usage_dict, "cache_read_input_tokens")
    if (
        raw_input_tokens is None
        and output_tokens is None
        and cache_creation_tokens is None
        and cache_read_tokens is None
    ):
        return extract_model_usage_tokens(result_msg)

    # Claude Agent SDK reports prompt cache tokens separately. Store them in
    # input_tokens as well so aggregate usage includes the full prompt-side token volume.
    input_parts = (raw_input_tokens, cache_creation_tokens, cache_read_tokens)
    input_tokens = sum(part or 0 for part in input_parts) if any(part is not None for part in input_parts) else None
    token_parts = (raw_input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens)
    usage_tokens = sum(part or 0 for part in token_parts) if any(part is not None for part in token_parts) else None
    return input_tokens, output_tokens, usage_tokens


def extract_model_usage_tokens(result_msg: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    model_usage = result_msg.get("model_usage")
    if not isinstance(model_usage, dict):
        return None, None, None

    raw_input_total = 0
    output_total = 0
    cache_creation_total = 0
    cache_read_total = 0
    has_tokens = False
    has_input_tokens = False
    has_output_tokens = False
    for usage in model_usage.values():
        if not isinstance(usage, dict):
            continue
        raw_input = first_int(usage, "inputTokens")
        output = first_int(usage, "outputTokens")
        cache_creation = first_int(usage, "cacheCreationInputTokens")
        cache_read = first_int(usage, "cacheReadInputTokens")
        if any(part is not None for part in (raw_input, output, cache_creation, cache_read)):
            has_tokens = True
        if any(part is not None for part in (raw_input, cache_creation, cache_read)):
            has_input_tokens = True
        if output is not None:
            has_output_tokens = True
        raw_input_total += raw_input or 0
        output_total += output or 0
        cache_creation_total += cache_creation or 0
        cache_read_total += cache_read or 0

    if not has_tokens:
        return None, None, None
    input_tokens = raw_input_total + cache_creation_total + cache_read_total if has_input_tokens else None
    output_tokens = output_total if has_output_tokens else None
    usage_tokens = raw_input_total + output_total + cache_creation_total + cache_read_total
    return input_tokens, output_tokens, usage_tokens


def extract_assistant_cost(result_msg: dict[str, Any]) -> float | None:
    total_cost = extract_float(result_msg.get("total_cost_usd"))
    if total_cost is not None:
        return total_cost

    model_usage = result_msg.get("model_usage")
    if not isinstance(model_usage, dict):
        return None

    model_cost_total = 0.0
    has_model_cost = False
    for usage in model_usage.values():
        if not isinstance(usage, dict):
            continue
        cost = extract_float(usage.get("costUSD"))
        if cost is None:
            continue
        model_cost_total += cost
        has_model_cost = True
    return model_cost_total if has_model_cost else None


def first_int(source: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = extract_int(source.get(key))
        if value is not None:
            return value
    return None


def extract_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) and value >= 0 and value.is_integer() else None
    if isinstance(value, str):
        value_str = value.strip()
        if not value_str:
            return None
        try:
            numeric_value = float(value_str)
        except ValueError:
            return None
        if not math.isfinite(numeric_value) or numeric_value < 0 or not numeric_value.is_integer():
            return None
        return int(numeric_value)
    return None


def extract_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    return numeric_value if math.isfinite(numeric_value) and numeric_value >= 0 else None


def resolve_assistant_model(result_msg: dict[str, Any], configured_model: str = "") -> str:
    model = result_msg.get("model") or result_msg.get("model_name")
    if isinstance(model, str) and model.strip():
        return model.strip()
    if configured_model.strip():
        return configured_model.strip()
    model_usage = result_msg.get("model_usage")
    if isinstance(model_usage, dict) and len(model_usage) == 1:
        model_name = next(iter(model_usage))
        if isinstance(model_name, str) and model_name.strip():
            return model_name.strip()
    return os.environ.get("ANTHROPIC_MODEL", "").strip() or "claude-sonnet-4"


def resolve_configured_assistant_model(env: Any) -> str:
    if not isinstance(env, dict):
        return ""
    for key in ("ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL"):
        value = env.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
