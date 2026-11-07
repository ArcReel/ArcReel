"""Core text-generation service interfaces."""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ValidationError
from pydantic.json_schema import GenerateJsonSchema, JsonSchemaMode, JsonSchemaValue

from lib.infra.retry import NonRetryableError
from lib.infra.text_utils import strip_json_code_fences

_logger = logging.getLogger(__name__)

# Chat Completions output-limit parameter: official OpenAI endpoints deprecated max_tokens (reasoning models
# reject it), replacing it with max_completion_tokens. Third-party compatible endpoints vary in support,
# so conservatively retain max_tokens there. Callers select by endpoint.
TokenParam = Literal["max_tokens", "max_completion_tokens"]


def warn_if_truncated(
    finish_reason: str | None,
    *,
    provider: str,
    model: str,
    output_tokens: int | None = None,
    truncation_values: tuple[str, ...] = ("length", "MAX_TOKENS", "max_tokens"),
) -> bool:
    """Detect token-limit truncation and log a warning when present.

    Return True for truncation so callers can handle it further. Free text (no response_schema)
    only warns, exposing ``truncated`` on results; structured-output truncation must use
    :func:`check_truncation` to become a hard error.
    """
    if finish_reason is None:
        return False
    if finish_reason in truncation_values:
        _logger.warning(
            "%s/%s 输出被截断（finish_reason=%s, output_tokens=%s）：已达模型输出上限。"
            "考虑切换到更大输出上限的模型，或减少请求规模。",
            provider,
            model,
            finish_reason,
            output_tokens,
        )
        return True
    return False


class TextOutputTruncatedError(NonRetryableError):
    """Text model output hit its output limit, leaving incomplete results unsafe for direct use.

    Backends raise this only for response_schema requests (structured-output intent); free-text truncation only warns and
    marks results ``truncated`` (docs/adr/0044). When callers require complete output,
    :meth:`lib.backends.text_generator.TextGenerator.generate` upgrades it to this exception.

    Inherit NonRetryableError rather than RuntimeError directly: embedded output_tokens can be any
    integer, whose decimal text might accidentally contain with_retry_async transient patterns (429/500/
    502/503/504). Without explicit nonretryability, backend
    @with_retry_async() decorators could resend the same inevitably truncated request.

    ``provider_id`` is resolution-layer registry provider_id (``custom-<id>`` for custom providers); ``custom_model``
    identifies custom-provider models. Both are supplied by :class:`lib.backends.text_generator.TextGenerator`,
    letting UI suggest registering maximum output length or switching text models. Both default absent in backend exceptions.
    """

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        output_tokens: int | None = None,
        provider_id: str | None = None,
        custom_model: bool = False,
    ):
        self.provider = provider
        self.model = model
        self.output_tokens = output_tokens
        self.provider_id = provider_id
        self.custom_model = custom_model
        detail = f"在 output_tokens={output_tokens} 处" if output_tokens is not None else ""
        super().__init__(
            f"{provider}/{model} 的输出{detail}被模型输出上限截断，内容不完整。请改用输出能力更高的文本模型。"
        )


class StructuredOutputExhaustedError(NonRetryableError):
    """Structured-output fallback chains finished without schema-compliant content.

    Backends converge terminal insufficient-structured-output failures here: exhausted Instructor validation retries
    and exhausted Gemini prompt-injection fallback both raise this rather than internal exceptions (``InstructorRetryException``,
    raw ``ValueError``), allowing actionable user explanations instead of exposing SDK exceptions verbatim.

    Inherit NonRetryableError for the same reason as :class:`TextOutputTruncatedError`: embedded failure reasons
    contain dynamic model text that may match with_retry_async transient patterns. Retrying is also semantically
    wrong: fallback chains already attempted all applicable tiers and within-tier retries.
    """

    def __init__(self, *, provider: str, model: str, reason: str):
        self.provider = provider
        self.model = model
        self.reason = reason
        super().__init__(f"{provider}/{model} 的结构化输出能力不足：{reason}。请更换文本模型或供应商后重试。")


