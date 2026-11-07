"""Structured task-failure encoding for the generation worker.

The worker (lib layer) stores a machine-stable reason in ``Task.error_message``
instead of locale-locked text: a ``[code]`` token optionally followed by a JSON
object of parameters. The tasks API serialization path renders that reason via
the request Translator on read, so the same failed task shows zh/en/vi text per
``Accept-Language``.

Anything that is not a recognised ``[code]`` form — raw provider exception text
(``str(exc)``), or legacy rows written before this format — passes through
verbatim, so no stored reason is ever lost or mis-parsed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any, TypeGuard

from lib.custom_provider.comfyui.artifacts import expected_suffixes_text
from lib.i18n import render_generation_input_error, render_message
from lib.i18n.display_names import DisplayNames

# Backend capability rejections (``ImageCapabilityError`` / ``VideoCapabilityError`` /
# ``ReferencePayloadFloorError``). Their ``.code`` is already an ``errors`` catalog key,
# so the mapping below is identity — no prefix indirection. Enumerated rather than
# derived so an unregistered code fails fast; ``tests/integration/lib/generation/test_task_failure_capability.py``
# AST-scans the raise sites and fails CI when a new code is added without registering it.
CAPABILITY_FAILURE_CODES: frozenset[str] = frozenset(
    {
        "image_capability_missing_i2i",
        "image_capability_missing_t2i",
        "image_dashscope_4k_t2i_only",
        "image_endpoint_mismatch_no_i2i",
        "image_endpoint_mismatch_no_t2i",
        "image_reference_images_exceeded",
        "image_reference_images_unreadable",
        "image_seedream_edits_unavailable",
        "ref_payload_floor_exceeded",
        "video_capability_missing_t2v",
        "video_duration_invalid",
        "video_duration_not_supported",
        "video_end_image_requires_start_image",
        "video_end_image_unreadable",
        "video_first_frame_unsupported",
        "video_last_frame_requires_pro",
        "video_last_frame_unsupported",
        "video_prompt_too_long",
        "video_reference_images_exceeded",
        "video_reference_images_required",
        "video_reference_images_unreadable",
        "video_reference_images_unsupported",
        "video_reference_images_with_frames_unsupported",
        "video_reference_resolution_unsupported",
        "video_reference_audio_duration_exceeded",
        "video_reference_audio_exceeded",
        "video_reference_audio_format_unsupported",
        "video_reference_audio_slots_insufficient",
        "video_reference_audio_unreadable",
        "video_reference_audio_unsupported",
        "video_reference_route_audio_unsupported",
        "video_aspect_ratio_not_supported",
        "video_audio_switch_not_supported",
        "video_resolution_not_supported",
        "video_start_image_unreadable",
    }
)

# Blocking codes emitted by ``ReferenceUnitRequestProjector``. The executor raises the
# canonical ``ProjectionProblem`` through ``ReferenceProjectionBlockedError``; this
# registry only authorizes those same codes for the persisted task-failure envelope.
REFERENCE_PROJECTION_FAILURE_CODES: frozenset[str] = frozenset(
    {
        "reference_asset_missing",
        "reference_asset_unregistered",
        "reference_capability_changed",
        "reference_capability_unavailable",
        "needs_replan",
        "reference_duration_confirmation_required",
        "reference_supported_durations_incompatible",
        "reference_supported_durations_invalid",
        "reference_supported_durations_missing",
        "video_audio_switch_not_supported",
        "video_capability_missing_i2v",
        "video_capability_missing_r2v",
        "video_capability_reference_unavailable",
    }
)

# First gap codes raised by the executor through server translation when generation inputs are refused
# (``lib.artifacts.generation_input.InputRefused``); reference-video projection may report the same gap, so union overlapping registrations.
GENERATION_INPUT_FAILURE_CODES: frozenset[str] = frozenset(
    {
        "asset_description_required",
        "asset_original_missing",
        "derivative_description_required",
        "derivative_owner_sheet_missing",
        "reference_asset_missing",
        "reference_asset_unregistered",
        "script_prompt_pending",
    }
)

# Storyboard-route issue codes when video request facts (``lib.generation.video_request_facts``) fail evaluation: executors block through
# ``VideoRequestFactsError`` and persist original codes, matching preflight. Reference-route and bucket-capability gate codes are registered above.
VIDEO_REQUEST_FACTS_FAILURE_CODES: frozenset[str] = frozenset(
    {
        "video_audio_switch_not_supported",
        "video_capability_unavailable",
        "video_supported_durations_incompatible",
        "video_supported_durations_invalid",
        "video_supported_durations_missing",
    }
)

# Generation provider materials: applicable associations unavailable or ingestion failed (``lib.asset_library.generation_inputs``), or providers
# reject unapproved input images (``lib.backends.provider_assets``). Include image numbers/names when the rejected image is identifiable.
PROVIDER_ASSET_FAILURE_CODES: frozenset[str] = frozenset(
    {
        "provider_asset_link_failed",
        "provider_asset_link_processing",
        "provider_input_asset_rejected_at",
        "provider_input_image_rejected",
        "provider_input_image_rejected_at",
    }
)

# Stable failure code -> i18n errors key. The code is agent-facing and persisted
# in the DB; the key resolves to zh/en/vi templates rendered at read time.
FAILURE_CODE_KEYS: dict[str, str] = {
    **{code: code for code in CAPABILITY_FAILURE_CODES},
    **{code: code for code in REFERENCE_PROJECTION_FAILURE_CODES},
    **{code: code for code in GENERATION_INPUT_FAILURE_CODES},
    **{code: code for code in VIDEO_REQUEST_FACTS_FAILURE_CODES},
    **{code: code for code in PROVIDER_ASSET_FAILURE_CODES},
    "provider_unsupported_media": "task_fail_provider_unsupported_media",
    # Built-in provider model references absent from catalogs (renamed or never listed): preserve references for users to reselect.
    "model_not_in_provider_catalog": "model_not_in_provider_catalog",
    # Referenced models discontinued: include dates and official successors; failure_message_key omits sentences for unregistered details.
    "model_discontinued": "model_discontinued",
    # Deterministic upstream 4xx rejection. provider_reason in params is redacted, truncated upstream text, intentionally excluded from
    # translation templates: never translate it; readers display it unchanged in a separate field.
    "provider_rejected": "task_fail_provider_rejected",
    "dispatch_provider_requeue_failed": "task_fail_dispatch_provider_requeue_failed",
    # Projects deleted during execution: tasks abandon disk writes; see lib.project.task_project_claim.
    "project_deleted_during_task": "task_fail_project_deleted_during_task",
    "restart_lost_image": "task_fail_restart_lost_image",
    "restart_lost_audio": "task_fail_restart_lost_audio",
    "restart_lost_text": "task_fail_restart_lost_text",
    "restart_lost_render": "task_fail_restart_lost_render",
    "restart_lost_no_job_id": "task_fail_restart_lost_no_job_id",
    "restart_lost_resume_no_job_id": "task_fail_restart_lost_resume_no_job_id",
    "resume_unsupported_provider": "task_fail_resume_unsupported_provider",
    "resume_unsupported_capacity_zero": "task_fail_resume_unsupported_capacity_zero",
    "resume_unsupported_detail": "task_fail_resume_unsupported_detail",
    "resume_expired_detail": "task_fail_resume_expired_detail",
    "resume_endpoint_changed_detail": "task_fail_resume_endpoint_changed_detail",
    "declarative_template_render_failed": "task_fail_declarative_template_render_failed",
    "declarative_response_extract_failed": "task_fail_declarative_response_extract_failed",
    "declarative_image_save_failed": "task_fail_declarative_image_save_failed",
    # Image-edit failure from insufficient references or first/last frames: cascades reach artifact nodes because this workflow's output depends on that image.
    "comfyui_image_drop_unsupported": "task_fail_comfyui_image_drop_unsupported",
    "comfyui_upload_failed": "task_fail_comfyui_upload_failed",
    "comfyui_node_errors": "task_fail_comfyui_node_errors",
    "comfyui_job_lost": "task_fail_comfyui_job_lost",
    "comfyui_execution_error": "task_fail_comfyui_execution_error",
    "comfyui_interrupted": "task_fail_comfyui_interrupted",
    "comfyui_output_missing": "task_fail_comfyui_output_missing",
    # Successful image-provider responses without images (usually safety filtering): see ImageOutputMissingError.
    "image_output_missing": "task_fail_image_output_missing",
    # Returned image formats cannot convert to bitmaps (for example vector SVG): see ImageOutputFormatError.
    "image_output_format_unsupported": "task_fail_image_output_format_unsupported",
    "comfyui_output_type_mismatch": "task_fail_comfyui_output_type_mismatch",
    "comfyui_output_container_mismatch": "task_fail_comfyui_output_container_mismatch",
    "artifact_download_failed": "task_fail_artifact_download_failed",
    "restart_lost_checkpoint_no_job_id": "task_fail_restart_lost_checkpoint_no_job_id",
    "execution_identity_unrecoverable": "task_fail_execution_identity_unrecoverable",
    # ScriptEditError.key is already an errors.py key (see lib/script/script_editor.py), requiring no prefix indirection.
    "script_edit_error": "script_edit_error",
    "script_edit_items_not_list": "script_edit_items_not_list",
    "script_edit_unit_lists_invalid": "script_edit_unit_lists_invalid",
    "script_edit_generated_assets_invalid": "script_edit_generated_assets_invalid",
    # Cascade failures (TaskRepository._cascade_failed_queued): reason nests the dependency task's own failure reason
    # (possibly another structured encoding), recursively expanded during rendering; see render_failure.
    "cascade_blocked_dependency": "task_fail_cascade_blocked_dependency",
}

# A structured reason is ``[code]`` optionally followed by a single space and a
# JSON object of params. Anchored at both ends so legacy ``[restart_lost] 中文``
# (non-JSON tail) and arbitrary exception text never match. DOTALL keeps the JSON
# group matching even if a param value contains an escaped newline.
_STRUCTURED_RE = re.compile(r"^\[(\w+)\](?:[ ](\{.*\}))?$", re.DOTALL)

CASCADE_FAILURE_CODE = "cascade_blocked_dependency"

# Unwrapping limit for collapse_cascade_reason, solely a busy-loop defense.
_MAX_CASCADE_UNWRAP = 100


def encode_failure(code: str, /, **params: Any) -> str:
    """Encode a known failure code (+ params) into the stored machine string.

    ``[code]`` when there are no params, otherwise ``[code] {sorted-json}``.
    Raises ``KeyError`` for codes not declared in :data:`FAILURE_CODE_KEYS`, so a
    typo fails fast at the call site instead of silently storing an unrenderable
    reason.

    Param values are stringified when not JSON-native (``default=str``): capability
    codes carry whatever the caller rejected — e.g. ``video_duration_invalid`` echoes
    the raw payload value — and a failed task must still record a reason rather than
    have encoding raise inside the failure path.
    """
    if code not in FAILURE_CODE_KEYS:
        raise KeyError(f"unknown failure code: {code!r}")
    if params:
        return f"[{code}] {json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)}"
    return f"[{code}]"


def _as_shrinkable(value: Any) -> Any:
    """Replace container parameter values with their JSON text so trimming can narrow them.

    Truncating a container's string representation is safer than truncating a JSON literal, which leaves unclosed brackets. Values nested deeply enough for
    ``json.dumps`` to hit its recursion limit degrade to an ellipsis: trimming occurs on the persistence path, where escaping exceptions leave tasks stuck
    running.

    Change containers only: ``int`` / ``float`` / ``bool`` / ``None`` are native JSON scalars, cannot exceed the budget, and cannot
    be narrowed. Converting them to text adds quotes on reencoding (``42`` becomes ``"42"``, ``True`` becomes
    ``"true"``, ``None`` becomes ``"null"``), changing parameter types in persisted envelopes. Trimming processes all params
    together, so any parameter triggering it would also distort every scalar in the same failure.
    """
    if not isinstance(value, dict | list):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except RecursionError:
        return "…"


def _is_validation_message(value: Any) -> TypeGuard[dict[str, Any]]:
    """Return whether ``value`` is the locale-neutral nested diagnostic envelope."""
    return (
        isinstance(value, dict)
        and set(value) == {"key", "params"}
        and isinstance(value.get("key"), str)
        and isinstance(value.get("params"), dict)
    )


def collapse_cascade_reason(reason: str) -> str:
    """Collapse nested cascade reasons to their innermost root cause before cascade encoding.

    Wrapping each level makes strings grow nearly exponentially: each level reencodes the previous envelope into JSON, and escaping doubles its length.
    Around seven levels exceed the persistence budget; tail trimming cuts the inner envelope mid-JSON. The outer envelope still parses,
    but readers treat the incomplete inner one as plain text and embed it unchanged in localized copy.

    Intermediate levels carry only another equally blocked task ID. Users can act on the direct dependency and root cause;
    the outer envelope preserves the former in its own ``dependency_task_id``. Collapsed string length is independent of chain depth.
    """
    seen = 0
    while True:
        parsed = parse_failure(reason)
        if parsed is None or parsed[0] != CASCADE_FAILURE_CODE:
            return reason
        nested = parsed[1].get("reason")
        if not isinstance(nested, str):
            return reason
        reason = nested
        seen += 1
        if seen > _MAX_CASCADE_UNWRAP:
            # Normal chains never reach this depth (collapsed before exceeding budgets). Reaching it means malformed or artificial
            # deep nesting; stop locally and return the current level without looping on the persistence path.
            return reason


def bound_reason(reason: str, limit: int) -> str:
    """Trim ``reason`` to ``limit`` characters before cascade-failure encoding.

    Direct character truncation of valid structured ``[code] {params}`` strings cuts off the JSON tail, preventing
    :func:`render_failure` from parsing and leaking untranslated raw text into the UI (for example, ``resume_expired_detail``
    has a ``detail`` parameter from remote error responses that can span thousands of characters). Prefer trimming the longest
    string parameter while keeping the reencoded ``[code] {params}`` valid; fall back to raw character truncation
    for unstructured text or strings still over budget after trimming.

    Oversized nonstring parameters first degrade to their own JSON text for trimming: containers can also exceed the budget.
    ``video_duration_invalid`` echoes rejected raw caller configuration; an imported or manually edited
    ``project.json`` can supply a large array. Native JSON types bypass ``default=str``,
    so without string parameters to narrow, raw slicing would cut the envelope mid-JSON.

    Trimming converges iteratively on actual reencoded length rather than estimating the difference once: characters like ``"`` and ``\\``
    occupy two positions after JSON escaping. Excess encoded length differs from raw character count, so a single estimate can wrongly classify a narrowable parameter as unshrinkable.
    """
    if len(reason) <= limit:
        return reason
    match = _STRUCTURED_RE.match(reason)
    if match is None:
        return reason[:limit]
    code = match.group(1)
    if code not in FAILURE_CODE_KEYS:
        return reason[:limit]
    raw_params = match.group(2)
    if not raw_params:
        return reason[:limit]
    try:
        parsed = json.loads(raw_params)
    except (ValueError, RecursionError):
        # Malformed JSON and params nested deeply enough for ``json.loads`` itself to hit recursion limits. Trimming runs on the persistence
        # path, so escaping exceptions leave tasks stuck running. Treat both as unparseable and fall back to character trimming.
        return reason[:limit]
    if not isinstance(parsed, dict):
        return reason[:limit]
    params: dict[str, Any] = {key: _as_shrinkable(value) for key, value in parsed.items()}
    detail = parsed.get("detail")
    if _is_validation_message(detail):
        # ``detail`` must retain machine structure for readers to render the requested locale. Only degrade potentially oversized
        # containers in its params to trimmable text; string leaves share the same budget competition as top-level parameters.
        params["detail"] = {
            "key": detail["key"],
            "params": {key: _as_shrinkable(value) for key, value in detail["params"].items()},
        }

    gaps = parsed.get("gaps")
    kept_gaps: dict[str, dict[str, Any]] = {}
    if (
        code in GENERATION_INPUT_FAILURE_CODES
        and isinstance(gaps, list)
        and all(
            isinstance(gap, dict)
            and isinstance(gap.get("code"), str)
            and gap["code"] in GENERATION_INPUT_FAILURE_CODES
            and isinstance(gap.get("name"), str)
            and gap.get("asset_type") in (None, "character", "scene", "prop", "product")
            for gap in gaps
        )
    ):
        # Over budget, retain one gap per reason plus omitted counts; machine codes and grouping structure never undergo string trimming.
        for gap in gaps:
            kept_gaps.setdefault(gap["code"], {key: gap.get(key) for key in ("code", "asset_type", "name")})
        params["gaps"] = list(kept_gaps.values())
        omitted = parsed.get("gaps_omitted", 0)
        params["gaps_omitted"] = (omitted if isinstance(omitted, int) else 0) + len(gaps) - len(kept_gaps)

    shrinkable: list[tuple[dict[str, Any], str]] = [
        (params, key) for key, value in params.items() if isinstance(value, str)
    ]
    shrinkable.extend((gap, "name") for gap in kept_gaps.values())
    nested_detail = params.get("detail")
    if _is_validation_message(nested_detail):
        nested_params = nested_detail["params"]
        shrinkable.extend((nested_params, key) for key, value in nested_params.items() if isinstance(value, str))
    if not shrinkable:
        return reason[:limit]

    encoded = encode_failure(code, **params)
    while len(encoded) > limit:
        owner, longest_key = max(shrinkable, key=lambda item: len(item[0][item[1]]))
        current: str = owner[longest_key]
        if not current:
            # If emptying all string parameters still exceeds budget, even the envelope skeleton does not fit; fall back to character trimming.
            return reason[:limit]
        # Excess length counts encoded characters, while JSON escaping may make raw characters occupy multiple positions. Using it as
        # the number of raw characters to remove is only a lower-bound estimate; trim, reencode and recheck until it fits. If estimates exceed
        # parameter length, remove half rather than emptying it: escaped-parameter excess often exceeds raw length, and emptying once loses
        # otherwise retainable diagnostics. Remove at least one character to guarantee convergence.
        deficit = len(encoded) - limit
        drop = deficit if deficit < len(current) else max(1, len(current) // 2)
        owner[longest_key] = current[: len(current) - drop]
        encoded = encode_failure(code, **params)
    return encoded


#: Two failure codes include expected endpoint output extensions in copy; compute lists at render time from persisted media_type.
_COMFYUI_ARTIFACT_MISMATCH_CODES = frozenset({"comfyui_output_type_mismatch", "comfyui_output_container_mismatch"})


def failure_message_key(code: str, params: Mapping[str, Any]) -> str:
    """Copy key for registered failure codes. ``model_discontinued`` selects copy omitting the corresponding sentence when parameters lack the discontinuation date or successor."""
    key = FAILURE_CODE_KEYS[code]
    if code == "model_discontinued":
        if not params.get("shutdown_date"):
            key += "_undated"
        if not params.get("successor"):
            key += "_no_successor"
    return key


def render_failure(error_message: str | None, translate: Callable[..., str], names: DisplayNames) -> str | None:
    """Render a stored failure reason for display via the request Translator.

    Recognised ``[code]`` / ``[code] {params}`` strings render to localized text; cascade
    reasons nest the upstream reason in their ``reason`` param and are expanded
    recursively, so it is localized too. Everything else (raw exception text, legacy rows,
    malformed payloads) passes through unchanged.

    Cascade nesting is self-limiting: each layer re-encodes the previous envelope into JSON,
    so escaping makes the string grow super-linearly and the write side caps it well before
    the depth could threaten the recursion limit.

    Provider and model IDs in the params render as display names from ``names``; the stored
    params keep the IDs.

    The two ComfyUI artifact-mismatch codes take their ``expected`` extension list from the
    stored ``media_type`` here rather than from the row: the list is a projection of a static
    whitelist, so every stored row — including those written before the text listed it —
    renders with the whitelist this build actually enforces.
    """
    if not error_message:
        return error_message
    parsed = parse_failure(error_message)
    if parsed is None:
        return error_message
    code, params = parsed
    if code == CASCADE_FAILURE_CODE:
        nested_reason = params.get("reason")
        if isinstance(nested_reason, str):
            params = {**params, "reason": render_failure(nested_reason, translate, names)}
    if code in {"declarative_template_render_failed", "declarative_response_extract_failed"}:
        detail = params.get("detail")
        if _is_validation_message(detail):
            params = {**params, "detail": render_message(detail["key"], detail["params"], translate, names)}
    if code in _COMFYUI_ARTIFACT_MISMATCH_CODES:
        media_type = params.get("media_type")
        params = {**params, "expected": expected_suffixes_text(media_type if isinstance(media_type, str) else "")}
    return render_generation_input_error(failure_message_key(code, params), params, translate, names)


def parse_failure(error_message: str | None) -> tuple[str, dict[str, Any]] | None:
    """Split registered ``[code] {params}`` into machine codes and parameters; return ``None`` for raw/malformed reasons."""
    if not error_message:
        return None
    match = _STRUCTURED_RE.match(error_message)
    if match is None:
        return None
    code = match.group(1)
    if code not in FAILURE_CODE_KEYS:
        return None
    raw_params = match.group(2)
    if not raw_params:
        return code, {}
    try:
        parsed = json.loads(raw_params)
    except (ValueError, RecursionError):
        # Malformed JSON and payloads nested deeply enough for ``json.loads`` itself to hit recursion limits. Rendering occurs within
        # HTTP requests, where escaping exceptions cause 500s. Treat both as unrecognized: callers pass through unchanged without losing reasons.
        return None
    if not isinstance(parsed, dict):
        return None
    return code, parsed
