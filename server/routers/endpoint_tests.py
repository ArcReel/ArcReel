"""Endpoint-test API: preview requests, validate responses, and test connections.

Three independent endpoints without ``mode``: inputs, costs, and responses differ; combining them
would make applicable fields merely prose. Inline all definitions (connection tests may alternatively
supply ``model_ref``); the server retains no drafts.

Accept JSON bodies; with assets, use ``multipart/form-data`` with the same JSON in ``payload`` and
file fields named by ``inputs`` ``source`` (``start_image`` / ``end_image`` / ``reference_images`` /
``reference_audio_files``; the latter two repeatable). Discard assets after use, without temporary-upload
endpoints or project-asset references.

Invalid definitions/rendering errors yield 422 + ``diagnostic.errors[{path, code, message}]``, using the
same shared validator as saving: definitions accepted here are necessarily accepted by saving too."""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import shutil
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

# Use starlette UploadFile, not the fastapi subclass: isinstance checks apply to Request.form() output,
# whose parser returns starlette.datastructures.UploadFile; checking the subclass would miss every file.
from starlette.datastructures import FormData, UploadFile

from arcreel_market_core.endpoint_definition import AssetData, DefinitionDiagnostics, validate_definition
from lib.backends.backend_assembly.specs import builtin_declarative_video_diagnostics
from lib.catalog import query as catalog
from lib.config.registry import PROVIDER_REGISTRY, ProviderMeta
from lib.config.resolver import ConfigResolver
from lib.custom_provider import is_custom_provider, parse_provider_id
from lib.custom_provider.comfyui.failures import ComfyuiError
from lib.custom_provider.endpoint_resolution import resolve_endpoint_spec
from lib.custom_provider.endpoint_test import (
    ASSET_SOURCES,
    EndpointTestAssets,
    EndpointTestCredentials,
    EndpointTestDefinitionError,
    EndpointTestMode,
    EndpointTestParameters,
    KindTestSupport,
    TrialRun,
    TrialRunBusyError,
    TrialRunManager,
    TrialRunTarget,
    artifact_media_type,
    check_response,
    model_ref_target,
    parse_response_body,
    stage_report_payload,
    support_for_kind,
    supports_test_mode,
    trial_run_manager,
)
from lib.custom_provider.path_overrides import NO_PATH_OVERRIDES, PathOverrides, effective_path_overrides
from lib.db import async_session_factory, get_async_session
from lib.db.models.custom_provider import CustomProvider
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from lib.generation.generation_result import problem_from_task_failure
from lib.generation.task_failure import encode_failure, parse_failure, render_failure
from lib.i18n.display_names import DisplayNames
from lib.infra.api_errors import BadRequestError, ConflictError, NotFoundError, UnprocessableError
from lib.infra.async_thread import run_sync_transaction
from server.i18n import DisplayNamesCatalog, Translator

logger = logging.getLogger(__name__)

#: Per-asset file limit. Test first frames/references have real-generation sizes; use the same tier as reference audio.
MAX_ASSET_BYTES = 15 * 1024 * 1024

#: Total asset-file count per request, with headroom beyond the heaviest legitimate combination (H3: 9 references + first/end frames + 3 audio segments).
MAX_ASSET_FILES = 16

#: Endpoint media types supporting connection tests. Model-row entry points reject text/audio rows accordingly:
#: dispatching them to video/image endpoints would pay for a different model.
_TESTABLE_MEDIA_TYPES = frozenset({"video", "image"})

router = APIRouter(tags=["Custom Endpoints"])


def get_trial_run_manager() -> TrialRunManager:
    """Injection point for the connection-test registry. Process singleton; tests override the dependency with an isolated instance."""
    return trial_run_manager()


def get_config_resolver() -> ConfigResolver:
    """Configuration resolver for ``model_ref`` backend assembly; opens its own session without sharing the request transaction."""
    return ConfigResolver(async_session_factory)


# ---------------------------------------------------------------------------
# Request / response models.
# ---------------------------------------------------------------------------