# Raw-model-output diagnostic log limit: enough to reveal structural issues (prose vs JSON, object vs array,
# missing fields), without logging entire generated content.
RAW_OUTPUT_LOG_LIMIT = 500


def truncate_for_log(text: str | None, limit: int = RAW_OUTPUT_LOG_LIMIT) -> str:
    """Limit raw model output to a loggable length.

    Structured-output fallback and parsing rejection depend on what models actually produced; summaries alone (field paths/error types)
    cannot reconstruct failures, so deliberately retain raw excerpts here. This differs from :func:`summarize_validation_error`:
    that high-frequency path runs on every validation failure, while this function serves warning-level fallback/rejection branches only.
    """
    if not text:
        return "<空>"
    if len(text) > limit:
        return f"{text[:limit]}…（截断，共 {len(text)} 字符）"
    return text


_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


def strip_leading_think_block(text: str) -> str:
    """Strip a leading ``<think>…</think>`` thinking block, returning the following body.

    Thinking models over OpenAI-compatible APIs (such as MiniMax M2 / M3) embed reasoning in ``content`` by default,
    inside ``<think>`` tags, followed by the answer after ``</think>``; structured JSON follows too.
    Recognize one leading block only: preserve unchanged if not at the start or missing its closing tag (usually truncation), without guessing.
    """
    body = text.lstrip()
    if not body.startswith(_THINK_OPEN):
        return text
    end = body.find(_THINK_CLOSE, len(_THINK_OPEN))
    if end < 0:
        return text
    return body[end + len(_THINK_CLOSE) :].lstrip()


