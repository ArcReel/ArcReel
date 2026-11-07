"""Catalog queries: sole read entry for model-level information.

Read display names, media types, capabilities, durations/resolutions, maximum output tokens, prices, and provider/media defaults
here. Registries (``lib.config.registry``) contain provider-level code facts only (credential forms/channels,
default hosts/concurrency), never models.

Manifests, entry facts, and defaults come from composed catalogs (``lib.catalog.composition``): official data combines bundled factory snapshots
(``lib/catalog/snapshot/``, validated through ``arcreel_catalog_core`` instance checks) and newer synchronized datasets
(``lib.catalog.official_sync``); whole user copies replace official entries.
Prices are entry price tables, :func:`provider_model_price`.
Compute lifecycle by current dates (:func:`lib.catalog.lifecycle.entry_lifecycle`), never index-cache it: discontinued models remain
listed with ``hidden`` (:func:`list_models`), but cannot default (:func:`default_model`) or be called (:func:`require_callable`).
Official models requiring newer application versions stay outside manifests, exposed only through :func:`version_gated_models` for settings (docs/adr/0103).
Provider-model queries return catalog-owned value objects (:class:`CatalogModel`, :class:`CatalogSearchHit`); name-based
:func:`find_entry` returns ``arcreel_catalog_core`` ``CatalogEntry`` because callers need entry keys/facts/prices themselves.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal, assert_never, cast

from packaging.version import InvalidVersion, Version

from arcreel_catalog_core import (
    CATALOG_FILENAME,
    MANIFEST_FILENAME,
    MEDIA_TYPES,
    AudioFacts,
    CatalogData,
    CatalogEntry,
    EntryKey,
    ImageFacts,
    MediaType,
    PriceTable,
    TextFacts,
    VideoFacts,
    load_catalog,
    normalize_model_name,
)
from lib.app_version import app_version
from lib.catalog.composition import ComposedCatalog, EntrySource, compose_catalog, merge_official
from lib.catalog.entry_matching import match_entry
from lib.catalog.errors import ModelDiscontinuedError, ModelNotInCatalogError
from lib.catalog.lifecycle import AVAILABLE, Lifecycle, entry_lifecycle

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = Path(__file__).parent / "snapshot"

#: Closed capability-token vocabulary derived from entry facts: consumed tokens only, matching media capability enums
#: TextCapability / ImageCapability / AudioCapability in names/semantics. Video input modes/reference maxima/audio forms
#: live outside this vocabulary, sourced from backend VideoCapabilities/request gates.
ModelCapability = Literal[
    "text_generation",
    "structured_output",  # Consumer: text-backend structured-output detection.
    "vision",  # Consumer: text-parsing vision gates, lib/config/resolver.py.
    "text_to_image",  # Consumer: image task buckets, lib/backends/generation_type_buckets.py.
    "image_to_image",  # Consumer: same as above.
    "text_to_speech",
]

#: Legacy keys for dual-modality models: provider-model identity is API name + entry media, assigning
#: separate keys here for two models sharing API names. Others use API names as keys, keeping stored settings/project keys resolvable.
_LEGACY_MODEL_KEYS: Mapping[tuple[str, str, str], str] = {
    ("kling", "kling-v3", "image"): "kling-v3-image",
    ("kling", "kling-v3-omni", "image"): "kling-v3-omni-image",
}


@dataclass(frozen=True)
class CatalogModel:
    """Catalog information for one provider model key: entry facts plus provider-model facts.

    Entry facts: ``display_name``, ``media_type``, ``capabilities``, durations/constraints,
    ``resolutions``, ``max_output_tokens``, ``max_reference_images`` (image/video reference maxima; ``None`` means unlisted), ``hidden`` (discontinued entries, excluded from candidates, recomputed daily).
    Provider facts: ``default`` (provider/media default, false when discontinued), ``api_model_name`` (sent model name, model keys when
    ``None``). Read prices separately through :func:`provider_model_price`.

    Field names/order are settings API serialization shapes (``dataclasses.asdict``).
    """

    display_name: str
    media_type: str
    capabilities: list[ModelCapability]
    default: bool = False
    supported_durations: list[int] = field(default_factory=list)
    duration_resolution_constraints: dict[str, list[int]] = field(default_factory=dict)
    reference_image_durations: list[int] = field(default_factory=list)
    resolutions: list[str] = field(default_factory=list)
    hidden: bool = False
    api_model_name: str | None = None
    max_output_tokens: int | None = None
    max_reference_images: int | None = None


@functools.cache
def factory_snapshot() -> CatalogData:
    """Bundled factory snapshot, read once per process; invalid snapshots raise ``CatalogFormatError``."""
    return load_catalog(
        (SNAPSHOT_DIR / MANIFEST_FILENAME).read_bytes(),
        (SNAPSHOT_DIR / CATALOG_FILENAME).read_bytes(),
    )


# Official synchronized data loaded through install_synced_data; single-element mailbox lists.
_synced: list[CatalogData | None] = [None]

# Official-data cache keyed by factory snapshot/sync objects, rebuilt on synchronized-data replacement.
_official_cache: list[tuple[CatalogData, CatalogData, CatalogData]] = []


def install_synced_data(data: CatalogData | None) -> None:
    """Replace synchronized data; subsequent queries compose it with factory snapshots (:func:`merge_official`).

    Load persisted data at startup and newly applied datasets after sync; whole replacement means readers see either old or new data.
    """
    _synced[0] = data


def official_data() -> CatalogData:
    """Newer official snapshot/sync dataset, retaining entries unique to the other; snapshot alone without sync."""
    snapshot, synced = factory_snapshot(), _synced[0]
    if synced is None:
        return snapshot
    if not _official_cache or _official_cache[0][0] is not snapshot or _official_cache[0][1] is not synced:
        _official_cache[:] = [(snapshot, synced, merge_official(snapshot, synced))]
    return _official_cache[0][2]


def _to_catalog_model(entry: CatalogEntry, api_model_name: str, key: str, default: bool) -> CatalogModel:
    capabilities: list[ModelCapability] = []
    durations: list[int] = []
    by_resolution: dict[str, list[int]] = {}
    reference_durations: list[int] = []
    resolutions: list[str] = []
    max_output_tokens: int | None = None
    max_reference_images: int | None = None
    facts = entry.facts
    match facts:
        case TextFacts():
            capabilities.append("text_generation")
            if facts.structured_output:
                capabilities.append("structured_output")
            if facts.image_input:
                capabilities.append("vision")
            max_output_tokens = facts.max_output_tokens
        case ImageFacts():
            if facts.text_to_image:
                capabilities.append("text_to_image")
            if facts.image_to_image:
                capabilities.append("image_to_image")
            resolutions = list(facts.resolutions)
            max_reference_images = facts.max_reference_images
        case VideoFacts():
            max_reference_images = facts.max_reference_images
            durations = list(facts.durations)
            resolutions = list(facts.resolutions)
            constraints = facts.duration_constraints
            by_resolution = {res: list(allowed) for res, allowed in constraints.by_resolution.items()}
            reference_durations = list(constraints.with_reference_images)
        case AudioFacts():
            capabilities.append("text_to_speech")
        case _:
            assert_never(facts)
    return CatalogModel(
        display_name=entry.display_name,
        media_type=entry.media_type,
        capabilities=capabilities,
        default=default,
        supported_durations=durations,
        duration_resolution_constraints=by_resolution,
        reference_image_durations=reference_durations,
        resolutions=resolutions,
        api_model_name=None if key == api_model_name else api_model_name,
        max_output_tokens=max_output_tokens,
        max_reference_images=max_reference_images,
    )


def _unmatched_catalog_model(user: UserAddedModel) -> CatalogModel:
    """User-added models without matching entries: media known only, model facts use runtime fallback (docs/adr/0104).

    Text/audio have basic capabilities only; images impose no text/image-to-image constraints; video durations use load-time fallback values.
    """
    model_id, media_type = user.model_id, user.media_type
    match media_type:
        case "text":
            return CatalogModel(display_name=model_id, media_type=media_type, capabilities=["text_generation"])
        case "image":
            return CatalogModel(
                display_name=model_id, media_type=media_type, capabilities=["text_to_image", "image_to_image"]
            )
        case "video":
            return CatalogModel(
                display_name=model_id,
                media_type=media_type,
                capabilities=[],
                supported_durations=list(user.fallback_durations),
            )
        case "audio":
            return CatalogModel(display_name=model_id, media_type=media_type, capabilities=["text_to_speech"])
        case _:
            assert_never(media_type)


@dataclass(frozen=True)
class UserAddedModel:
    """User-added built-in-provider models loaded from built-in model tables.

    ``model_id`` is API names and settings reference keys; ``entry_binding`` is vendor/official model name.
    ``fallback_durations`` supplies runtime video choices without entry matches, inferred by name and supplied by loaders.
    ``capability_overrides`` are user overrides (docs/adr/0104), with invalid media-specific values already filtered by loaders: model facts
    replace whole catalog dimensions here; channel facts pass through :func:`user_added_capability_overrides` for channel-side merging.
    ``endpoint`` is user-selected built-in endpoints; ``None`` means unrecorded, so assembly uses provider/media defaults.
    """

    provider_id: str
    model_id: str
    media_type: MediaType
    multiplier: float = 1.0
    entry_binding: str | None = None
    fallback_durations: tuple[int, ...] = ()
    capability_overrides: Mapping[str, object] = field(default_factory=dict)
    endpoint: str | None = None


@dataclass(frozen=True)
class _IndexedModel:
    """Indexed provider models: public catalog data plus corresponding entries (``None`` without matches) and provider-model multipliers.

    ``base_model`` is pre-override catalog data, from entries/runtime fallback; only user additions have overrides, others equal ``model``.
    """

    model: CatalogModel
    entry: CatalogEntry | None
    multiplier: float
    api_model_name: str
    official: bool
    base_model: CatalogModel | None = None
    capability_overrides: Mapping[str, object] = field(default_factory=dict)
    endpoint: str | None = None
    min_app_version: str | None = None


@dataclass(frozen=True)
class _ProviderIndex:
    """Provider indexes: ``models`` is manifests for candidates/defaults/calls, official first, user-added last;
    ``version_gated`` holds minimum-app-version-incompatible official models in manifest order, for settings display only."""

    models: dict[str, _IndexedModel]
    version_gated: dict[str, _IndexedModel]


def meets_min_app_version(required: str | None, current: str) -> bool:
    """Whether application ``current`` meets provider-model minimum versions; absent requirements always pass.

    Uncomparable versions on either side fail conservatively, preventing old instances from calling models their channels cannot encode.
    """
    if required is None:
        return True
    try:
        return Version(current) >= Version(required)
    except InvalidVersion:
        return False


def _official_models(data: CatalogData, provider_id: str, current_version: str) -> _ProviderIndex:
    listing = data.builtin_providers[provider_id]
    models: dict[str, _IndexedModel] = {}
    version_gated: dict[str, _IndexedModel] = {}
    for model in listing.models:
        entry = data.entry_of(model)
        if entry is None:
            logger.warning(
                "内置供应商 %s 的模型 %s 引用不到条目 %s，已跳过", provider_id, model.api_model_name, model.entry
            )
            continue
        key = _LEGACY_MODEL_KEYS.get((provider_id, model.api_model_name, entry.media_type), model.api_model_name)
        if key in models or key in version_gated:
            logger.warning("内置供应商 %s 的模型键 %s 重复，已跳过后一个", provider_id, key)
            continue
        # Below-minimum-app versions cannot enable models, settings-only; designated defaults become unspecified (docs/adr/0103).
        callable_here = meets_min_app_version(model.min_app_version, current_version)
        default = callable_here and listing.defaults.get(entry.media_type) == model.api_model_name
        (models if callable_here else version_gated)[key] = _IndexedModel(
            _to_catalog_model(entry, model.api_model_name, key, default),
            entry,
            model.multiplier,
            model.api_model_name,
            official=True,
            endpoint=model.endpoint,
            min_app_version=model.min_app_version,
        )
    return _ProviderIndex(models, version_gated)


def _toggle(capabilities: list[ModelCapability], capability: ModelCapability, value: object) -> list[ModelCapability]:
    if not isinstance(value, bool):
        return capabilities
    kept: list[ModelCapability] = [c for c in capabilities if c != capability]
    if value:
        kept.append(capability)
    return kept


def _with_model_fact_overrides(model: CatalogModel, overrides: Mapping[str, object]) -> CatalogModel:
    """Replace whole model-fact dimensions from overrides into catalog information (docs/adr/0104); loaders already validate shapes."""
    capabilities = list(model.capabilities)
    capabilities = _toggle(capabilities, "vision", overrides.get("image_input"))
    capabilities = _toggle(capabilities, "structured_output", overrides.get("structured_output"))
    capabilities = _toggle(capabilities, "text_to_image", overrides.get("text_to_image"))
    capabilities = _toggle(capabilities, "image_to_image", overrides.get("image_to_image"))
    max_output_tokens = overrides.get("max_output_tokens")
    durations = overrides.get("durations")
    resolutions = overrides.get("resolutions")
    constraints = overrides.get("duration_constraints")
    by_resolution = model.duration_resolution_constraints
    reference_durations = model.reference_image_durations
    if isinstance(constraints, Mapping):
        raw_by_resolution = constraints.get("by_resolution")
        raw_reference = constraints.get("with_reference_images")
        by_resolution = (
            {str(res).strip().lower(): [int(d) for d in allowed] for res, allowed in raw_by_resolution.items()}
            if isinstance(raw_by_resolution, Mapping)
            else {}
        )
        reference_durations = [int(d) for d in raw_reference] if isinstance(raw_reference, list) else []
    return replace(
        model,
        capabilities=capabilities,
        max_output_tokens=max_output_tokens if isinstance(max_output_tokens, int) else model.max_output_tokens,
        supported_durations=(
            sorted({int(d) for d in durations}) if isinstance(durations, list) else model.supported_durations
        ),
        resolutions=[str(r).strip() for r in resolutions] if isinstance(resolutions, list) else model.resolutions,
        duration_resolution_constraints=by_resolution,
        reference_image_durations=reference_durations,
    )


def _user_added_model(data: CatalogData, user: UserAddedModel) -> _IndexedModel:
    binding = binding_key(user.entry_binding, user.media_type)
    entry = match_entry(data.entries, user.model_id, media_type=user.media_type, binding=binding)
    base = (
        _to_catalog_model(entry, user.model_id, user.model_id, default=False)
        if entry is not None
        else _unmatched_catalog_model(user)
    )
    model = _with_model_fact_overrides(base, user.capability_overrides) if user.capability_overrides else base
    return _IndexedModel(
        model,
        entry,
        user.multiplier,
        user.model_id,
        official=False,
        base_model=base,
        capability_overrides=user.capability_overrides,
        endpoint=user.endpoint,
    )


def _build_index(
    data: CatalogData, user_models: tuple[UserAddedModel, ...], current_version: str
) -> dict[str, _ProviderIndex]:
    index = {
        provider_id: _official_models(data, provider_id, current_version) for provider_id in data.builtin_providers
    }
    for user in user_models:
        provider = index.setdefault(user.provider_id, _ProviderIndex({}, {}))
        # Official same-key models replace user additions, retaining enabled states (docs/adr/0099). When official versions need newer apps,
        # retain user-added models and show only them in settings.
        if user.model_id not in provider.models:
            provider.models[user.model_id] = _user_added_model(data, user)
            provider.version_gated.pop(user.model_id, None)
    return index


# User-added built-in models loaded from built-in tables through install_user_added_models; single-element mailboxes.
_user_added: list[tuple[UserAddedModel, ...]] = [()]

# Indexes cached per snapshot object, loaded/built once per process; user additions clear/rebuild indexes.
_index_cache: list[tuple[CatalogData, dict[str, _ProviderIndex]]] = []

# User-entry copies loaded through install_user_entries; single-element mailboxes.
_user_entries: list[tuple[tuple[CatalogEntry, ...], frozenset[EntryKey]]] = [((), frozenset())]

# Composition cached per official-data object; user-copy changes clear/rebuild it.
_composed_cache: list[tuple[CatalogData, ComposedCatalog]] = []


def install_user_added_models(models: Iterable[UserAddedModel]) -> None:
    """Replace user-added built-in models, merged into provider manifests for subsequent queries.

    Writers call at startup/after built-in-model-table writes. All catalog readers (generation workers, Agent session tools,
    APIs) run in application processes, so process replacement immediately affects all readers.
    """
    _user_added[0] = tuple(models)
    _index_cache.clear()


def install_user_entries(entries: Iterable[CatalogEntry], created: Iterable[EntryKey] = ()) -> None:
    """Replace user-entry copies; subsequent facade/capability/pricing/name-match/search queries use composed entries.

    Writers call at startup/after user-table writes; like :func:`install_user_added_models`, all readers run in
    application processes, so replacement immediately affects them. ``created`` holds user-created identities.
    """
    _user_entries[0] = (tuple(entries), frozenset(created))
    _composed_cache.clear()
    _index_cache.clear()


def composed_catalog() -> ComposedCatalog:
    """Composed catalogs: official data plus user-entry copies."""
    official = official_data()
    if not _composed_cache or _composed_cache[0][0] is not official:
        _composed_cache[:] = [(official, compose_catalog(official, *_user_entries[0]))]
    return _composed_cache[0][1]


def catalog_data() -> CatalogData:
    """Runtime catalog data after composition."""
    return composed_catalog().data


def entry_source(key: EntryKey) -> EntrySource:
    """Entry ownership: official following, user-modified, or user-entered without official entries."""
    return composed_catalog().source_of(key)


def official_entry(key: EntryKey) -> CatalogEntry | None:
    """Official entries by identity, for displaying/restoring official values; user-created entries lack them."""
    return composed_catalog().official_entry(key)


def get_entry(key: EntryKey) -> CatalogEntry | None:
    """Composed entries by identity; ``None`` if absent."""
    return next((entry for entry in catalog_data().entries if entry.key == key), None)


def _provider_index(provider_id: str) -> _ProviderIndex | None:
    data = catalog_data()
    if not _index_cache or _index_cache[0][0] is not data:
        _index_cache[:] = [(data, _build_index(data, _user_added[0], app_version()))]
    return _index_cache[0][1].get(provider_id)


def _indexed_models(provider_id: str) -> dict[str, _IndexedModel]:
    provider = _provider_index(provider_id)
    return provider.models if provider is not None else {}


def _current_model(indexed: _IndexedModel) -> CatalogModel:
    """Today's catalog information: discontinued entries become ``hidden`` without defaults; indexes cache date-independent portions only."""
    if not entry_lifecycle(indexed.entry).discontinued:
        return indexed.model
    return replace(indexed.model, hidden=True, default=False)