class TestParametersInput(BaseModel):
    """Call parameters named as in ``VideoGenerationRequest``. Deliberately no seed (the product UI has no setting for it)."""

    model: str
    prompt: str = ""
    duration_seconds: int = 5
    aspect_ratio: str = "9:16"
    resolution: str | None = None
    generate_audio: bool = True

    def to_parameters(self) -> EndpointTestParameters:
        return EndpointTestParameters(
            model=self.model,
            prompt=self.prompt,
            duration_seconds=self.duration_seconds,
            aspect_ratio=self.aspect_ratio,
            resolution=self.resolution,
            generate_audio=self.generate_audio,
        )


class TestCredentialsInput(BaseModel):
    """Two credential forms: database lookup by ``provider_id``, or inline ``base_url`` + ``api_key``."""

    provider_id: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class ModelRefInput(BaseModel):
    """Model-row reference: canonical ``provider_id`` (built-in ``kling``, custom ``custom-3``, for example)."""

    provider_id: str
    model_id: str


class PreviewRequestInput(BaseModel):
    definition: Any
    parameters: TestParametersInput
    credentials: TestCredentialsInput | None = None


class CheckResponseInput(BaseModel):
    definition: Any
    stage: Literal["submit", "poll", "result"]
    #: Verbatim provider response. Try JSON parsing strings; otherwise validate as the original string.
    response_body: Any = None


class TrialRunInput(BaseModel):
    definition: Any = None
    model_ref: ModelRefInput | None = None
    parameters: TestParametersInput
    credentials: TestCredentialsInput | None = None


class PreviewedRequestResponse(BaseModel):
    method: str
    url: str
    headers: dict[str, str]
    body: Any = None


class PreviewResponse(BaseModel):
    """Rendered request sections plus how the request was calculated.

    ``conversions`` shape follows definition ``kind``: declarative endpoints fill templates directly with
    no explainable conversions, so null; ComfyUI reports dimension/frame/seed conversions and nodes
    removed by this graph transformation."""

    submit: PreviewedRequestResponse
    poll: PreviewedRequestResponse
    result: PreviewedRequestResponse | None = None
    conversions: dict[str, Any] | None = None


class TrialRunResponse(BaseModel):
    id: str
    status: str
    provider: str
    model: str
    #: Video or image output; readers choose player or image display accordingly.
    media_type: str = "video"
    created_at: float
    finished_at: float | None = None
    api_call_id: int | None = None
    provider_job_id: str | None = None
    stages: dict[str, str] = {}
    request: dict[str, Any] | None = None
    submit_response: Any = None
    poll_responses: list[Any] = []
    result_response: Any = None
    extractions: dict[str, Any] = {}
    video_url: str | None = None
    duration_seconds: int | None = None
    error: str | None = None
    #: Stable failure code behind ``error``. Readers dispatch by it, with code references/troubleshooting directions unavailable for bare exception text.
    error_code: str | None = None
    #: User next action (``GenerationAction``) for this failure code, sharing the project-page generation-failure decision.
    error_action: str | None = None
    has_artifact: bool = False


# ---------------------------------------------------------------------------
# Shared request parsing and definition gate.
# ---------------------------------------------------------------------------


async def _parse_body[T: BaseModel](request: Request, model: type[T]) -> tuple[T, EndpointTestAssets]:
    """Accept JSON or multipart. For multipart, ``payload`` holds the same JSON and other fields are assets."""
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("multipart/form-data"):
        return _validated(model, await _json_body(request)), EndpointTestAssets()
    form = await request.form()
    raw = form.get("payload")
    if not isinstance(raw, str):
        raise BadRequestError("endpoint_test_payload_required")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise BadRequestError("endpoint_test_payload_invalid") from exc
    assets = await _read_assets(form)
    return _validated(model, payload), assets


async def _json_body(request: Request) -> object:
    try:
        return await request.json()
    except ValueError as exc:
        raise BadRequestError("endpoint_test_payload_invalid") from exc


def _validated[T: BaseModel](model: type[T], payload: object) -> T:
    try:
        return model.model_validate(payload)
    except ValueError as exc:
        raise BadRequestError("endpoint_test_payload_invalid").with_diagnostic(str(exc)) from exc