def strip_json_code_fence(text: str) -> str:
    """Strip Markdown JSON fences surrounding the entire output, returning the enclosed JSON text.

    Handle paired ``json`` / unlabelled fences only; normalize surrounding body whitespace and missing final newlines.
    Preserve unfenced content unchanged to avoid altering ordinary text or truncated JSON.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text

    lines = stripped.splitlines()
    if len(lines) < 2 or lines[-1].strip() != "```":
        return text

    opening = lines[0].strip().lower()
    if opening not in {"```", "```json"}:
        return text

    return "\n".join(lines[1:-1]).strip()


# Text output ceiling: a nonbinding safety valve against pathological runaway, never a functional budget. Normal episode planning, script generation,
# and drama script_plan normalization volumes are naturally bounded by their schemas/content and never approach this high value.
# Only pathologically large batches or user-selected low-output-capacity models hit it. All three share this constant; adjust only
# here (docs/adr/0044).
DEFAULT_MAX_OUTPUT_TOKENS = 64000


def check_truncation(
    finish_reason: str | None,
    *,
    provider: str,
    model: str,
    structured: bool,
    output_tokens: int | None = None,
    truncation_values: tuple[str, ...] = ("length", "MAX_TOKENS", "max_tokens"),
) -> bool:
    """Detect truncation: structured output (``structured=True``) raises hard errors; free text warns and returns True.

    Callers supply ``structured`` from whether the original ``generate()`` request has response_schema:
    the criterion is generation intent, not whether this specific wire call carries schema parameters. Internal fallback
    paths temporarily clearing response_schema for strategies such as prompt-injected schemas must still
    explicitly pass ``structured=True``.

    Truncation raises :class:`TextOutputTruncatedError`, naturally short-circuiting caller validation retry loops:
    resending the same inevitably truncated request is pointless (docs/adr/0044).
    """
    truncated = warn_if_truncated(
        finish_reason,
        provider=provider,
        model=model,
        output_tokens=output_tokens,
        truncation_values=truncation_values,
    )
    if truncated and structured:
        raise TextOutputTruncatedError(provider=provider, model=model, output_tokens=output_tokens)
    return truncated


class TextCapability(StrEnum):
    """Text backend capability enum."""

    TEXT_GENERATION = "text_generation"
    STRUCTURED_OUTPUT = "structured_output"
    VISION = "vision"


class TextTaskType(StrEnum):
    """Text-generation task types."""

    SCRIPT = "script"
    OVERVIEW = "overview"
    STYLE_ANALYSIS = "style"


class TextTaskTier(StrEnum):
    """Text-task tiers.

    Call sites have fixed code-level assignments (TEXT_TASK_TIERS); users configure which text backend each tier uses,
    not the mapping itself (docs/adr/0051).
    """

    SIMPLE = "simple"
    COMPLEX = "complex"


# Sole TextTaskType → tier mapping. Explicitly assign every new task type here;
# missing assignments fail at import, backed by exhaustive tests.
TEXT_TASK_TIERS: dict[TextTaskType, TextTaskTier] = {
    TextTaskType.SCRIPT: TextTaskTier.COMPLEX,
    TextTaskType.OVERVIEW: TextTaskTier.SIMPLE,
    TextTaskType.STYLE_ANALYSIS: TextTaskTier.SIMPLE,
}

if _missing := set(TextTaskType) - set(TEXT_TASK_TIERS):
    raise RuntimeError(f"TextTaskType 成员未归档到 TEXT_TASK_TIERS: {sorted(m.value for m in _missing)}")

# Tasks requiring image input (vision). Reject unsupported resolved models instead of silently switching models.
VISION_REQUIRED_TASKS: frozenset[TextTaskType] = frozenset({TextTaskType.STYLE_ANALYSIS})


@dataclass
class ImageInput:
    """Image input for vision."""

    path: Path | None = None
    url: str | None = None


@dataclass
class TextGenerationRequest:
    """Common text-generation request. Backends ignore unsupported fields."""

    prompt: str
    response_schema: dict | type | None = None
    images: list[ImageInput] | None = None
    system_prompt: str | None = None
    max_output_tokens: int | None = None


@dataclass
class TextGenerationResult:
    """Common text-generation result.

    ``truncated`` marks free-text output truncated by model output limits (structured-output truncation directly raises
    :class:`TextOutputTruncatedError` instead of returning this flag).

    Usage follows the usage contract (docs/adr/0100): ``input_tokens`` is total input, including cache reads/writes; ``cache_read_tokens``,
    ``cache_write_5m_tokens``, and ``cache_write_1h_tokens`` are its cache-read/two cache-write portions. Uncached input is total minus
    these three. Each backend resolves vendor usage semantics (whether cache counts in input) before populating fields.

    For multiple fallback-chain requests, these measurements sum all calls; ``billed_requests`` lists each call's measurements so billing
    chooses context tiers per request. For a single request it is empty; read uniformly through :meth:`request_tokens`.
    """

    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    truncated: bool = False
    cache_read_tokens: int | None = None
    cache_write_5m_tokens: int | None = None
    cache_write_1h_tokens: int | None = None
    billed_requests: list[RequestTokens] = field(default_factory=list)

    def request_tokens(self) -> list[RequestTokens]:
        """Measurements for each billed request; empty when all measurements are absent (untracked)."""
        if self.billed_requests:
            return list(self.billed_requests)
        own = RequestTokens(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_read_tokens=self.cache_read_tokens,
            cache_write_5m_tokens=self.cache_write_5m_tokens,
            cache_write_1h_tokens=self.cache_write_1h_tokens,
        )
        return [] if own == RequestTokens() else [own]


@dataclass(frozen=True)
class RequestTokens:
    """Token measurements for one provider request, with the same field semantics as :class:`TextGenerationResult`."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_5m_tokens: int | None = None
    cache_write_1h_tokens: int | None = None


def merge_billed_tokens(kept: int | None, discarded: int | None) -> int | None:
    """Merge tokens from a discarded fallback-path call into retained result measurements.

    Any pre-fallback call receiving HTTP 200 was billed; omitting it systematically undercounts usage/cost.
    Sum only if at least one side has measurements; keep None when both are None (untracked), never collapse into literal zero tokens:
    untracked and zero consumption differ for cost accounting.
    """
    if kept is None and discarded is None:
        return None
    return (kept or 0) + (discarded or 0)


