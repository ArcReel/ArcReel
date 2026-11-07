"""Response validation: report path matches/final interpretations from real provider responses at zero cost.

Path evaluation shares runtime :func:`normalize_extract_spec`; final verdicts directly call runtime
media-specific interpreters: video :func:`extract_provider_state`, images
:func:`extract_image_state`. Avoiding duplicated rules is what lets validation substitute for paid calls.
Reports also populate per-stage extraction in connection-test responses.

``image_b64`` extracts entire image base64, but reports expose decoded byte counts only (``None`` if undecodable),
never original payloads.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from arcreel_market_core.endpoint_definition import (
    JsonPathEvaluationError,
    definition_media_type,
    extract_value,
    normalize_extract_spec,
)
from arcreel_market_core.video_backend_contract import ProviderJobStatus
from lib.custom_provider.declarative_backend import (
    DeclarativeRuntimeError,
    JobState,
    ProviderState,
    StateReader,
    extract_provider_state,
    text_or_none,
)
from lib.custom_provider.declarative_image_backend import ImageJobState, decode_image_b64, extract_image_state

from .errors import EndpointTestDefinitionError

#: Three validatable sections; ``result`` exists only with declared secondary retrieval.
STAGES = ("submit", "poll", "result")

#: Media → runtime response interpreters.
_STATE_READERS: dict[str, StateReader[Any]] = {
    "video": extract_provider_state,
    "image": extract_image_state,
}


@dataclass(frozen=True)
class PathAttempt:
    """One priority-array path evaluation."""

    path: str
    json_decode: bool
    matched: bool
    value: object | None


@dataclass(frozen=True)
class FieldExtraction:
    """Complete extract-key reports: attempted paths + final values."""

    key: str
    attempts: tuple[PathAttempt, ...]
    value: object | None


@dataclass(frozen=True)
class StageReport:
    """Section validation results, with runtime ``status`` / ``verdict``."""

    stage: str
    fields: tuple[FieldExtraction, ...]
    task_id: str | None
    raw_status: object | None
    status: str | None
    video_url: str | None
    image_url: str | None
    #: Decoded bytes from ``image_b64``; ``None`` without matches/decodable images.
    image_bytes: int | None
    error: str | None
    result_id: str | None
    duration_seconds: int | None


def parse_response_body(raw: object) -> object:
    """Normalize pasted responses: parse strings as JSON first, otherwise validate original strings."""
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def check_response(definition: Mapping[str, Any], stage: str, response_body: object) -> StageReport:
    """Validate section extraction paths/status mapping; definitions must pass shared validation.

    Raises:
        EndpointTestDefinitionError: Missing sections or rules unevaluable on supplied responses.
    """
    section = definition.get(stage)
    if stage not in STAGES or not isinstance(section, Mapping):
        raise EndpointTestDefinitionError.from_render_failure(stage, f"definition has no {stage} section")
    extract = section["extract"]
    fields = tuple(_field_reports(extract, response_body))
    values = {field.key: field.value for field in fields}
    # Submission has no readable status: any 2xx means accepted, determined by extractable task_id. Runtime treats secondary retrieval
    # as already successful because requests occur only after successful polling.
    try:
        state = _runtime_state(definition, stage, extract, response_body)
    except DeclarativeRuntimeError as exc:
        raise EndpointTestDefinitionError.from_render_failure(f"{stage}.extract", str(exc)) from exc
    video = state if isinstance(state, ProviderState) else None
    image = state if isinstance(state, ImageJobState) else None
    return StageReport(
        stage=stage,
        fields=fields,
        task_id=_task_id(values.get("task_id")) if stage == "submit" else None,
        raw_status=values.get("status"),
        status=state.status.value if state else None,
        video_url=video.video_url if video else None,
        image_url=image.image_url if image else None,
        image_bytes=_image_bytes(image.image_b64) if image and image.image_b64 else None,
        error=state.error if state else text_or_none(values.get("error")),
        result_id=state.result_id if state else None,
        duration_seconds=video.duration_seconds if video else None,
    )


def stage_report_payload(report: StageReport) -> dict[str, Any]:
    """Flatten section reports into shared response-validation/connection-test API contracts."""
    return {
        "stage": report.stage,
        "fields": [
            {
                "key": field_report.key,
                "value": field_report.value,
                "attempts": [
                    {
                        "path": attempt.path,
                        "json_decode": attempt.json_decode,
                        "matched": attempt.matched,
                        "value": attempt.value,
                    }
                    for attempt in field_report.attempts
                ],
            }
            for field_report in report.fields
        ],
        "task_id": report.task_id,
        "raw_status": report.raw_status,
        "status": report.status,
        "video_url": report.video_url,
        "image_url": report.image_url,
        "image_bytes": report.image_bytes,
        "error": report.error,
        "result_id": report.result_id,
        "duration_seconds": report.duration_seconds,
    }


def _runtime_state(
    definition: Mapping[str, Any], stage: str, extract: Mapping[str, Any], body: object
) -> JobState | None:
    if stage == "submit":
        return None
    return _STATE_READERS[definition_media_type(definition)](
        body,
        extract,
        status_map=definition.get("status_map"),
        status=ProviderJobStatus.SUCCEEDED if stage == "result" else None,
    )


def _task_id(value: object | None) -> str | None:
    """Runtime-shared semantics: arrays/objects never match, scalars stringify."""
    if isinstance(value, (list, dict)):
        return None
    return text_or_none(value)


def _image_bytes(text: str) -> int | None:
    try:
        return len(decode_image_b64(text))
    except ValueError:
        return None


def _image_summary(value: object | None) -> object | None:
    """Replace matched ``image_b64`` with byte-count summaries."""
    if value is None:
        return None
    text = text_or_none(value)
    return {"image_bytes": _image_bytes(text) if text else None}


def _summarized(report: FieldExtraction) -> FieldExtraction:
    return replace(
        report,
        attempts=tuple(replace(attempt, value=_image_summary(attempt.value)) for attempt in report.attempts),
        value=_image_summary(report.value),
    )


def _field_reports(extract: Mapping[str, Any], body: object):
    for key, spec in extract.items():
        if key == "usage" and isinstance(spec, Mapping):
            for usage_key, usage_spec in spec.items():
                yield _field_report(f"usage.{usage_key}", usage_spec, body)
            continue
        report = _field_report(key, spec, body)
        yield _summarized(report) if key == "image_b64" else report


def _field_report(key: str, spec: object, body: object) -> FieldExtraction:
    try:
        paths, accept = normalize_extract_spec(spec)
    except (TypeError, ValueError) as exc:
        raise EndpointTestDefinitionError.from_render_failure(key, str(exc)) from exc
    attempts: list[PathAttempt] = []
    value: object | None = None
    for item in paths:
        try:
            # Reuse extract_value for every path: priority semantics/json_decode suffixes/accept filtering have one implementation.
            hit = extract_value({"paths": [item], "accept": accept}, body)
        except JsonPathEvaluationError as exc:
            raise EndpointTestDefinitionError.from_render_failure(key, exc) from exc
        except (TypeError, ValueError) as exc:
            raise EndpointTestDefinitionError.from_render_failure(key, str(exc)) from exc
        attempts.append(
            PathAttempt(
                path=str(item["path"]) if isinstance(item, Mapping) else str(item),
                json_decode=isinstance(item, Mapping),
                matched=hit is not None,
                value=hit,
            )
        )
        if hit is not None and value is None:
            value = hit
    return FieldExtraction(key=key, attempts=tuple(attempts), value=value)