async def _read_assets(form: FormData) -> EndpointTestAssets:
    # Count every form file, not only registered fields: unknown-field files have already been parsed, buffered,
    # or written to temporary disk. Counting recognized fields alone lets renamed keys bypass the limit.
    if sum(1 for _name, item in form.multi_items() if isinstance(item, UploadFile)) > MAX_ASSET_FILES:
        raise BadRequestError("endpoint_test_too_many_assets", limit=MAX_ASSET_FILES)
    by_source: dict[str, AssetData | Sequence[AssetData] | None] = {}
    for source, is_list in ASSET_SOURCES.items():
        uploads = [item for item in form.getlist(source) if isinstance(item, UploadFile)]
        loaded = [await _read_upload(upload) for upload in uploads]
        by_source[source] = loaded if is_list else (loaded[0] if loaded else None)
    return EndpointTestAssets(by_source=by_source)


async def _read_upload(upload: UploadFile) -> AssetData:
    # Read at most limit + 1 bytes, detecting excess without loading oversized files entirely.
    content = await upload.read(MAX_ASSET_BYTES + 1)
    if len(content) > MAX_ASSET_BYTES:
        raise BadRequestError(
            "endpoint_test_asset_too_large",
            name=upload.filename or "asset",
            limit_mb=MAX_ASSET_BYTES // (1024 * 1024),
        )
    return AssetData(upload.content_type or "application/octet-stream", content)


def _accepted_definition(definition: object, _t: Translator, *, mode: EndpointTestMode) -> dict[str, Any]:
    """Run shared validation, then check whether definition ``kind`` supports this mode.

    Validation shares saving's decisions/codes, with no additional definition threshold here. An unsupported
    mode for a kind is not an invalid definition; reject separately with 400, outside definition diagnostics."""
    diagnostics = validate_definition(definition)
    if diagnostics.errors or not isinstance(definition, dict):
        raise _invalid(diagnostics, _t)
    kind = str(definition["kind"])
    if not supports_test_mode(kind, mode):
        raise BadRequestError("endpoint_test_mode_unsupported_for_kind", kind=kind)
    return definition


def _support(definition: Mapping[str, Any]) -> KindTestSupport:
    """Endpoint-test implementation for this definition kind, already admitted by :func:`_accepted_definition` mode checks."""
    return support_for_kind(str(definition["kind"]))


def _invalid(diagnostics: DefinitionDiagnostics, _t: Translator) -> UnprocessableError:
    return UnprocessableError("custom_endpoint_definition_invalid").with_diagnostic(diagnostics.to_payload(_t))


async def _resolve_credentials(
    body_credentials: TestCredentialsInput | None,
    session: AsyncSession,
    *,
    require_base_url: bool = True,
    require_api_key: bool = True,
) -> EndpointTestCredentials | None:
    """Normalize both credential forms. ``provider_id`` accepts only custom providers; built-in credentials do not call endpoint definitions this way.

    With ``require_base_url`` False (all three definition URL sections use fixed absolute addresses),
    inline credentials need no base URL. With ``require_api_key`` False (empty ``auth``), api_key is
    unnecessary: the definition never uses that credential, so forcing invented placeholder values is pointless."""
    if body_credentials is None:
        return None
    if body_credentials.provider_id:
        if body_credentials.base_url or body_credentials.api_key:
            # Silently choosing one mixed credential source would send paid requests to a billing target the caller did not explicitly select.
            raise BadRequestError("endpoint_test_credentials_ambiguous")
        provider = await _custom_provider(body_credentials.provider_id, session)
        # Empty stored values follow missing-inline-field rules: expose missing credentials on the request thread, not after background
        # tasks render empty-auth requests and fail.
        if (require_base_url and not provider.base_url) or (require_api_key and not provider.api_key):
            return None
        return EndpointTestCredentials(base_url=provider.base_url or "", api_key=provider.api_key or "")
    if (body_credentials.base_url or not require_base_url) and (
        body_credentials.api_key is not None or not require_api_key
    ):
        return EndpointTestCredentials(base_url=body_credentials.base_url or "", api_key=body_credentials.api_key or "")
    return None


