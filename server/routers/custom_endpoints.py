"""Custom call-endpoint management API.

CRUD for definitions and confirmation before saving. No envelope: request bodies and exports are the
verbatim definition JSON. Import uses ``POST``; export uses ``GET`` followed by client-side saving.
There are no separate import/export endpoints or outer envelopes.

``POST /validate`` is one-step, server-stateless confirmation: share the saving validator and additionally
return existing endpoints with matching author/name, echoed hints, and version levels, letting clients
choose a new copy, overwrite, or cancellation before creation.

Both import entry points (``validate`` and create) route by payload shape: ``kind`` means an endpoint
definition; otherwise accept as ComfyUI workflow. Users most commonly have workflows exported by
ComfyUI itself, not endpoint definitions."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_market_core.endpoint_definition import (
    DefinitionDiagnostics,
    SchemaVersionLevel,
    VersionRelation,
    current_schema_version,
    meets_min_app_version,
    parse_semver,
    schema_version_level,
    validate_definition,
    version_relation,
)
from lib.custom_provider import make_endpoint_key, make_provider_id
from lib.custom_provider.comfyui.import_shapes import ImportShape, route_import_payload, ui_workflow_refusal
from lib.custom_provider.discovery_formats import endpoint_attachment_holds
from lib.custom_provider.endpoint_resolution import derive_mirror_columns
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.models.custom_endpoint import CustomEndpoint
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository, EndpointReference
from lib.infra.api_errors import ConflictError, NotFoundError, UnprocessableError
from server.i18n import Translator
from server.routers import comfyui_inference, endpoint_tests
from server.routers._market_installations import (
    EndpointInstallationResponse,
    endpoint_installation,
    endpoint_installations,
)
from server.routers.system_config import get_app_version_reader

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/custom-endpoints", tags=["Custom Endpoints"])

# Register endpoint-test and node-binding-inference routes before this module's ``/{endpoint_id}``: FastAPI matches registration order;
# failed path-parameter parsing does not fall through. ``/custom-endpoints/trial-runs`` matching ``endpoint_id: int`` yields
# 422 directly.
router.include_router(endpoint_tests.router)
router.include_router(comfyui_inference.router)

#: Body is verbatim definition JSON. Deliberately avoid ``dict``: nonobjects (arrays, bare strings) must pass through shared validation
#: for field-located diagnostics instead of FastAPI's own 422 shape.
DefinitionBody = Annotated[Any, Body()]

#: For pasted raw workflows, importers specify image or video output; workflows themselves do not declare this.
ImportMediaType = Literal["image", "video"]


# ---------------------------------------------------------------------------
# Pydantic models.
# ---------------------------------------------------------------------------


class CustomEndpointResponse(BaseModel):
    """One custom call endpoint. ``definition`` is its export content; clients save it directly as a file."""

    id: int
    key: str  # ce-<id>, system-assigned and opaque.
    display_name: str
    kind: str
    schema_version: str
    media_type: str
    definition: dict[str, Any]
    created_at: str | None = None
    updated_at: str | None = None
    installation: EndpointInstallationResponse | None = None


class CustomEndpointListResponse(BaseModel):
    endpoints: list[CustomEndpointResponse]


class DuplicateDescriptor(BaseModel):
    """Existing endpoint matching the import definition's author/name (``meta.author`` + ``meta.name``)."""

    id: int
    key: str
    display_name: str
    version: str
    # Existing definition age relative to the import file.
    relation: VersionRelation


class SchemaVersionInfo(BaseModel):
    """File/current-version comparison. ``level`` is only a hint; the schema validator remains the gate."""

    file: str | None
    current: str
    level: SchemaVersionLevel


class MinAppVersionInfo(BaseModel):
    """Compare ``meta.min_app_version`` with current application version. Noncompliance is advisory, not an import blocker."""

    required: str
    current: str
    satisfied: bool


class ValidateResponse(BaseModel):
    errors: list[dict[str, str]]
    warnings: list[dict[str, str]]
    duplicates: list[DuplicateDescriptor]
    # Echo meta.hints verbatim (base_url and suggested model); display only, without bundled provider creation.
    hints: dict[str, Any] | None = None
    schema_version: SchemaVersionInfo
    # null if undeclared/non-semver, or application version cannot be read.
    min_app_version: MinAppVersionInfo | None = None
    # Accepted payload shape: endpoint definition, ComfyUI API workflow, or unsubmittable UI format.
    import_shape: ImportShape
    # Wrapped raw API workflow; null for the other two shapes, where clients retain their own content.
    wrapped_definition: dict[str, Any] | None = None