def _provider_models(provider_id: str) -> dict[str, CatalogModel]:
    return {key: _current_model(indexed) for key, indexed in _indexed_models(provider_id).items()}


def reset_for_tests() -> None:
    """Clear process snapshot/sync/index/user-model/user-entry state; next queries reread snapshots."""
    factory_snapshot.cache_clear()
    _synced[0] = None
    _official_cache.clear()
    _user_added[0] = ()
    _user_entries[0] = ((), frozenset())
    _composed_cache.clear()
    _index_cache.clear()


def _indexed_model(provider_id: str, model_id: str, media_type: str | None) -> _IndexedModel | None:
    """Index items by provider/key; supplied ``media_type`` treats mismatched model types as absent."""
    indexed = _indexed_models(provider_id).get(model_id)
    if indexed is None or media_type not in (None, indexed.model.media_type):
        return None
    return indexed


def get_model(provider_id: str, model_id: str, *, media_type: str | None = None) -> CatalogModel | None:
    """Catalog information by provider/key; ``None`` for absent providers/models.

    Supplied ``media_type`` also returns ``None`` for other media: callers request this provider's models of this type;
    wrong-media key references are as unusable as unlisted ones.
    """
    indexed = _indexed_model(provider_id, model_id, media_type)
    return _current_model(indexed) if indexed is not None else None