def chat_completion_result(
    *, text: str, provider: str, model: str, usage: object | None, truncated: bool = False
) -> TextGenerationResult:
    """Populate result measurements from Chat Completions usage.

    ``prompt_tokens`` is total input, with cache hits in ``prompt_tokens_details.cached_tokens``; ``completion_tokens``
    includes reasoning tokens. Missing usage leaves measurements ``None``.
    """
    details = getattr(usage, "prompt_tokens_details", None)
    return TextGenerationResult(
        text=text,
        provider=provider,
        model=model,
        input_tokens=getattr(usage, "prompt_tokens", None),
        output_tokens=getattr(usage, "completion_tokens", None),
        truncated=truncated,
        cache_read_tokens=getattr(details, "cached_tokens", None),
    )


def merge_billed_usage(kept: TextGenerationResult, discarded: TextGenerationResult) -> None:
    """Merge all token measurements from discarded fallback calls into retained results (mutating ``kept``).

    Sum measurements and separately record each call in ``billed_requests``: context tiers use each request's total input, never the aggregate.
    """
    kept.billed_requests = [*kept.request_tokens(), *discarded.request_tokens()]
    kept.input_tokens = merge_billed_tokens(kept.input_tokens, discarded.input_tokens)
    kept.output_tokens = merge_billed_tokens(kept.output_tokens, discarded.output_tokens)
    kept.cache_read_tokens = merge_billed_tokens(kept.cache_read_tokens, discarded.cache_read_tokens)
    kept.cache_write_5m_tokens = merge_billed_tokens(kept.cache_write_5m_tokens, discarded.cache_write_5m_tokens)
    kept.cache_write_1h_tokens = merge_billed_tokens(kept.cache_write_1h_tokens, discarded.cache_write_1h_tokens)