async def _required_credentials(
    body_credentials: TestCredentialsInput | None,
    session: AsyncSession,
    *,
    require_base_url: bool = True,
    require_api_key: bool = True,
) -> EndpointTestCredentials:
    """Connection tests require credentials used by the definition: requests cannot send without them, so
    queuing them only to fail in the background is pointless. If neither credential is used (empty ``auth``
    and all three URLs fixed absolute addresses), allow empty credentials."""
    credentials = await _resolve_credentials(
        body_credentials, session, require_base_url=require_base_url, require_api_key=require_api_key
    )
    if credentials is None:
        if not require_base_url and not require_api_key:
            return EndpointTestCredentials(base_url="", api_key="")
        raise BadRequestError("endpoint_test_credentials_required")
    return credentials


async def _custom_provider(provider_id: str, session: AsyncSession) -> CustomProvider:
    if not is_custom_provider(provider_id):
        raise BadRequestError("endpoint_test_provider_not_custom", provider_id=provider_id)
    try:
        row_id = parse_provider_id(provider_id)
    except ValueError as exc:
        raise BadRequestError("endpoint_test_provider_not_custom", provider_id=provider_id) from exc
    provider = await CustomProviderRepository(session).get_provider(row_id)
    if provider is None:
        raise NotFoundError("provider_not_found")
    return provider


# ---------------------------------------------------------------------------
# Request preview / response validation.
# ---------------------------------------------------------------------------


@router.post("/preview-request")
async def preview_endpoint_request(
    request: Request,
    _t: Translator,
    names: DisplayNamesCatalog,
    session: AsyncSession = Depends(get_async_session),
) -> PreviewResponse:
    """Render the outgoing request without sending it. Mask credentials, replace assets with size summaries, and retain placeholders for postsubmission IDs."""
    body, assets = await _parse_body(request, PreviewRequestInput)
    definition = _accepted_definition(body.definition, _t, mode=EndpointTestMode.PREVIEW_REQUEST)
    support = _support(definition)
    require_base_url, require_api_key = support.credential_needs(definition)
    credentials = await _resolve_credentials(
        body.credentials, session, require_base_url=require_base_url, require_api_key=require_api_key
    )
    try:
        preview = support.preview(definition, body.parameters.to_parameters(), credentials=credentials, assets=assets)
    except EndpointTestDefinitionError as exc:
        raise _invalid(exc.diagnostics, _t) from exc
    except ComfyuiError as exc:
        # Construction-time ComfyUI failures (e.g. fewer references than cells, graph cascades reaching output nodes) mean this definition
        # cannot render with these parameters, not an invalid definition. Use existing trilingual failure-code wording rather than separate prose.
        raise BadRequestError(
            "endpoint_test_preview_failed", detail=render_failure(encode_failure(exc.code, **exc.params), _t, names)
        ) from exc
    return PreviewResponse(
        submit=_previewed(preview.submit),
        poll=_previewed(preview.poll),
        result=_previewed(preview.result) if preview.result else None,
        conversions=dict(preview.conversions) if preview.conversions is not None else None,
    )


@router.post("/check-response")
async def check_endpoint_response(
    body: CheckResponseInput,
    _t: Translator,
) -> dict[str, Any]:
    """Validate extraction paths and status mappings against a real provider response, without sending requests. Zero cost."""
    definition = _accepted_definition(body.definition, _t, mode=EndpointTestMode.CHECK_RESPONSE)
    try:
        report = check_response(definition, body.stage, parse_response_body(body.response_body))
    except EndpointTestDefinitionError as exc:
        raise _invalid(exc.diagnostics, _t) from exc
    return stage_report_payload(report)


# ---------------------------------------------------------------------------
# Connection tests.
# ---------------------------------------------------------------------------