class EndpointReferenceDescriptor(BaseModel):
    """Model row referencing the endpoint. Return with deletion-denied 409 responses to show users where to detach it."""

    provider_id: int
    provider_display_name: str
    model_id: str
    model_display_name: str


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def endpoint_response(
    row: CustomEndpoint, installation: EndpointInstallationResponse | None = None
) -> CustomEndpointResponse:
    return CustomEndpointResponse(
        installation=installation,
        id=row.id,
        key=make_endpoint_key(row.id),
        display_name=row.display_name,
        kind=row.kind,
        schema_version=row.schema_version,
        media_type=row.media_type,
        definition=row.definition,
        created_at=dt_to_iso(row.created_at),
        updated_at=dt_to_iso(row.updated_at),
    )


def _routed_import(body: object, media_type: str) -> tuple[ImportShape, Any, DefinitionDiagnostics]:
    """Route by payload shape, returning the definition to validate plus diagnostics.

    UI-format workflows bypass the validator: they lack ``class_type``, so definition-schema validation
    would flood the screen with field errors unrelated to choosing the wrong export menu item.
    One structured refusal instead explains the next action."""
    shape, definition = route_import_payload(body, media_type=media_type)
    if shape is ImportShape.COMFYUI_UI_WORKFLOW:
        return shape, definition, ui_workflow_refusal()
    return shape, definition, validate_definition(definition)


def _accepted(definition: object, diagnostics: DefinitionDiagnostics, _t: Translator) -> dict[str, Any]:
    """Errors yield 422 + structured diagnostics; only accepted definitions may be persisted.

    Saving and ``validate`` share :func:`validate_definition`, preventing divergent decisions. Eliminating
    “validate accepts, saving rejects” is precisely why the validator is shared. Warnings do not block saving."""
    if diagnostics.errors or not isinstance(definition, dict):
        raise UnprocessableError("custom_endpoint_definition_invalid").with_diagnostic(diagnostics.to_payload(_t))
    return definition


def _accepted_definition(body: object, _t: Translator) -> dict[str, Any]:
    """Full-replacement entry point: accept endpoint definitions only, without import routing.

    Overwriting an existing endpoint with raw workflow would erase confirmed node bindings too; this must not happen silently."""
    return _accepted(body, validate_definition(body), _t)


def _lineage(definition: object) -> tuple[str | None, str | None, str | None]:
    """Extract ``(author, name, version)`` from any input shape.

    Database rows are validated, but request bodies may not yet be. Duplicate detection must still work
    on erroneous definitions, or a single name typo would hide the fact that a copy was already imported."""
    if not isinstance(definition, Mapping):
        return None, None, None
    meta = definition.get("meta")
    if not isinstance(meta, Mapping):
        return None, None, None
    author, name, version = meta.get("author"), meta.get("name"), meta.get("version")
    return (
        author if isinstance(author, str) else None,
        name if isinstance(name, str) else None,
        version if isinstance(version, str) else None,
    )


async def _duplicates_of(
    repo: CustomEndpointRepository, definition: object, exclude_id: int | None
) -> list[DuplicateDescriptor]:
    """Find existing endpoints by matching ``meta.author + meta.name``.

    Match only these: keys are system-assigned and absent from shared files; display names are not unique.
    Author/name is the only file information identifying the same definition across instances.
    Versions explain relative age only and do not participate in matching."""
    author, name, version = _lineage(definition)
    if author is None or name is None:
        return []
    duplicates: list[DuplicateDescriptor] = []
    for row in await repo.list_all():
        if row.id == exclude_id:
            continue
        row_author, row_name, row_version = _lineage(row.definition)
        if row_author != author or row_name != name:
            continue
        duplicates.append(
            DuplicateDescriptor(
                id=row.id,
                key=make_endpoint_key(row.id),
                display_name=row.display_name,
                version=row_version or "",
                relation=version_relation(row_version, version),
            )
        )
    return duplicates


def _hints_of(definition: object) -> dict[str, Any] | None:
    if not isinstance(definition, Mapping):
        return None
    meta = definition.get("meta")
    if not isinstance(meta, Mapping):
        return None
    hints = meta.get("hints")
    return dict(hints) if isinstance(hints, Mapping) else None