def _discontinued_error(provider_id: str, model_id: str, media_type: str, state: Lifecycle) -> ModelDiscontinuedError:
    return ModelDiscontinuedError(
        provider_id=provider_id,
        model_id=model_id,
        media_type=media_type,
        shutdown_date=state.shutdown_date.isoformat() if state.shutdown_date is not None else None,
        successor=state.successor,
    )


def require_callable(provider_id: str, model_id: str, media_type: str) -> CatalogModel:
    """Before built-in calls, require provider/media manifest membership and nondiscontinued lifecycle, returning catalog information.

    Discontinued raises :class:`ModelDiscontinuedError` with shutdown dates/successors. Unlisted names match retained local entries
    (last versions retained after upstream deletion); discontinued matches likewise raise :class:`ModelDiscontinuedError`.
    Others raise :class:`ModelNotInCatalogError` for user reselection, never substituting models.
    """
    reject_discontinued(provider_id, model_id, media_type)
    return require_listed(provider_id, model_id, media_type)


def require_listed(provider_id: str, model_id: str, media_type: str) -> CatalogModel:
    """Return catalog information for listed provider/media models, else :class:`ModelNotInCatalogError`; ignore lifecycle.

    For resuming already-submitted vendor tasks: poll existing tasks without new calls, retrieving results even after discontinuation. New calls always use
    :func:`require_callable`.
    """
    indexed = _indexed_model(provider_id, model_id, media_type)
    if indexed is None:
        raise ModelNotInCatalogError(provider_id=provider_id, model_id=model_id, media_type=media_type)
    return indexed.model