@router.post("/trial-runs", status_code=201)
async def start_trial_run(
    request: Request,
    _t: Translator,
    names: DisplayNamesCatalog,
    session: AsyncSession = Depends(get_async_session),
    manager: TrialRunManager = Depends(get_trial_run_manager),
    resolver: ConfigResolver = Depends(get_config_resolver),
) -> TrialRunResponse:
    """Actually submit generation and poll to terminal state; **incurs charges**. There is no confirmation parameter: callers own explicit billing disclosure.

    Choose exactly one of inline definition or ``model_ref``. The latter resolves endpoint/credentials by
    model row; built-in and custom providers share the same resource, result shape, and accounting.
    Allow only one concurrent run per user."""
    body, assets = await _parse_body(request, TrialRunInput)
    parameters = body.parameters.to_parameters()
    if body.model_ref is not None and body.definition is not None:
        # Silently choosing between two target forms would send paid requests to a target the caller did not select.
        raise BadRequestError("endpoint_test_definition_and_model_ref_exclusive")
    if body.model_ref is not None:
        target, credentials, definition, path_overrides = await _model_ref_target(body.model_ref, session, resolver, _t)
        # Result model must match actual execution and accounting: model_ref uses its row,
        # overriding an independently supplied parameters model.
        parameters = replace(parameters, model=body.model_ref.model_id)
    elif body.definition is not None:
        path_overrides = NO_PATH_OVERRIDES
        definition = _accepted_definition(body.definition, _t, mode=EndpointTestMode.TRIAL_RUN)
        support = _support(definition)
        require_base_url, require_api_key = support.credential_needs(definition)
        credentials = await _required_credentials(
            body.credentials, session, require_base_url=require_base_url, require_api_key=require_api_key
        )
        target = support.trial_target(definition, credentials, parameters)
    else:
        raise BadRequestError("endpoint_test_definition_or_model_ref_required")

    preview_payload = _preview_payload(definition, parameters, credentials, assets, _t, path_overrides)
    # Register temporary directories immediately after thread creation; cancellation while persisting assets loses return values, so use registration for cleanup.
    staged: list[Path] = []
    try:
        persisted = await run_sync_transaction(_stage_assets, assets, staged)
        run = await manager.start(
            target,
            parameters,
            assets=persisted,
            staging=staged[0],
            request_preview=preview_payload,
        )
    except TrialRunBusyError as exc:
        await run_sync_transaction(_remove_staging, staged)
        raise ConflictError("trial_run_already_running") from exc
    except BaseException:
        # Failure before manager takeover (asset persistence/result-directory creation) leaves unowned directories no later actor would delete.
        await run_sync_transaction(_remove_staging, staged)
        raise
    return _run_response(run, _t, names)


@router.get("/trial-runs/{run_id}")
async def get_trial_run(
    run_id: str,
    _t: Translator,
    names: DisplayNamesCatalog,
    manager: TrialRunManager = Depends(get_trial_run_manager),
) -> TrialRunResponse:
    """Read a connection test: memory while running, disk when terminal. Canceled or restart-interrupted runs retain no results."""
    run = await asyncio.to_thread(manager.get, run_id)
    if run is None:
        raise NotFoundError("trial_run_not_found")
    return _run_response(run, _t, names)


@router.post("/trial-runs/{run_id}/cancel", status_code=204)
async def cancel_trial_run(
    run_id: str,
    manager: TrialRunManager = Depends(get_trial_run_manager),
) -> Response:
    """Stop local polling and settle accounting as failed. Backend cancellation handles remote stopping; if unsuccessful, it continues running and charging."""
    if not await manager.cancel(run_id):
        raise NotFoundError("trial_run_not_found")
    return Response(status_code=204)


@router.get("/trial-runs/{run_id}/artifact")
async def get_trial_run_artifact(
    run_id: str,
    manager: TrialRunManager = Depends(get_trial_run_manager),
) -> FileResponse:
    """Play the artifact generated by this connection test.

    Determine MIME from persisted byte headers, not filenames: this layer chooses names (video
    ``artifact.mp4``, image ``artifact.png``), but ComfyUI may export another container in the same family.
    Declaring by name would make browsers decode using the wrong type."""
    located = await asyncio.to_thread(_locate_artifact, manager, run_id)
    if located is None:
        raise NotFoundError("trial_run_artifact_not_found")
    path, media_type = located
    return FileResponse(path, media_type=media_type)


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _previewed(section: Any) -> PreviewedRequestResponse:
    return PreviewedRequestResponse(method=section.method, url=section.url, headers=section.headers, body=section.body)