def _schema_version_of(definition: object) -> str | None:
    if not isinstance(definition, Mapping):
        return None
    value = definition.get("schema_version")
    return value if isinstance(value, str) else None


def _min_app_version_of(definition: object, read_app_version: Callable[[], str]) -> MinAppVersionInfo | None:
    if not isinstance(definition, Mapping):
        return None
    meta = definition.get("meta")
    required = meta.get("min_app_version") if isinstance(meta, Mapping) else None
    if not isinstance(required, str) or parse_semver(required) is None:
        return None
    try:
        current = read_app_version()
    except Exception:
        logger.exception("Failed to read app version")
        return None
    return MinAppVersionInfo(required=required, current=current, satisfied=meets_min_app_version(required, current))


def _reference_descriptors(references: list[EndpointReference]) -> list[dict[str, Any]]:
    return [EndpointReferenceDescriptor(**ref._asdict()).model_dump() for ref in references]


async def _invalidate_backend_cache() -> None:
    """Apply in-place definition updates to new tasks immediately by clearing backend instances cached by provider/model.

    Polling tasks retain construction-time specs; changes affect only new tasks. This accepted behavior
    uses no in-flight version lock."""
    from server.services.tasks.generation_context import invalidate_backend_cache

    invalidate_backend_cache()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post("", status_code=201)
async def create_endpoint(
    body: DefinitionBody,
    _t: Translator,
    media_type: ImportMediaType = "video",
    session: AsyncSession = Depends(get_async_session),
) -> CustomEndpointResponse:
    """Create (import) a custom call endpoint. The body is verbatim definition JSON; keys are system-assigned.

    Bodies may also be raw ComfyUI API workflows; the server wraps them as ComfyUI endpoint definitions
    using ``media_type``. Wrapped node bindings are empty, so required prompt/output binding checks reject them."""
    _, routed, diagnostics = _routed_import(body, media_type)
    definition = _accepted(routed, diagnostics, _t)
    mirror = derive_mirror_columns(definition)
    repo = CustomEndpointRepository(session)
    row = await repo.create(
        definition=definition,
        kind=mirror.kind,
        schema_version=mirror.schema_version,
        media_type=mirror.media_type,
        display_name=mirror.display_name,
    )
    await session.commit()
    await _invalidate_backend_cache()
    await session.refresh(row)
    return endpoint_response(row, await endpoint_installation(session, row.id))


@router.get("")
async def list_endpoints(session: AsyncSession = Depends(get_async_session)) -> CustomEndpointListResponse:
    """List all custom call endpoints, including full definitions."""
    rows = await CustomEndpointRepository(session).list_all()
    installations = await endpoint_installations(session)
    return CustomEndpointListResponse(endpoints=[endpoint_response(row, installations.get(row.id)) for row in rows])


@router.post("/validate")
async def validate_endpoint_definition(
    body: DefinitionBody,
    _t: Translator,
    read_app_version: Annotated[Callable[[], str], Depends(get_app_version_reader)],
    exclude_id: int | None = None,
    media_type: ImportMediaType = "video",
    session: AsyncSession = Depends(get_async_session),
) -> ValidateResponse:
    """One-step presave confirmation: shape routing + validation diagnostics + matching author/name endpoints + echoed hints + version levels + minimum app version. The server retains no state.

    ``exclude_id`` excludes the edited endpoint itself; otherwise it always reports itself as a duplicate.
    For raw API workflow payloads, compute every returned item from the wrapped result, which the client then takes forward."""
    shape, definition, diagnostics = _routed_import(body, media_type)
    payload = diagnostics.to_payload(_t)
    file_version = _schema_version_of(definition)
    current_version = current_schema_version(definition)
    return ValidateResponse(
        errors=payload["errors"],
        warnings=payload["warnings"],
        duplicates=await _duplicates_of(CustomEndpointRepository(session), definition, exclude_id),
        hints=_hints_of(definition),
        schema_version=SchemaVersionInfo(
            file=file_version,
            current=current_version,
            level=schema_version_level(file_version, current_version),
        ),
        min_app_version=_min_app_version_of(definition, read_app_version),
        import_shape=shape,
        wrapped_definition=definition if shape is ImportShape.COMFYUI_API_WORKFLOW else None,
    )