def reject_discontinued(provider_id: str, model_id: str, media_type: str) -> None:
    """Raise :class:`ModelDiscontinuedError` for models discontinued today, without checking listing.

    Listed models use referenced entries, with user additions using bindings/name matches; unlisted models name-match retained entries.
    Reuse callers invoke before cache lookups; complete call checks use :func:`require_callable`.
    """
    reject_discontinued_entry(provider_id, model_id, media_type, referenced_entry(provider_id, model_id, media_type))


def referenced_entry(provider_id: str, model_id: str, media_type: str) -> CatalogEntry | None:
    """Entries for built-in references in settings, used for lifecycle checks.

    Listed models use referenced entries; unlisted names match retained local entries (last versions preserved after official deletion).
    ``None`` without matches.
    """
    indexed = _indexed_model(provider_id, model_id, media_type)
    return indexed.entry if indexed is not None else find_entry(model_id, media_type=media_type)


def reject_discontinued_entry(provider_id: str, model_id: str, media_type: str, entry: CatalogEntry | None) -> None:
    """Raise :class:`ModelDiscontinuedError` with shutdown dates/successors for provider-model entries discontinued today.

    Callers resolve entries: built-ins through :func:`reject_discontinued`, customs through bindings/name matches
    (``lib.custom_provider.model_facts.model_entry``). ``entry`` = ``None`` has no lifecycle information and is not blocked.
    """
    state = entry_lifecycle(entry)
    if state.discontinued:
        raise _discontinued_error(provider_id, model_id, media_type, state)