def _run_response(run: TrialRun, translate: Translator, names: DisplayNames) -> TrialRunResponse:
    """Localize results: render failure reasons in the current language, separately returning original failure codes and next actions.

    Provide codes/actions together rather than making readers infer from wording, which varies by language.
    Dispatch failure category/next steps by code. Use :func:`problem_from_task_failure`: the same code
    must recommend the same next action here and on project pages; duplicate implementations diverge
    (three of eight ComfyUI codes direct retries, not provider configuration). Bare exception text without
    an encodable code has neither."""
    payload = run.to_payload()
    parsed = parse_failure(run.error)
    payload["error"] = render_failure(run.error, translate, names)
    payload["error_code"] = parsed[0] if parsed else None
    payload["error_action"] = problem_from_task_failure(run.error).action.value if parsed else None
    return TrialRunResponse(**payload)


def _has_usable_credentials(meta: ProviderMeta, config: Mapping[str, str]) -> bool:
    """Whether credentials suffice for assembly. Follow credential-form grouping: declared credential_groups
    are alternatives; otherwise require all required ∩ secret fields. If neither yields nonempty groups
    (e.g. file-upload vertex), impose no gate here."""
    groups = [
        group
        for group in (meta.credential_groups or [[key for key in meta.required_keys if key in meta.secret_keys]])
        if group
    ]
    return not groups or any(all(config.get(key) for key in group) for group in groups)


async def _model_ref_target(
    model_ref: ModelRefInput,
    session: AsyncSession,
    resolver: ConfigResolver,
    _t: Translator,
) -> tuple[TrialRunTarget, EndpointTestCredentials | None, dict[str, Any] | None, PathOverrides]:
    """Resolve the target by model row. For declarative definitions, retrieve credentials too, enabling rendered-request and extraction result sections.

    Custom model rows also supply effective path overrides (``docs/adr/0101``), keeping rendered request sections consistent with actual sending."""
    if not is_custom_provider(model_ref.provider_id):
        # Check built-in model rows too: unregistered models make assembly fall back to provider-default video models;
        # registered nonvideo models (e.g. text tiers) dispatch to video endpoints. Both pay for another model.
        info = catalog.get_model(model_ref.provider_id, model_ref.model_id)
        if info is None:
            raise NotFoundError("model_not_found")
        if info.media_type != "video":
            raise BadRequestError("endpoint_test_model_unavailable")
        # Check credentials on the request thread too: missing-credential assembly errors are backend-specific Chinese sentences,
        # which detached background tasks would copy verbatim to result error fields.
        config = await resolver.provider_config(model_ref.provider_id)
        if not _has_usable_credentials(PROVIDER_REGISTRY[model_ref.provider_id], config):
            raise BadRequestError("missing_credentials")
        # Built-in declarative model rows still receive both diagnostic sections (rendered request / per-stage extraction) and presubmission rendering gates:
        # retrieve definitions/credential fallbacks from the same assembly source; Python backend rows leave both sections empty.
        definition = None
        credentials = None
        diagnostics = builtin_declarative_video_diagnostics(model_ref.provider_id, model_ref.model_id, config)
        if diagnostics is not None:
            builtin_definition, base_url, api_key = diagnostics
            definition = _accepted_definition(dict(builtin_definition), _t, mode=EndpointTestMode.TRIAL_RUN)
            credentials = EndpointTestCredentials(base_url=base_url, api_key=api_key)
        target = model_ref_target(model_ref.provider_id, model_ref.model_id, resolver=resolver, definition=definition)
        return target, credentials, definition, NO_PATH_OVERRIDES
    provider = await _custom_provider(model_ref.provider_id, session)
    model = await CustomProviderRepository(session).get_model_by_ids(provider.id, model_ref.model_id)
    if model is None:
        raise NotFoundError("model_not_found")
    # Existence alone is insufficient: disabled/nonvideo endpoint rows make assembly fall back to provider-default video models,
    # billing paid tests to another model while results still name the requested row.
    try:
        endpoint_spec = await resolve_endpoint_spec(model.endpoint, CustomEndpointRepository(session).get)
    except ValueError:
        endpoint_spec = None
    if not model.is_enabled or endpoint_spec is None or endpoint_spec.media_type not in _TESTABLE_MEDIA_TYPES:
        raise BadRequestError("endpoint_test_model_unavailable")
    # Detect credential configuration errors on the request thread, returning localized 400. Without base_url, assembly throws only
    # an untranslatable Chinese sentence in a detached background task, copied verbatim to result error; without api_key,
    # requests send empty auth headers and pay for calls certain to be rejected.
    if endpoint_spec.definition is not None:
        require_base_url, require_api_key = _support(endpoint_spec.definition).credential_needs(
            endpoint_spec.definition
        )
        if require_base_url and not provider.base_url:
            raise BadRequestError("endpoint_test_provider_base_url_required")
        if require_api_key and not provider.api_key:
            raise BadRequestError("missing_credentials")
    definition = None
    if endpoint_spec.definition is not None:
        # Built-in declarative endpoints (newapi-video / v2 / minimax families) have definitions like ce-*:
        # use the same rendered-request/per-stage-extraction/presubmission gate path, preserving diagnostics for bundled definitions too.
        definition = _accepted_definition(dict(endpoint_spec.definition), _t, mode=EndpointTestMode.TRIAL_RUN)
    credentials = EndpointTestCredentials(base_url=provider.base_url, api_key=provider.api_key)
    target = model_ref_target(
        model_ref.provider_id,
        model_ref.model_id,
        resolver=resolver,
        definition=definition,
        media_type=endpoint_spec.media_type,
    )
    return target, credentials, definition, effective_path_overrides(model.path_overrides, endpoint_spec.requests)