class _ModelFacingJsonSchema(GenerateJsonSchema):
    """Generate model-facing schemas only: class docstrings never enter ``description``.

    Class docstrings explain implementations to maintainers (ADR numbers, migration policies, etc.). Pydantic normally renders them
    as ``description`` on model/enum definitions, sending them with structured requests; explicit field ``description`` values are model-facing and preserved.

    Pydantic inserts enum/RootModel docstrings after type methods return, so do not intercept inside ``model_schema`` and similar methods.
    Instead collect class docstrings during generation, then strip identical descriptions from top-level and ``$defs`` definitions afterward;
    preserve differing descriptions explicitly supplied through ``json_schema_extra`` and similar mechanisms.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._class_docstrings: set[str] = set()

    def generate_inner(self, schema: Any) -> JsonSchemaValue:
        cls = schema.get("cls")
        if isinstance(cls, type) and cls.__doc__:
            self._class_docstrings.add(inspect.cleandoc(cls.__doc__))
        return super().generate_inner(schema)

    def handle_ref_overrides(self, json_schema: JsonSchemaValue) -> JsonSchemaValue:
        # Pydantic removes field descriptions identical to referenced definitions; since definition docstrings are stripped afterward, retain field descriptions.
        reduced = super().handle_ref_overrides(json_schema)
        if "description" in json_schema and "description" not in reduced:
            reduced = {**reduced, "description": json_schema["description"]}
        return reduced

    def generate(self, schema: Any, mode: JsonSchemaMode = "validation") -> JsonSchemaValue:
        json_schema = super().generate(schema, mode)
        for node in (json_schema, *json_schema.get("$defs", {}).values()):
            if node.get("description") in self._class_docstrings:
                del node["description"]
        return json_schema


def model_facing_response_model[M: BaseModel](model: type[M]) -> type[M]:
    """Subclass ``model`` for libraries generating their own schemas without :func:`resolve_schema` (Instructor, xai_sdk).

    The subclass has no docstring; ``model_json_schema`` defaults to :class:`_ModelFacingJsonSchema`, so generated schemas
    and tool descriptions contain no class docstrings. Fields, validation, and serialization reuse ``model``.
    """

    def model_json_schema(cls: type[M], *args: Any, **kwargs: Any) -> dict[str, Any]:
        # Subclasses add/change no fields, retaining model schemas; generator strips class docstrings.
        kwargs.setdefault("schema_generator", _ModelFacingJsonSchema)
        return model.model_json_schema(*args, **kwargs)

    namespace = {
        "__doc__": None,
        "__module__": model.__module__,
        "__qualname__": model.__qualname__,
        "model_json_schema": classmethod(model_json_schema),
    }
    return type(model)(model.__name__, (model,), namespace)


def resolve_schema(schema: dict | type[BaseModel]) -> dict:
    """Convert response_schema into a model-facing pure JSON Schema dict without $ref.

    - BaseModel subclass: generate schemas without class docstrings (see :class:`_ModelFacingJsonSchema`), then inline $ref
    - dict: directly inline $ref if present
    """
    if isinstance(schema, type):
        schema_dict: dict = schema.model_json_schema(schema_generator=_ModelFacingJsonSchema)
    else:
        schema_dict = schema

    defs = schema_dict.get("$defs", {})
    if not defs:
        return schema_dict

    def _inline(obj: Any, visited_refs: frozenset[str] = frozenset()) -> Any:
        if isinstance(obj, dict):
            if "$ref" in obj:
                ref_name = obj["$ref"].split("/")[-1]
                if ref_name in visited_refs:
                    raise ValueError(f"检测到 schema 中的循环引用: {ref_name}")
                resolved = _inline(defs[ref_name], visited_refs | {ref_name})
                extra = {k: v for k, v in obj.items() if k != "$ref"}
                return {**resolved, **extra} if extra else resolved
            return {k: _inline(v, visited_refs) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_inline(item, visited_refs) for item in obj]
        return obj

    result: dict = _inline(schema_dict)
    result.pop("$defs", None)
    return result


def is_valid_json(text: str) -> bool:
    """Determine whether a string is valid JSON.

    Some native structured channels (commonly OpenAI-compatible proxies/custom providers) silently ignore structured-output
    parameters and return plain text/markdown, requiring fallback detection here.
    """
    if not text or not text.strip():
        return False
    try:
        json.loads(text)
        return True
    except (ValueError, TypeError):
        return False


def summarize_validation_error(exc: ValidationError) -> str:
    """Reduce ValidationError to brief field-location summaries.

    Include field paths (loc) and error counts only, **never raw model input values**: these may be large and expose
    generated content in logs (potentially through diagnostic bundles), also inflating individual logs to several KB.
    Strip input at source with include_input=False, preventing raw values entering error dicts (defense in depth).
    """
    locs = [
        ".".join(str(part) for part in err.get("loc", ())) or "<root>" for err in exc.errors(include_input=False)[:5]
    ]
    suffix = "…" if exc.error_count() > 5 else ""
    return f"{exc.error_count()} 处字段不符（{', '.join(locs)}{suffix}）"


def structured_fallback_reason(text: str, response_schema: dict | type | None, *, strict: bool = True) -> str | None:
    """Determine whether a native structured HTTP 200 response needs validated fallback.

    Pure function independent of provider/client/native API shape, shared by all text backends. Return a fallback reason
    for logs, or None if native output is usable. Two triggers:

    1. Non-JSON: providers silently ignore structured parameters, returning plain text/markdown.
    2. Valid JSON violating schemas: providers accept structured parameters without enforcing schemas (common with proxies /
       nonoriginal models), giving invalid enums or missing required fields. Accepting these would leak into downstream
       Pydantic validation or rendering and raise raw ValidationError.

    Callers align ``strict`` with native request declarations: strict-declaring backends use
    strict=True (coercible but type-mismatched values, such as "30" for int, still indicate unenforced schemas);
    others use strict=False (tolerate coercible values; fall back only for truly schema-unsatisfying output),
    avoiding unnecessary billed fallback calls for valid provider-accepted responses.

    Only Pydantic models support schema validation; dict schemas lack corresponding models, preserving existing
    JSON-validity-only checks without tightening them. None response_schema (no structured intent) requires no fallback.
    """
    if response_schema is None:
        # Plain text has no structured intent: accept native output, never trigger non-JSON fallback.
        return None
    if isinstance(response_schema, type) and issubclass(response_schema, BaseModel):
        # One model_validate_json parse covers both non-JSON and schema violations.
        try:
            response_schema.model_validate_json(text, strict=strict)
        except ValidationError as exc:
            return f"返回内容不满足 response_schema（供应商可能未强制 schema）：{summarize_validation_error(exc)}"
        return None
    if not is_valid_json(text):
        return "返回非 JSON 内容（供应商可能未支持结构化输出）"
    return None


# Prompt-injection fallback maximum calls: initial schema injection + one validation-error-feedback retry.
# Every call is billed; additional attempts have negligible value after two schema-injected failures on a model/proxy.
PROMPT_JSON_FALLBACK_MAX_ATTEMPTS = 2


async def prompt_json_fallback(
    request: TextGenerationRequest,
    generate_once: Callable[[TextGenerationRequest], Awaitable[TextGenerationResult]],
    *,
    provider: str,
    model: str,
) -> TextGenerationResult:
    """Prompt-injection fallback: inject schemas, repeat plain-text calls, strip fences and validate, retrying failures with feedback.

    When providers reject or silently ignore wire-level structured parameters, prompt schemas are the sole strategy independent
    of wire parameters. ``generate_once`` is the backend's single native call (including logging, truncation handling, and transient
    retries), receiving requests with cleared ``response_schema``; they remain structured-output attempts requiring
    structured truncation handling. Return normalized JSON (model_dump_json) after validation, ensuring downstream parsing succeeds.
    """
    assert request.response_schema is not None  # Guaranteed by the caller (generate revalidation branch).
    schema_dict = resolve_schema(request.response_schema)
    schema_model = request.response_schema if isinstance(request.response_schema, type) else None

    base_prompt = (
        f"{request.prompt}\n\n"
        "仅输出一个符合以下 JSON Schema 的 JSON 对象，"
        "不要输出任何解释、前后缀或 markdown 代码栅栏：\n"
        f"{json.dumps(schema_dict, ensure_ascii=False)}"
    )
    feedback = ""
    billed = TextGenerationResult(text="", provider=provider, model=model)
    last_reason = ""
    for _attempt in range(PROMPT_JSON_FALLBACK_MAX_ATTEMPTS):
        fb_request = replace(request, prompt=base_prompt + feedback, response_schema=None)
        fb_result = await generate_once(fb_request)
        merge_billed_usage(billed, fb_result)

        text = strip_json_code_fences(fb_result.text)
        normalized, last_reason = _validate_fallback_text(text, schema_model)
        if normalized is not None:
            billed.text = normalized
            return billed
        feedback = f"\n\n上次输出不符合要求（{last_reason}），请严格按上述 JSON Schema 重新输出纯 JSON。"
        _logger.warning(
            "prompt 注入降级输出校验未通过（%s），带反馈重试；模型原始输出：%s",
            last_reason,
            truncate_for_log(text),
        )

    raise StructuredOutputExhaustedError(
        provider=provider,
        model=model,
        reason=f"prompt 注入降级 {PROMPT_JSON_FALLBACK_MAX_ATTEMPTS} 次尝试后输出仍不合规（{last_reason}）",
    )


def _validate_fallback_text(text: str, schema_model: type | None) -> tuple[str | None, str]:
    """Validate and normalize fallback output. Return (normalized JSON text, "") or (None, failure reason).

    Pydantic models get full schema validation and model_dump_json normalization; dict schemas lack corresponding models,
    preserving valid-JSON-only acceptance, aligned with structured_fallback_reason for dict schemas.
    """
    if schema_model is not None and issubclass(schema_model, BaseModel):
        try:
            return schema_model.model_validate_json(text).model_dump_json(), ""
        except ValidationError as exc:
            return None, summarize_validation_error(exc)
    if is_valid_json(text):
        return text, ""
    return None, "输出不是合法 JSON"


class TextBackend(Protocol):
    """Text-generation backend protocol."""

    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def capabilities(self) -> set[TextCapability]: ...

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult: ...
