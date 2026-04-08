"""Instructor fallback support — provides prompt injection + parsing + retry for models that do not support native structured output."""

from __future__ import annotations

import logging

import instructor
from instructor import Mode
from pydantic import BaseModel

from lib.text_backends.base import TextGenerationResult

logger = logging.getLogger(__name__)


def generate_structured_via_instructor(
    client,
    model: str,
    messages: list[dict],
    response_model: type[BaseModel],
    mode: Mode = Mode.MD_JSON,
    max_retries: int = 2,
) -> tuple[str, int | None, int | None]:
    """Generate structured output via Instructor (synchronous version, for Ark and other sync SDKs).

    Returns (json_text, input_tokens, output_tokens).
    """
    patched = instructor.from_openai(client, mode=mode)
    if patched is None:
        raise TypeError(
            f"instructor.from_openai() returned None — client type {type(client).__name__} is not supported. "
            "Please pass an openai.OpenAI or openai.AsyncOpenAI instance."
        )
    result, completion = patched.chat.completions.create_with_completion(
        model=model,
        messages=messages,
        response_model=response_model,
        max_retries=max_retries,
    )
    json_text = result.model_dump_json()

    input_tokens = None
    output_tokens = None
    if completion.usage:
        input_tokens = completion.usage.prompt_tokens
        output_tokens = completion.usage.completion_tokens

    return json_text, input_tokens, output_tokens


async def generate_structured_via_instructor_async(
    client,
    model: str,
    messages: list[dict],
    response_model: type[BaseModel],
    mode: Mode = Mode.MD_JSON,
    max_retries: int = 2,
) -> tuple[str, int | None, int | None]:
    """Generate structured output via Instructor (asynchronous version, for OpenAI AsyncOpenAI).

    Returns (json_text, input_tokens, output_tokens).
    """
    patched = instructor.from_openai(client, mode=mode)
    if patched is None:
        raise TypeError(
            f"instructor.from_openai() returned None — client type {type(client).__name__} is not supported. "
            "Please pass an openai.OpenAI or openai.AsyncOpenAI instance."
        )
    result, completion = await patched.chat.completions.create_with_completion(
        model=model,
        messages=messages,
        response_model=response_model,
        max_retries=max_retries,
    )
    json_text = result.model_dump_json()

    input_tokens = None
    output_tokens = None
    if completion.usage:
        input_tokens = completion.usage.prompt_tokens
        output_tokens = completion.usage.completion_tokens

    return json_text, input_tokens, output_tokens


def inject_json_instruction(messages: list[dict]) -> list[dict]:
    """Inject JSON format instruction into messages to ensure json_object mode is available.

    The OpenAI API requires the prompt to contain the "JSON" keyword to enable json_object mode.
    If messages already contain "JSON", return a copy unchanged.
    """
    fb_messages = list(messages)
    if any("JSON" in (m.get("content") or "") for m in fb_messages):
        return fb_messages
    sys_idx = next((i for i, m in enumerate(fb_messages) if m.get("role") == "system"), None)
    if sys_idx is not None:
        orig = fb_messages[sys_idx]
        fb_messages[sys_idx] = {**orig, "content": (orig.get("content") or "") + "\nRespond in JSON format."}
    else:
        fb_messages.insert(0, {"role": "system", "content": "Respond in JSON format."})
    return fb_messages


def instructor_fallback_sync(
    client,
    model: str,
    messages: list[dict],
    response_schema: dict | type,
    provider: str,
):
    """Synchronous Instructor fallback path.

    - response_schema is a Pydantic class → instructor create_with_completion
    - response_schema is a dict → inject JSON instruction + json_object mode

    For use with Ark and other sync SDK backends (callers wrap with asyncio.to_thread).
    No retries; transient errors are handled uniformly by the caller's retry loop.
    """
    if isinstance(response_schema, type):
        json_text, input_tokens, output_tokens = generate_structured_via_instructor(
            client=client,
            model=model,
            messages=messages,
            response_model=response_schema,
        )
        return TextGenerationResult(
            text=json_text,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    logger.info("response_schema is a dict; Instructor cannot be used, falling back to json_object mode")
    fb_messages = inject_json_instruction(messages)
    response = client.chat.completions.create(
        model=model,
        messages=fb_messages,
        response_format={"type": "json_object"},
    )
    usage = getattr(response, "usage", None)
    text = response.choices[0].message.content or ""
    return TextGenerationResult(
        text=text.strip() if isinstance(text, str) else str(text),
        provider=provider,
        model=model,
        input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
        output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
    )


async def instructor_fallback_async(
    client,
    model: str,
    messages: list[dict],
    response_schema: dict | type,
    provider: str,
):
    """Asynchronous Instructor fallback path.

    - response_schema is a Pydantic class → instructor create_with_completion (async)
    - response_schema is a dict → inject JSON instruction + json_object mode (async)

    For use with OpenAI and other native async SDK backends.
    No retries; transient errors are handled uniformly by the caller's retry loop.
    """
    from lib.text_backends.base import TextGenerationResult

    if isinstance(response_schema, type):
        json_text, input_tokens, output_tokens = await generate_structured_via_instructor_async(
            client=client,
            model=model,
            messages=messages,
            response_model=response_schema,
        )
        return TextGenerationResult(
            text=json_text,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    logger.info("response_schema is a dict; Instructor cannot be used, falling back to json_object mode")
    fb_messages = inject_json_instruction(messages)
    response = await client.chat.completions.create(
        model=model,
        messages=fb_messages,
        response_format={"type": "json_object"},
    )
    usage = getattr(response, "usage", None)
    text = response.choices[0].message.content or ""
    return TextGenerationResult(
        text=text.strip() if isinstance(text, str) else str(text),
        provider=provider,
        model=model,
        input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
        output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
    )
