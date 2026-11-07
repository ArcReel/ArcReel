"""Built-in provider models: enable/disable official models, add/remove user IDs (docs/adr/0099).

Queries supply manifests/all official fields; local built-in model tables store enabled state and user-added models only.
Without state rows, nondiscontinued official models default enabled. Discontinued models never become candidates regardless of enabled states (:func:`disabled_builtin_models`),
but remain visible with statuses. Callers commit writes then reload user-added models into queries
(``server.routers.provider_models.reload_user_added_models``).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_catalog_core import MEDIA_TYPES
from lib.backends.backend_assembly import specs as builtin_endpoints
from lib.catalog import query as catalog
from lib.catalog.model_id import has_dot_segment
from lib.config import registry
from lib.db.models.builtin_provider_model import BuiltinProviderModelState
from lib.db.repositories.builtin_provider_model_repo import BuiltinProviderModelRepository


class BuiltinModelError(Exception):
    """Rejected built-in-model operations. ``code`` is i18n keys; ``not_found`` distinguishes absent from forbidden."""

    def __init__(self, code: str, *, not_found: bool = False, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.not_found = not_found
        self.params = params


@dataclass(frozen=True)
class BuiltinModelItem:
    """One built-in provider model with enabled state."""

    view: catalog.ProviderModelView
    enabled: bool

    @property
    def is_default(self) -> bool:
        """Officially designated defaults, neither disabled nor discontinued."""
        return self.enabled and self.view.model.default


def _enabled(view: catalog.ProviderModelView, row: BuiltinProviderModelState | None) -> bool:
    if row is not None:
        return row.enabled
    return not view.lifecycle.discontinued


def _ensure_builtin(provider_id: str) -> None:
    if provider_id not in registry.PROVIDER_REGISTRY:
        raise BuiltinModelError("provider_not_found", not_found=True)


async def list_user_added_rows(session: AsyncSession) -> list[BuiltinProviderModelState]:
    """All user-added rows for query loading."""
    return [row for row in await BuiltinProviderModelRepository(session).list_all() if row.user_added]


async def list_builtin_models(session: AsyncSession, provider_id: str) -> list[BuiltinModelItem]:
    """All provider models, official first/user-added last, with enabled states."""
    _ensure_builtin(provider_id)
    rows = {row.model_id: row for row in await BuiltinProviderModelRepository(session).list_for_provider(provider_id)}
    return [
        BuiltinModelItem(view, _enabled(view, rows.get(view.model_id))) for view in catalog.provider_models(provider_id)
    ]


async def default_builtin_model(session: AsyncSession, provider_id: str, media_type: str) -> str | None:
    """Actually available built-in default model keys per media: catalog defaults, ``None`` when discontinued/user-disabled.

    Calls without models (bare provider overrides, model-less payloads, assembly) all resolve here; ``None`` means unspecified defaults,
    matching automatic selection (``ProviderStatus.disabled_models``). Nonbuilt-in providers also return ``None``.
    """
    model_id = catalog.default_model(provider_id, media_type)  # Catalogs omit discontinued defaults.
    if model_id is None:
        return None
    row = await BuiltinProviderModelRepository(session).get(provider_id, model_id)
    return model_id if row is None or row.enabled else None


def disabled_without_state(provider_id: str) -> frozenset[str]:
    """Keys excluded without state rows: discontinued-entry models."""
    return frozenset(view.model_id for view in catalog.provider_models(provider_id) if not _enabled(view, None))


async def disabled_builtin_models(session: AsyncSession) -> dict[str, frozenset[str]]:
    """Noncandidate keys by built-in provider: disabled plus discontinued, even when users manually enabled them.

    Global/project/task-bucket candidates and automatic selection without configured defaults use remaining models only.
    """
    rows: dict[tuple[str, str], BuiltinProviderModelState] = {
        (row.provider_id, row.model_id): row for row in await BuiltinProviderModelRepository(session).list_all()
    }
    return {
        provider_id: frozenset(
            view.model_id
            for view in catalog.provider_models(provider_id)
            if view.lifecycle.discontinued or not _enabled(view, rows.get((provider_id, view.model_id)))
        )
        for provider_id in registry.PROVIDER_REGISTRY
    }


def _find_item(items: list[BuiltinModelItem], model_id: str) -> BuiltinModelItem:
    item = next((i for i in items if i.view.model_id == model_id), None)
    if item is None:
        raise BuiltinModelError("provider_model_not_found", not_found=True, model=model_id)
    return item


async def set_builtin_model_enabled(
    session: AsyncSession, provider_id: str, model_id: str, enabled: bool
) -> BuiltinModelItem:
    """Enable/disable provider models immediately."""
    item = _find_item(await list_builtin_models(session, provider_id), model_id)
    await BuiltinProviderModelRepository(session).set_enabled(provider_id, model_id, enabled)
    return BuiltinModelItem(item.view, enabled)


def _media_types_of(views: list[catalog.ProviderModelView]) -> set[str]:
    return {view.model.media_type for view in views if view.official}


@dataclass(frozen=True)
class AddedModel:
    """ID-addition result: added model keys; true ``official`` means matching/enabling official models."""

    model_id: str
    official: bool


async def add_builtin_model(
    session: AsyncSession,
    provider_id: str,
    model_id: str,
    *,
    media_type: str | None = None,
    entry_binding: str | None = None,
    inferred_media_type: str | None = None,
    endpoint: str | None = None,
) -> AddedModel:
    """Add models by ID.

    Match official manifest API names exactly, distinguishing dual-modality models by ``media_type``; enable matches.
    Nonmatches become user additions through entry bindings/name matches (docs/adr/0105). Media uses ``media_type``,
    otherwise matched-entry media, then caller name-inferred ``inferred_media_type``. ``endpoint`` must be an available
    built-in endpoint for provider/media; absent values prefill by name, sharing custom-provider inference.
    """
    _ensure_builtin(provider_id)
    model_id = model_id.strip()
    if not model_id:
        raise BuiltinModelError("builtin_model_id_required")
    if has_dot_segment(model_id):
        raise BuiltinModelError("model_id_dot_segment", model=model_id)
    if media_type is not None and media_type not in MEDIA_TYPES:
        raise BuiltinModelError("builtin_model_media_type_unsupported", media_type=media_type)
    views = catalog.provider_models(provider_id)
    official = [
        v for v in views if v.official and v.api_model_name == model_id and media_type in (None, v.model.media_type)
    ]
    if len(official) > 1:
        raise BuiltinModelError("builtin_model_media_type_required", model=model_id)
    binding = entry_binding.strip() if entry_binding else None
    if not official and binding:
        # Catalog selections provide official model_id, possibly different from manifest API names such as dated Ark names.
        # If providers already list models corresponding to entries, enable them instead of adding uncallable duplicate user models. Multiple
        # aliases can map to one entry (two Ark Seedream 5.0 Lite names); enable first manifest order.
        official = [
            v
            for v in views
            if v.official
            and v.entry is not None
            and v.entry.key.ref == binding
            and media_type in (None, v.model.media_type)
        ][:1]
    repo = BuiltinProviderModelRepository(session)
    if official:
        view = official[0]
        await repo.set_enabled(provider_id, view.model_id, True)
        return AddedModel(view.model_id, official=True)

    existing = next((v for v in views if v.model_id == model_id), None)
    if existing is not None:
        raise BuiltinModelError("builtin_model_duplicate", model=model_id)
    resolved_media = media_type or _entry_media_type(model_id) or inferred_media_type
    if resolved_media is None:
        raise BuiltinModelError("builtin_model_media_type_required", model=model_id)
    if resolved_media not in _media_types_of(views):
        raise BuiltinModelError("builtin_model_media_type_unsupported", media_type=resolved_media)
    if binding and catalog.binding_key(binding, resolved_media) is None:
        raise BuiltinModelError("builtin_model_binding_invalid", binding=binding)
    chosen = _checked_endpoint(provider_id, resolved_media, endpoint, model_id=model_id)

    row = await repo.get(provider_id, model_id)
    if row is None:
        await repo.add_user_model(
            provider_id, model_id, media_type=resolved_media, entry_binding=binding, endpoint=chosen
        )
    else:
        # Rows no longer officially listed, retaining enabled states only, become user additions.
        row.user_added = True
        row.enabled = True
        row.media_type = resolved_media
        row.entry_binding = binding
        row.endpoint = chosen
        await session.flush()
    return AddedModel(model_id, official=False)


def endpoint_options(provider_id: str, media_type: str) -> tuple[str, ...]:
    """Available built-in provider/media endpoints; empty without channels for that media."""
    try:
        return builtin_endpoints.get_provider_endpoints(provider_id, media_type).endpoints
    except ValueError:
        return ()


def _checked_endpoint(provider_id: str, media_type: str, endpoint: str | None, *, model_id: str) -> str:
    """Persisted user-model endpoints: validate supplied options, otherwise prefill by name."""
    if endpoint is None:
        return builtin_endpoints.infer_builtin_endpoint(provider_id, media_type, model_id)
    if endpoint not in endpoint_options(provider_id, media_type):
        raise BuiltinModelError("builtin_model_endpoint_unavailable", endpoint=endpoint, media_type=media_type)
    return endpoint


def _entry_media_type(model_id: str) -> str | None:
    """Matched-entry media; ``None`` for dual-modality/absent matches."""
    entry = catalog.find_entry(model_id)
    return entry.media_type if entry is not None else None


async def remove_builtin_model(session: AsyncSession, provider_id: str, model_id: str) -> None:
    """Remove user additions; official models can only be disabled."""
    item = _find_item(await list_builtin_models(session, provider_id), model_id)
    if item.view.official:
        raise BuiltinModelError("builtin_model_not_removable", model=model_id)
    await BuiltinProviderModelRepository(session).delete(provider_id, model_id)


_UNCHANGED = object()


async def update_builtin_model(
    session: AsyncSession,
    provider_id: str,
    model_id: str,
    *,
    enabled: bool | None = None,
    new_model_id: str | None = None,
    multiplier: float | None = None,
    capability_overrides: object = _UNCHANGED,
    entry_binding: object = _UNCHANGED,
    endpoint: str | None = None,
) -> str:
    """Save provider settings from model Sheets, returning saved model keys.

    Official models permit enable/disable only; other settings remain official. User-added models also permit ID/endpoint changes
    (available provider/media endpoints only), provider-model multipliers, and capability overrides.
    Omitted ``capability_overrides`` leaves unchanged, except endpoint changes clear them; ``None`` clears. Values must already pass
    write validation (``lib.custom_provider.capability_view.check_user_added_overrides``). ``entry_binding`` likewise accepts
    vendor/official-name bindings, ``None`` to unbind and resume name matching; callers validate entry existence.
    """
    item = _find_item(await list_builtin_models(session, provider_id), model_id)
    renamed = new_model_id is not None and new_model_id.strip() != model_id
    if item.view.official:
        if (
            renamed
            or multiplier is not None
            or capability_overrides is not _UNCHANGED
            or entry_binding is not _UNCHANGED
            or endpoint is not None
        ):
            raise BuiltinModelError("builtin_model_official_settings", model=model_id)
        if enabled is not None:
            await BuiltinProviderModelRepository(session).set_enabled(provider_id, model_id, enabled)
        return model_id

    repo = BuiltinProviderModelRepository(session)
    row = await repo.get(provider_id, model_id)
    if row is None:
        raise BuiltinModelError("provider_model_not_found", not_found=True, model=model_id)
    target = model_id
    if renamed and new_model_id is not None:
        target = new_model_id.strip()
        if not target:
            raise BuiltinModelError("builtin_model_id_required")
        if has_dot_segment(target):
            raise BuiltinModelError("model_id_dot_segment", model=target)
        views = catalog.provider_models(provider_id)
        if any(v.model_id == target or (v.official and v.api_model_name == target) for v in views):
            raise BuiltinModelError("builtin_model_duplicate", model=target)
        if await repo.get(provider_id, target) is not None:
            raise BuiltinModelError("builtin_model_duplicate", model=target)
        row.model_id = target
    if endpoint is not None:
        checked = _checked_endpoint(provider_id, row.media_type or "", endpoint, model_id=target)
        # Overrides were validated for endpoints: changing endpoints without submitted replacement overrides clears them, preventing old overrides on new endpoints.
        if capability_overrides is _UNCHANGED and checked != builtin_endpoints.builtin_model_endpoint(
            provider_id, row.media_type or "", model_id
        ):
            capability_overrides = None
        row.endpoint = checked
    if enabled is not None:
        row.enabled = enabled
    if multiplier is not None:
        row.multiplier = multiplier
    if capability_overrides is not _UNCHANGED:
        row.capability_overrides = capability_overrides if isinstance(capability_overrides, dict) else None
    if entry_binding is not _UNCHANGED:
        binding = entry_binding.strip() if isinstance(entry_binding, str) else ""
        if binding and catalog.binding_key(binding, row.media_type or "") is None:
            raise BuiltinModelError("builtin_model_binding_invalid", binding=binding)
        row.entry_binding = binding or None
    await session.flush()
    return target