def provider_model_entry(provider_id: str, model_id: str, media_type: str) -> CatalogEntry | None:
    """Corresponding built-in entries: official models use manifest references, user-added models bindings/current name lookup.

    ``None`` for missing provider/key, wrong media, or unmatched user-added entries.
    """
    indexed = _indexed_model(provider_id, model_id, media_type)
    return indexed.entry if indexed is not None else None


def list_models(provider_id: str) -> dict[str, CatalogModel]:
    """All provider models in manifest order, including ``hidden``; empty for uncataloged providers."""
    return _provider_models(provider_id)


@dataclass(frozen=True)
class ProviderModelView:
    """Provider-list model: catalog information, matching entries (``None`` if absent), and official/user-added ownership."""

    model_id: str
    api_model_name: str
    model: CatalogModel
    entry: CatalogEntry | None
    official: bool
    #: Provider-model multipliers: official data supplies official models; users supply additions.
    multiplier: float
    #: Pre-override catalog information: entry values, or runtime fallbacks without matches.
    base_model: CatalogModel | None = None
    #: User-added overrides, already filtered for valid values; official models lack them.
    capability_overrides: Mapping[str, object] = field(default_factory=dict)
    #: Corresponding entry lifecycle today; available when unmatched.
    lifecycle: Lifecycle = AVAILABLE
    #: User-added saved built-in endpoints; ``None`` for official/unsaved models.
    endpoint: str | None = None
    #: Official minimum application versions; ``None`` if absent/user-added.
    min_app_version: str | None = None
    #: Whether application meets ``min_app_version``; failing models appear only in :func:`version_gated_models`.
    min_app_version_satisfied: bool = True