def _preview_payload(
    definition: Mapping[str, Any] | None,
    parameters: EndpointTestParameters,
    credentials: EndpointTestCredentials | None,
    assets: EndpointTestAssets,
    _t: Translator,
    path_overrides: PathOverrides = NO_PATH_OVERRIDES,
) -> dict[str, Any] | None:
    """Rendered-request result section.

    Also gates rendering before submission: render templates once before actual sending, returning 422
    immediately for missing placeholders rather than failing after background launch when users already
    await a doomed run. Leave empty without renderable definitions (Python endpoints), or for kinds
    without this section (ComfyUI; see ``KindTestSupport``)."""
    if definition is None or credentials is None:
        return None
    support = _support(definition)
    if not support.trial_run_request_preview:
        return None
    try:
        preview = support.preview(
            definition,
            parameters,
            credentials=credentials,
            assets=assets,
            placeholder_missing_assets=False,
            path_overrides=path_overrides,
        )
    except EndpointTestDefinitionError as exc:
        raise _invalid(exc.diagnostics, _t) from exc
    return {
        "method": preview.submit.method,
        "url": preview.submit.url,
        "headers": preview.submit.headers,
        "body": preview.submit.body,
    }


def _locate_artifact(manager: TrialRunManager, run_id: str) -> tuple[Path, str] | None:
    path = manager.artifact_path(run_id)
    return None if path is None else (path, artifact_media_type(path))


def _stage_assets(assets: EndpointTestAssets, staged: list[Path]) -> dict[str, Path | list[Path] | None]:
    staging = Path(tempfile.mkdtemp(prefix="arcreel-trial-run-"))
    staged.append(staging)
    return _persist_assets(assets, staging)


def _remove_staging(staged: list[Path]) -> None:
    for staging in staged:
        shutil.rmtree(staging, ignore_errors=True)  # hard-delete: temp -- Trial-run mkdtemp staging.


def _persist_assets(assets: EndpointTestAssets, staging: Path) -> dict[str, Path | list[Path] | None]:
    """Persist assets in a temporary directory: backends need file paths while uploads currently exist only in memory. Clean the directory when the run reaches terminal state."""
    persisted: dict[str, Path | list[Path] | None] = {}
    for source, is_list in ASSET_SOURCES.items():
        if is_list:
            persisted[source] = [
                _write_asset(staging, f"{source}_{index}", item) for index, item in enumerate(assets.items(source))
            ]
            continue
        single = assets.single(source)
        persisted[source] = _write_asset(staging, source, single) if single is not None else None
    return persisted


def _write_asset(staging: Path, name: str, asset: AssetData) -> Path:
    suffix = mimetypes.guess_extension(asset.mime_type) or ".bin"
    path = staging / f"{uuid.uuid4().hex}_{name}{suffix}"
    path.write_bytes(asset.content)
    return path