@router.get("/{endpoint_id}")
async def get_endpoint(
    endpoint_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> CustomEndpointResponse:
    """Read one endpoint. Export means saving this response's ``definition`` to a file; the server does not handle downloads."""
    row = await CustomEndpointRepository(session).get(endpoint_id)
    if row is None:
        raise NotFoundError("custom_endpoint_not_found")
    return endpoint_response(row, await endpoint_installation(session, row.id))


@router.put("/{endpoint_id}")
async def update_endpoint(
    endpoint_id: int,
    body: DefinitionBody,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> CustomEndpointResponse:
    """Replace the entire definition in place: retain its key and model-row attachments; new tasks use the new definition immediately."""
    definition = _accepted_definition(body, _t)
    mirror = derive_mirror_columns(definition)
    repo = CustomEndpointRepository(session)
    current = await repo.get(endpoint_id)
    if current is None:
        raise NotFoundError("custom_endpoint_not_found")
    if mirror.kind != current.kind:
        await _check_kind_change_keeps_attachments(repo, endpoint_id, mirror.kind, _t)
    if mirror.media_type != current.media_type:
        await _check_media_type_change_has_no_attachments(repo, endpoint_id, mirror.media_type)
    row = await repo.update(
        endpoint_id,
        definition=definition,
        kind=mirror.kind,
        schema_version=mirror.schema_version,
        media_type=mirror.media_type,
        display_name=mirror.display_name,
    )
    if row is None:
        raise NotFoundError("custom_endpoint_not_found")
    await session.commit()
    await _invalidate_backend_cache()
    await session.refresh(row)
    return endpoint_response(row, await endpoint_installation(session, row.id))


async def _check_kind_change_keeps_attachments(
    repo: CustomEndpointRepository,
    endpoint_id: int,
    new_kind: str,
    _t: Callable[..., str],
) -> None:
    """Changing definition container kind must not break active attachments.

    Endpoint × provider-protocol bidirectional compatibility was previously checked only on provider-side
    writes (``docs/adr/0081``). Full replacement changes ``kind`` in place without changing keys/model-row
    references: ComfyUI can overwrite a declarative endpoint attached to an OpenAI-protocol provider,
    and vice versa, with neither side warning at save. Share the provider-side predicate to reject bad
    attachments on both write paths. Changing model-row attachments first, then replacing the definition,
    remains possible as two separate operations."""
    for attachment in await repo.list_attachments(make_endpoint_key(endpoint_id)):
        if endpoint_attachment_holds(endpoint_kind=new_kind, discovery_format=attachment.discovery_format):
            continue
        raise UnprocessableError(
            "custom_endpoint_kind_conflicts_with_attachment",
            model=attachment.model_id,
            provider=make_provider_id(attachment.provider_id),
        )


async def _check_media_type_change_has_no_attachments(
    repo: CustomEndpointRepository,
    endpoint_id: int,
    new_media_type: str,
) -> None:
    """Changing definition media type must not reclassify attached model rows in place.

    Model rows have no own media type; their endpoint determines it. Replacing an image-producing
    ComfyUI definition with video-producing content reclassifies every attached row as video. Providers
    with one image and one video default then have two video defaults; default lookup finds two rows and
    fails only during generation. Duration-tier normalization also follows media type and becomes misaligned.

    Unlike changing ``kind``, no still-valid case exists: this changes model-row membership, not pairing.
    Detach first, then replace the definition; both separate operations remain possible."""
    attachments = await repo.list_attachments(make_endpoint_key(endpoint_id))
    if attachments:
        raise UnprocessableError(
            "custom_endpoint_media_type_conflicts_with_attachment",
            media_type=new_media_type,
            model=attachments[0].model_id,
            provider=make_provider_id(attachments[0].provider_id),
        )


@router.delete("/{endpoint_id}", status_code=204)
async def delete_endpoint(
    endpoint_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Delete an endpoint; reject referenced endpoints and return their reference list.

    Do not cascade-delete referencing model rows: they carry manual user configuration (pricing, capability
    overrides), and cascading loses that work. Do not leave dangling references either, delaying failure until generation."""
    repo = CustomEndpointRepository(session)
    row = await repo.get(endpoint_id)
    if row is None:
        raise NotFoundError("custom_endpoint_not_found")
    references = await repo.list_references(make_endpoint_key(endpoint_id))
    if references:
        raise ConflictError("custom_endpoint_referenced_by_models", count=len(references)).with_diagnostic(
            {"references": _reference_descriptors(references)}
        )
    await repo.delete(endpoint_id)
    await session.commit()
    await _invalidate_backend_cache()