def _view(key: str, indexed: _IndexedModel, *, min_app_version_satisfied: bool = True) -> ProviderModelView:
    return ProviderModelView(
        key,
        indexed.api_model_name,
        _current_model(indexed),
        indexed.entry,
        indexed.official,
        indexed.multiplier,
        base_model=indexed.base_model or indexed.model,
        capability_overrides=indexed.capability_overrides,
        lifecycle=entry_lifecycle(indexed.entry),
        endpoint=indexed.endpoint if not indexed.official else None,
        min_app_version=indexed.min_app_version,
        min_app_version_satisfied=min_app_version_satisfied,
    )


def provider_models(provider_id: str) -> list[ProviderModelView]:
    """All built-in provider models: official manifest order first, user additions afterward.

    Minimum-version-incompatible official models are excluded (:func:`version_gated_models`). Enabled states belong to built-in
    model tables, outside catalogs. Discontinued models remain included, with ``lifecycle`` statuses.
    """
    return [_view(key, indexed) for key, indexed in _indexed_models(provider_id).items()]


def version_gated_models(provider_id: str) -> list[ProviderModelView]:
    """Official built-in models requiring newer application versions, in manifest order (docs/adr/0103).

    Settings-only lists expose required versions; excluded from :func:`provider_models` / :func:`list_models`, so never candidates,
    defaults, callable, or enableable. User additions with matching keys appear as user-added instead.
    """
    provider = _provider_index(provider_id)
    if provider is None:
        return []
    return [_view(key, indexed, min_app_version_satisfied=False) for key, indexed in provider.version_gated.items()]


