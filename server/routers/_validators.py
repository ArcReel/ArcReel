"""Shared validation functions reused by multiple routers."""

from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError

from lib.catalog import query as catalog
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.model_setting_keys import MODEL_SETTING_MEDIA_TYPES
from lib.config.registry import PROVIDER_REGISTRY
from lib.config.resolver import ConfigResolver, VideoBucketCapabilityError, VideoGenerationType
from lib.infra.api_errors import BadRequestError


def split_video_backend_query(video_backend: str) -> tuple[str, str]:
    """Split capability-query ``video_backend`` into ``(provider_id, model_id)``.

    Complete bare providers without "/" using their default catalog video model, matching legacy bare-provider
    project.json overrides (``_parse_project_provider``). Failure to produce two parts raises ``BadRequestError``
    with ``video_backend_malformed``."""
    provider_id, sep, model_id = video_backend.partition("/")
    if not sep:
        provider_id, model_id = video_backend, catalog.default_model(video_backend, "video") or ""
    if not provider_id or not model_id:
        raise BadRequestError("video_backend_malformed", value=video_backend)
    return provider_id, model_id


def reject_retired_query_params(request: Request, *names: str) -> None:
    """Reject removed query parameters as unknown fields with 422, matching request-body ``extra="forbid"`` rejection."""

    errors = [
        {
            "type": "extra_forbidden",
            "loc": ("query", name),
            "msg": "Extra inputs are not permitted",
            "input": request.query_params[name],
        }
        for name in names
        if name in request.query_params
    ]
    if errors:
        raise RequestValidationError(errors)


async def require_video_bucket_capability(project: dict, generation_type: VideoGenerationType) -> None:
    """Video-submission preflight: resolve global/project configuration by task bucket and enforce the resolution gate (``docs/adr/0054``).

    Missing bucket capabilities or unavailable configured references (deleted models/providers or changed capabilities) raise
    ``BadRequestError``, localized to 400 by the app handler, providing repair guidance at submission rather than asynchronous
    task failure. Referenced models absent from provider catalogs similarly propagate ``ModelNotInCatalogError`` unchanged;
    its app handler returns 400 requesting model reselection. Workers independently enforce the same gate, so stale preflight
    cannot authorize invalid execution. Other resolution failures, such as no configured provider, retain existing behavior
    and are exposed by workers in the task panel."""
    from lib.db import async_session_factory

    try:
        await ConfigResolver(async_session_factory).resolve_video_backend(
            project, None, generation_type=generation_type
        )
    except VideoBucketCapabilityError as exc:
        raise BadRequestError(exc.code, **exc.params) from exc
    except ModelNotInCatalogError:
        raise
    except ValueError:
        # Other resolution failures, such as no configured provider, allow enqueueing for worker reporting in the task panel, matching image/video
        # entry behavior without upgrading noncapability failures to submission rejection.
        return


async def require_audio_switch_supported(project: dict, generation_type: VideoGenerationType) -> None:
    """Video-submission preflight: always-audible models do not accept audio-disabled configuration.

    These execution paths have no audio switch (``always_on``), so disabled intent cannot reach providers but would strip
    voice constraints through silent orchestration. Users would receive audible output without voice constraints. Reject
    explicitly with repair guidance rather than executing impossible intent.

    Settings UI disables the switch using the same predicate; cover legacy projects with stored disabled settings here.
    Share :func:`server.services.tasks.video_caps.resolve_audio_switch_conflict` with Agent admission. Resolution failures
    pass through, matching :func:`require_video_bucket_capability`, without upgrading configuration-resolution issues
    to submission-time rejection."""
    from server.services.tasks.video_caps import resolve_audio_switch_conflict

    conflict = await resolve_audio_switch_conflict(project, generation_type)
    if conflict is None:
        return
    provider_id, model_id = conflict
    raise BadRequestError("video_audio_switch_not_supported", provider=provider_id, model=model_id)


def validate_backend_value(value: str, field_name: str) -> None:
    """Validate backend fields in ``provider/model`` form.

    Accept only canonical provider IDs, ``PROVIDER_REGISTRY`` keys or ``custom-`` prefixes. Reject all legacy names
    (``gemini``/``aistudio``/``vertex``/``seedance``): one-time project migrations convert historical data to canonical IDs,
    after which legacy forms are no longer accepted (``docs/adr/0001``).

    Also require catalog model ``media_type`` to match ``field_name`` expectations through ``MODEL_SETTING_MEDIA_TYPES``;
    text tiers cannot use video models. Check only catalog-registered built-in models. Outside-catalog models, including
    custom providers, lack per-model capability facts and pass to provider API validation without guessing.

    Raises:
        BadRequestError: Invalid format, unknown provider, legacy name or media_type mismatch.
            Field names appear only in diagnostics, not user-facing summaries."""
    if "/" not in value:
        if value in PROVIDER_REGISTRY:
            return  # Bare registry ID without a model; downstream fills the global default.
        raise BadRequestError("invalid_backend_format").with_diagnostic(f"field: {field_name}")
    provider_id, model_id = value.split("/", 1)
    if provider_id not in PROVIDER_REGISTRY and not provider_id.startswith("custom-"):
        raise BadRequestError("unknown_provider", value=provider_id).with_diagnostic(f"field: {field_name}")
    # Skip this check for unregistered field names; missed registration of new fields passes silently rather than reporting falsely.
    expected_media_type = MODEL_SETTING_MEDIA_TYPES.get(field_name)
    if expected_media_type is not None:
        model_info = catalog.get_model(provider_id, model_id)
        if model_info is not None and model_info.media_type != expected_media_type:
            raise BadRequestError(
                "backend_media_type_mismatch",
                provider=provider_id,
                model=model_id,
                expected=expected_media_type,
                actual=model_info.media_type,
            ).with_diagnostic(f"field: {field_name}")
