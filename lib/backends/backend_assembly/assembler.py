"""assemble_backend: unified provider config + model → backend construction entry.

One is_custom_provider branch routes built-in/custom families (one gate, two symmetric rooms, ADR 0039).
Split async loading (DB/config → LoadedConfig envelopes) from sync closure construction. Stateless pure construction;
backend instance caches stay in caller orchestration, outside this seam.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lib.backends.backend_assembly.loaded_config import LoadedConfig
from lib.backends.backend_assembly.specs import PROVIDER_ENDPOINT_SPECS, builtin_model_endpoint, get_provider_endpoints
from lib.catalog import builtin_models
from lib.catalog import query as catalog
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.registry import PROVIDER_REGISTRY
from lib.custom_provider import is_custom_provider

if TYPE_CHECKING:
    from lib.config.resolver import ConfigResolver


async def _load_builtin_config(resolver: ConfigResolver, provider_id: str, rate_limiter: Any | None) -> LoadedConfig:
    """Built-in async loading: query DB/config into LoadedConfig envelopes.

    db_config (resolver.provider_config) supplies credential overlays; registry meta supplies default_base_url /
    api_model_name; callers inject shared rate_limiter. This is the sole await/DB touchpoint;
    subsequent sync closures read envelopes only.
    """
    db_config, credential_id = await resolver.provider_config_with_credential(provider_id)
    return LoadedConfig(
        credentials=dict(db_config),
        provider_meta=PROVIDER_REGISTRY.get(provider_id),
        rate_limiter=rate_limiter,
        credential_id=credential_id,
    )


async def assemble_backend(
    *,
    provider_id: str,
    media_type: str,
    model_id: str | None,
    resolver: ConfigResolver,
    rate_limiter: Any | None = None,
    generation_type: str | None = None,
    submitted_job: bool = False,
    builtin_endpoint: str | None = None,
) -> Any:
    """Unified construction, branching on custom provider_id; fail loudly for unregistered built-in provider × media.

    ``generation_type`` is the call site's task-type bucket, consumed only by custom providers (default model slots per bucket,
    ``lib.custom_provider.loader.load_custom_backend``). Registry defines built-in models without bucketing.

    True ``submitted_job`` means constructed backends only resume submitted vendor tasks (polling, never new generation):
    construct even discontinued models, preserving all other checks. Always false for new calls.

    ``builtin_endpoint`` is caller-selected: built-ins construct through it, aligning instances with resume guards frozen from
    the same read. None resolves current model-attached endpoints through :func:`builtin_model_endpoint`. Custom providers ignore it.
    """
    if is_custom_provider(provider_id):
        from lib.custom_provider.loader import load_custom_backend

        # Borrow resolver sessions, reusing bound transaction contexts. Custom/simple-family loading share
        # session_factory; construction inside resolver.session() reuses the connection instead of opening another factory.
        async with resolver._open_session() as (session, _):
            return await load_custom_backend(
                session=session,
                provider_id=provider_id,
                model_id=model_id,
                media_type=media_type,
                generation_type=generation_type,
                submitted_job=submitted_job,
            )
    get_provider_endpoints(provider_id, media_type)  # Unregistered → ValueError, fail loudly.
    model_id = await _callable_builtin_model(provider_id, media_type, model_id, resolver, submitted_job=submitted_job)
    # Resolve provider-attached built-in endpoints first, then construct through them: providers supply credentials, endpoints determine requests.
    endpoint = builtin_endpoint or builtin_model_endpoint(provider_id, media_type, model_id)
    spec = PROVIDER_ENDPOINT_SPECS[(provider_id, endpoint)]
    config = await _load_builtin_config(resolver, provider_id, rate_limiter)
    # Closures may read credential files (Vertex service-account JSON), so run in threads without blocking loops. Worker threads have no event loop;
    # constructors cannot create SDK clients requiring one (xai_sdk.AsyncClient uses grok_shared.LazyGrokClient).
    backend = await asyncio.to_thread(spec.build_backend, config, model_id)
    if media_type == "video":
        backend = _with_model_entry(backend, provider_id, model_id)
    return backend


async def callable_model_key(
    *,
    provider_id: str,
    media_type: str,
    model_id: str | None,
    resolver: ConfigResolver,
    generation_type: str | None = None,
    submitted_job: bool = False,
) -> str | None:
    """Before backend reuse, verify model callability against today's lifecycle; return model keys for instance caches.

    Cached instances may predate discontinuation and bypass :func:`assemble_backend` checks; callers invoke this before cache lookup.

    - Built-ins: ``model_id`` = ``None`` selects available defaults (excluding discontinued/user-disabled models; none raises ``ModelNotInCatalogError``, matching assembly),
      returning resolved keys; discontinued raises ``ModelDiscontinuedError``. Deliberately skip listing checks here; full checks occur on actual construction.
    - Custom providers: select model rows using loading rules (``lib.custom_provider.loader.select_custom_model``). Explicit discontinued references
      or fallback without nondiscontinued defaults also error; return ``model_id`` unchanged (bucket-specific default slots have separate bucket keys).
    - Unregistered providers return unchanged, leaving assembly to error.

    True ``submitted_job`` (resuming submitted tasks, :func:`assemble_backend`) permits discontinued models, retaining other rules.
    Resumed instances cached for discontinued models remain blocked for later new calls lacking this declaration before cache lookup.
    """
    if is_custom_provider(provider_id):
        from lib.custom_provider.loader import select_custom_model

        async with resolver._open_session() as (session, _):
            await select_custom_model(
                session=session,
                provider_id=provider_id,
                model_id=model_id,
                media_type=media_type,
                generation_type=generation_type,
                submitted_job=submitted_job,
            )
        return model_id
    if provider_id not in PROVIDER_REGISTRY:
        return model_id
    model_id = await _builtin_model_or_default(provider_id, media_type, model_id, resolver)
    if not submitted_job:
        catalog.reject_discontinued(provider_id, model_id, media_type)
    return model_id


async def _builtin_model_or_default(
    provider_id: str, media_type: str, model_id: str | None, resolver: ConfigResolver
) -> str:
    """For ``model_id`` = ``None``, select available built-in defaults by media type (:func:`lib.catalog.builtin_models.default_builtin_model`).

    Defaults come exclusively from catalogs: absent/unavailable defaults (unspecified, discontinued, user-disabled, or below minimum application versions)
    raise ``ModelNotInCatalogError``, never fall through to hardcoded backend constructor model names.
    """
    if model_id is not None:
        return model_id
    async with resolver._open_session() as (session, _):
        default = await builtin_models.default_builtin_model(session, provider_id, media_type)
    if default is None:
        raise ModelNotInCatalogError(provider_id=provider_id, model_id="", media_type=media_type)
    return default


async def _callable_builtin_model(
    provider_id: str, media_type: str, model_id: str | None, resolver: ConfigResolver, *, submitted_job: bool = False
) -> str:
    """Actual built-in model key checked callable against today's lifecycle.

    ``model_id`` = ``None`` selects available defaults, erroring if absent (:func:`_builtin_model_or_default`).
    Discontinued raises ``ModelDiscontinuedError``; unlisted raises ``ModelNotInCatalogError``
    (:func:`lib.catalog.query.require_callable`). Lifecycle uses current dates; reuse callers separately check before cache lookup
    (``server.services.tasks.generation_context``). True ``submitted_job`` (resume) checks listing only
    (:func:`lib.catalog.query.require_listed`).
    """
    model_id = await _builtin_model_or_default(provider_id, media_type, model_id, resolver)
    if submitted_job:
        catalog.require_listed(provider_id, model_id, media_type)
    else:
        catalog.require_callable(provider_id, model_id, media_type)
    return model_id


def _with_model_entry(backend: Any, provider_id: str, model_id: str) -> Any:
    """Bind video-channel request shapes to corresponding entry names, using the same entry as read-side ``builtin_video_capabilities_for_model``.

    Official models use manifest-referenced entries; user-added models use bindings/current name lookup (docs/adr/0104, 0105). Input capabilities
    are not determined here: execution gates jointly evaluate channel declarations and the same entries. Channels not reading entries, such as model-independent declarative definitions, return unchanged.
    """
    rebind = getattr(backend, "with_catalog_entry", None)
    if not callable(rebind):
        return backend
    return rebind(catalog.provider_model_entry(provider_id, model_id, "video"))


@dataclass(frozen=True)
class OutputLimitFacts:
    """Model facts for request output limits and truncation remedies."""

    #: Registered maximum output tokens; None if unregistered.
    registered_max_output_tokens: int | None
    #: Whether models come from custom providers.
    custom_model: bool


async def output_limit_facts(*, provider_id: str, model_id: str, resolver: ConfigResolver) -> OutputLimitFacts:
    """Read registered maximum model output lengths.

    Share :func:`assemble_backend` routing: built-ins read registries, custom models read model entries.
    """
    if is_custom_provider(provider_id):
        from lib.custom_provider.loader import custom_model_max_output_tokens

        async with resolver._open_session() as (session, _):
            registered = await custom_model_max_output_tokens(
                session=session, provider_id=provider_id, model_id=model_id
            )
        return OutputLimitFacts(registered_max_output_tokens=registered, custom_model=True)
    info = catalog.get_model(provider_id, model_id)
    return OutputLimitFacts(
        registered_max_output_tokens=info.max_output_tokens if info is not None else None, custom_model=False
    )