def user_added_capability_overrides(provider_id: str, model_id: str) -> Mapping[str, object]:
    """User-added built-in model overrides; empty for official/uncataloged models.

    Model facts already merge into :func:`get_model`; channel dimensions (input modes/reference limits/audio forms) replace whole
    dimensions after joint channel/entry evaluation (``lib.backends.backend_assembly.specs.builtin_video_capabilities_for_model``).
    """
    indexed = _indexed_models(provider_id).get(model_id)
    return indexed.capability_overrides if indexed is not None else {}


def provider_model_endpoint(provider_id: str, model_id: str, media_type: str) -> str | None:
    """Declared/user-selected built-in endpoint; ``None`` when undeclared, uncataloged, or wrong media."""
    indexed = _indexed_model(provider_id, model_id, media_type)
    return indexed.endpoint if indexed is not None else None


def default_model(provider_id: str, media_type: str) -> str | None:
    """Provider/media default keys; ``None`` for unspecified/discontinued defaults, treated as no defaults."""
    for model_id, model in _provider_models(provider_id).items():
        if model.media_type == media_type and model.default:
            return model_id
    return None


def provider_media_types(provider_id: str) -> list[str]:
    """Media types covered by provider models, ascending."""
    return sorted({model.media_type for model in _provider_models(provider_id).values()})


def provider_capabilities(provider_id: str) -> list[str]:
    """Capability union across provider models, ascending."""
    return sorted({cap for model in _provider_models(provider_id).values() for cap in model.capabilities})


def find_entry(
    model_name: str, *, media_type: str | None = None, binding: EntryKey | None = None
) -> CatalogEntry | None:
    """Name-matched entries (docs/adr/0105): prefer bindings, otherwise query current catalogs by name.

    For name-only contexts such as custom models/Agent credential tiers; built-in models directly reference entries, bypassing this.
    ``media_type`` comes from provider-model endpoints, otherwise name inference; ``binding`` is user-selected
    entries, ``None`` without storage. Return ``None`` for unmatched names or dual-modality names without ``media_type``.
    Never cache mappings: catalog changes such as official new entries/aliases affect the next call. Rules: :func:`match_entry`.
    """
    return match_entry(catalog_data().entries, model_name, media_type=media_type, binding=binding)


def binding_key(ref: str | None, media_type: str) -> EntryKey | None:
    """Restore entry keys from stored vendor/official-model-name bindings plus endpoint media; ``None`` for absent/malformed bindings."""
    if not ref or media_type not in MEDIA_TYPES:
        return None
    maker, sep, name = ref.partition("/")
    if not sep or not maker or not name:
        return None
    return EntryKey(maker, name, cast("MediaType", media_type))


def find_bound_entry(ref: str, media_type: str) -> CatalogEntry | None:
    """Bound entries; ``None`` for absent/wrong-media entries. Used for write-side binding validation."""
    key = binding_key(ref, media_type)
    if key is None:
        return None
    return get_entry(key)


def entry_ref(entry: CatalogEntry) -> str:
    """Stored binding shape: vendor/official model name."""
    return entry.key.ref


@dataclass(frozen=True)
class ModelPrice:
    """Built-in model pricing: corresponding entry tables (``None`` if absent) plus provider-model multipliers.

    False ``entry_found`` means unmatched user-added models.
    """

    price: PriceTable | None
    multiplier: float
    entry_found: bool = True


def provider_model_price(provider_id: str, model_id: str, media_type: str) -> ModelPrice | None:
    """Built-in entry price tables/provider-model multipliers.

    ``None`` for uncataloged providers/keys or wrong ``media_type``, matching :func:`get_model`,
    treated as unmatched entries.
    """
    indexed = _indexed_model(provider_id, model_id, media_type)
    if indexed is None:
        return None
    entry = indexed.entry
    return ModelPrice(
        price=entry.price if entry is not None else None, multiplier=indexed.multiplier, entry_found=entry is not None
    )


@dataclass(frozen=True)
class CatalogSearchHit:
    """One search-hit entry. ``maker`` + ``name`` + ``media_type`` uniquely identify entries (dual-modality models have two)."""

    maker: str
    name: str
    media_type: str
    display_name: str
    discontinued: bool


def _search_keys(entry: CatalogEntry) -> list[str]:
    """Searchable names: normalized official names/aliases; display names only ignore case and equate dots/hyphens."""
    names = [entry.name, *entry.aliases]
    return [normalize_model_name(name) for name in names] + [entry.display_name.lower().replace(".", "-")]


def search_entries(query: str = "", media_type: str | None = None) -> list[CatalogSearchHit]:
    """Search entries by name/media.

    Split search terms by whitespace; every term must match official names, aliases, or display names, case-insensitively,
    equating dots/hyphens and ignoring makers in vendor/model forms. Empty searches list all. Exact full-phrase names rank first,
    then full-phrase prefixes, then catalog order; discontinued entries, computed by current dates, always rank last.
    """
    tokens = [normalize_model_name(token) for token in query.split()]
    whole = normalize_model_name(query.strip())
    ranked: list[tuple[bool, int, int, CatalogSearchHit]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, entry in enumerate(catalog_data().entries):
        identity = (entry.maker, entry.name, entry.media_type)
        if identity in seen or (media_type is not None and entry.media_type != media_type):
            continue
        keys = _search_keys(entry)
        if not all(any(token in key for key in keys) for token in tokens):
            continue
        seen.add(identity)
        rank = 0 if whole in keys else 1 if any(key.startswith(whole) for key in keys) else 2
        discontinued = entry_lifecycle(entry).discontinued
        hit = CatalogSearchHit(entry.maker, entry.name, entry.media_type, entry.display_name, discontinued)
        ranked.append((discontinued, rank, index, hit))
    ranked.sort(key=lambda item: item[:3])
    return [hit for *_, hit in ranked]
